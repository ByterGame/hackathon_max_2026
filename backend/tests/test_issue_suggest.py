import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from src.domain.issues.service import IssueError
from src.domain.issues.suggest import (
    Candidate,
    _attach_candidate_descriptions,
    _load_candidates,
    _provider_prompt,
    _select_visible_candidates,
    _validate_provider_payload,
    local_similar_ids,
    suggest_issue,
)


def card(number: int, **overrides: object) -> SimpleNamespace:
    values = dict(
        id=UUID(int=number),
        author_user_id=UUID(int=99),
        category_id=UUID(int=50),
        title="Не работает лифт",
        status="open",
        merged_into_id=None,
        scope_all_house=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class IssueSuggestionRulesTests(unittest.TestCase):
    def test_local_match_uses_only_matching_titles(self) -> None:
        candidates = [
            Candidate(UUID(int=1), "Протекает крыша"),
            Candidate(UUID(int=2), "Не работает лифт"),
        ]
        self.assertEqual(
            local_similar_ids("Не работает лифт в подъезде", candidates),
            [UUID(int=2)],
        )

    def test_only_visible_active_cards_can_be_candidates(self) -> None:
        my_apartment = UUID(int=10)
        cards = [
            card(1, scope_all_house=True),
            card(2),
            card(3),
            card(4, scope_all_house=True, status="closed"),
            card(5, scope_all_house=True, merged_into_id=UUID(int=1)),
            card(6, author_user_id=UUID(int=7)),
        ]
        targets = [
            SimpleNamespace(card_id=UUID(int=2), apartment_id=None, entrance_number=3),
            SimpleNamespace(
                card_id=UUID(int=3), apartment_id=my_apartment, entrance_number=None
            ),
        ]
        result = _select_visible_candidates(
            cards=cards,
            targets=targets,
            apartment_ids={my_apartment},
            entrance_numbers={2},
            description="Лифт сломан",
            category_id=None,
        )
        self.assertEqual(
            {item.id for item in result}, {UUID(int=1), UUID(int=3)}
        )

    def test_recent_semantic_candidate_survives_lexical_ranking(self) -> None:
        cards = [
            card(number, scope_all_house=True, title="Вода в техническом помещении")
            for number in range(1, 41)
        ] + [
            card(number, scope_all_house=True, title="Сломался лифт")
            for number in range(41, 61)
        ]
        result = _select_visible_candidates(
            cards=cards,
            targets=[],
            apartment_ids=set(),
            entrance_numbers=set(),
            description="Сломался лифт",
            category_id=None,
        )
        self.assertEqual(len(result), 40)
        self.assertIn(UUID(int=15), {candidate.id for candidate in result})
        self.assertIn(UUID(int=50), {candidate.id for candidate in result})
        self.assertNotIn(UUID(int=30), {candidate.id for candidate in result})

    def test_provider_ids_must_all_be_allowed_and_unique(self) -> None:
        allowed = {UUID(int=1), UUID(int=2)}
        valid = {
            "title": "Сломан лифт",
            "similar_card_ids": [str(UUID(int=1))],
            "description_check": "ok",
            "description_warning": None,
            "summary_description": "Лифт в доме не работает.",
        }
        self.assertEqual(
            _validate_provider_payload(valid, allowed),
            ("Сломан лифт", [UUID(int=1)], "ok", None, "Лифт в доме не работает."),
        )
        self.assertIsNone(
            _validate_provider_payload(
                {"title": "Сломан лифт", "similar_card_ids": [str(UUID(int=3))]},
                allowed,
            )
        )
        self.assertIsNone(
            _validate_provider_payload(
                {"title": "Сломан лифт", "similar_card_ids": [str(UUID(int=1))] * 2},
                allowed,
            )
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"action": "merge"}, allowed)
        )

    def test_provider_warning_requires_a_short_explanation(self) -> None:
        valid = {
            "title": "Нужны подробности",
            "similar_card_ids": [],
            "description_check": "warning",
            "description_warning": "Что именно не работает и где?",
            "summary_description": "Требуется уточнить проблему с лифтом.",
        }
        self.assertEqual(
            _validate_provider_payload(valid, set()),
            (
                "Нужны подробности",
                [],
                "warning",
                "Что именно не работает и где?",
                "Требуется уточнить проблему с лифтом.",
            ),
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"description_warning": None}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"description_warning": " "}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(
                valid | {"description_warning": "x" * 301}, set()
            )
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"description_check": "ok"}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"description_check": []}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"summary_description": " "}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"summary_description": "п"}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"title": "п"}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"summary_description": "Протечка"}, set())
        )
        self.assertIsNotNone(
            _validate_provider_payload(valid | {"summary_description": "Нет света."}, set())
        )
        self.assertIsNone(
            _validate_provider_payload(
                valid | {"summary_description": "x" * 501}, set()
            )
        )
        self.assertIsNone(
            _validate_provider_payload(valid | {"summary_description": None}, set())
        )

    def test_provider_prompt_limits_candidate_details_and_requires_summary(
        self,
    ) -> None:
        candidate_id = UUID(int=7)
        messages, schema = _provider_prompt(
            category_name="Лифты",
            description="Лифт не реагирует на вызов",
            candidates=[Candidate(candidate_id, "Н" * 120, "О" * 200)],
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["category"], "Лифты")
        self.assertEqual(
            payload["candidates"],
            [
                {
                    "id": str(candidate_id),
                    "title": "Н" * 100,
                    "description": "О" * 160,
                }
            ],
        )
        self.assertIn("summary_description", schema["required"])
        self.assertEqual(
            schema["properties"]["description_warning"]["type"], ["string", "null"]
        )


class IssueSuggestionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_owned_apartments_do_not_mix_private_suggestions(self) -> None:
        house_id = UUID(int=20)
        actor = SimpleNamespace(id=UUID(int=7), kind="resident")
        first = SimpleNamespace(id=UUID(int=10), entrance_number=2)
        second = SimpleNamespace(id=UUID(int=11), entrance_number=3)
        cards = [
            card(1, scope_all_house=True),
            card(2),
            card(3, author_user_id=actor.id),
            card(4),
        ]
        targets = [
            SimpleNamespace(card_id=UUID(int=2), apartment_id=first.id, entrance_number=None),
            SimpleNamespace(card_id=UUID(int=3), apartment_id=second.id, entrance_number=None),
            SimpleNamespace(card_id=UUID(int=4), apartment_id=None, entrance_number=2),
        ]
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(archived_at=None)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [first, second])),
            scalars=AsyncMock(side_effect=[
                SimpleNamespace(all=lambda: cards),
                SimpleNamespace(all=lambda: targets),
            ]),
        )
        with patch(
            "src.domain.issues.suggest._attach_candidate_descriptions",
            new=AsyncMock(side_effect=lambda _session, candidates: candidates),
        ):
            result, _ = await _load_candidates(
                session, actor, house_id, "Лифт сломан", None,
                scope="apartment", apartment_id=first.id,
            )
        self.assertEqual({item.id for item in result}, {UUID(int=1), UUID(int=2), UUID(int=4)})
        self.assertNotIn(UUID(int=3), {item.id for item in result})

    async def test_unowned_apartment_cannot_be_used_to_search_suggestions(self) -> None:
        first = SimpleNamespace(id=UUID(int=10), entrance_number=2)
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(archived_at=None)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [first])),
            scalars=AsyncMock(),
        )
        with self.assertRaises(IssueError) as raised:
            await _load_candidates(
                session, SimpleNamespace(id=UUID(int=7), kind="resident"),
                UUID(int=20), "Лифт сломан", None,
                scope="apartment", apartment_id=UUID(int=11),
            )
        self.assertEqual(raised.exception.code, "apartment_access_denied")
        session.scalars.assert_not_awaited()

    async def test_candidate_descriptions_are_bounded_and_queried_only_for_visible_ids(
        self,
    ) -> None:
        visible_id = UUID(int=2)
        session = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(
                            card_id=visible_id, raw_description="  вода   " + "в" * 250
                        )
                    ]
                )
            )
        )
        result = await _attach_candidate_descriptions(
            session, [Candidate(visible_id, "Потоп в подвале")]
        )
        self.assertEqual(result[0].description, ("вода " + "в" * 250)[:160])
        statement = session.execute.call_args.args[0]
        params = statement.compile().params
        self.assertIn([visible_id], params.values())

    async def test_staff_without_resident_grant_cannot_suggest(self) -> None:
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(archived_at=None)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        )
        with self.assertRaises(IssueError) as error:
            await _load_candidates(
                session,
                SimpleNamespace(id=UUID(int=1)),
                UUID(int=2),
                "Лифт сломан",
                None,
            )
        self.assertEqual(error.exception.status_code, 403)

    async def test_missing_provider_is_configuration_error_before_database_access(
        self,
    ) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch.dict("os.environ", {}, clear=True),
        ):
            with self.assertRaises(IssueError) as caught:
                await suggest_issue(
                    SimpleNamespace(),
                    SimpleNamespace(id=UUID(int=1)),
                    house_id=UUID(int=3),
                    description="Не работает лифт в доме",
                )
            load.assert_not_awaited()
            giga.assert_not_awaited()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.code, "ai_provider_not_configured")

    async def test_explicit_local_ignores_configured_external_provider(self) -> None:
        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "local",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                },
                clear=True,
            ),
        ):
            load.return_value = (candidates, None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="Не работает лифт в доме",
            )
            giga.assert_not_awaited()
        local_logs = [call.kwargs["extra"] for call in logger.info.call_args_list]
        self.assertEqual(
            [event["phase"] for event in local_logs], ["attempt", "complete"]
        )
        self.assertEqual(
            {event["error_code"] for event in local_logs}, {"provider_disabled"}
        )
        self.assertEqual(result.source, "local")
        self.assertEqual(result.description_check, "not_checked")
        self.assertIsNone(result.description_warning)
        self.assertIsNone(result.summary_description)
        self.assertEqual(result.similar_card_ids, [UUID(int=2)])
        self.assertEqual(result.candidates, candidates)

    async def test_gigachat_is_disabled_without_explicit_opt_in(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                },
                clear=True,
            ),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="Лифт сломан",
            )
            giga.assert_not_awaited()
        self.assertEqual(
            logger.info.call_args.kwargs["extra"]["error_code"], "outbound_disabled"
        )
        self.assertEqual(result.source, "local")

    async def test_gigachat_is_disabled_without_key(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                },
                clear=True,
            ),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="Лифт сломан",
            )
            giga.assert_not_awaited()
        self.assertEqual(
            logger.info.call_args.kwargs["extra"]["error_code"], "missing_api_key"
        )
        self.assertEqual(result.source, "local")

    async def test_unknown_provider_is_configuration_error(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict("os.environ", {"ISSUE_AI_PROVIDER": "unsupported"}, clear=True),
        ):
            with self.assertRaises(IssueError) as caught:
                await suggest_issue(
                    SimpleNamespace(),
                    SimpleNamespace(id=UUID(int=1)),
                    house_id=UUID(int=3),
                    description="Лифт сломан",
                )
            load.assert_not_awaited()
            giga.assert_not_awaited()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.code, "invalid_ai_provider")
        self.assertEqual(
            logger.warning.call_args.kwargs["extra"]["error_code"], "invalid_provider"
        )

    async def test_valid_provider_result_is_only_a_suggestion(self) -> None:
        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                },
                clear=True,
            ),
        ):
            load.return_value = (candidates, "Лифт")
            giga.return_value = (
                "Не работает лифт",
                [UUID(int=2)],
                "ok",
                None,
                "Лифт в доме не работает.",
            )
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="Не работает лифт в доме",
            )
        self.assertEqual(result.source, "gigachat")
        self.assertEqual(result.similar_card_ids, [UUID(int=2)])
        self.assertEqual(result.description_check, "ok")
        self.assertIsNone(result.description_warning)
        self.assertEqual(result.summary_description, "Лифт в доме не работает.")
        self.assertEqual(result.candidates, candidates)

    async def test_provider_warning_does_not_block_suggestion(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                },
                clear=True,
            ),
        ):
            load.return_value = ([], "Лифт")
            giga.return_value = (
                "Проблема требует уточнения",
                [],
                "warning",
                "Что именно случилось с лифтом?",
                "Житель сообщил о проблеме с лифтом.",
            )
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="Что-то не так",
            )
        self.assertEqual(result.source, "gigachat")
        self.assertEqual(result.description_check, "warning")
        self.assertEqual(result.description_warning, "Что именно случилось с лифтом?")
        self.assertEqual(result.similar_card_ids, [])
        self.assertEqual(
            result.summary_description, "Житель сообщил о проблеме с лифтом."
        )

    async def test_provider_failure_falls_back_without_private_local_logs(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.suggest.gigachat_suggestion", new_callable=AsyncMock
            ) as giga,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                },
                clear=True,
            ),
        ):
            load.return_value = (
                [Candidate(UUID(int=2), "private candidate title")],
                None,
            )
            giga.return_value = None
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3),
                description="private resident description",
            )
        self.assertEqual(result.source, "local")
        self.assertIsNone(result.summary_description)
        self.assertEqual(
            logger.info.call_args.kwargs["extra"]["error_code"], "provider_unavailable"
        )
        logged = str([call.kwargs["extra"] for call in logger.info.call_args_list])
        self.assertNotIn("private-auth-key", logged)
        self.assertNotIn("private resident description", logged)
        self.assertNotIn("private candidate title", logged)


if __name__ == "__main__":
    unittest.main()
