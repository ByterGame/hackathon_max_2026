import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from src.domain.issues.service import IssueError
from src.domain.issues.suggest import (
    Candidate,
    _groq_suggestion,
    _load_candidates,
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
            SimpleNamespace(card_id=UUID(int=3), apartment_id=my_apartment, entrance_number=None),
        ]
        result = _select_visible_candidates(
            cards=cards,
            targets=targets,
            actor_id=UUID(int=7),
            apartment_ids={my_apartment},
            entrance_numbers={2},
            description="Лифт сломан",
            category_id=None,
        )
        self.assertEqual({item.id for item in result}, {UUID(int=1), UUID(int=3), UUID(int=6)})

    def test_provider_ids_must_all_be_allowed_and_unique(self) -> None:
        allowed = {UUID(int=1), UUID(int=2)}
        valid = {"title": "Сломан лифт", "similar_card_ids": [str(UUID(int=1))]}
        self.assertEqual(_validate_provider_payload(valid, allowed), ("Сломан лифт", [UUID(int=1)]))
        self.assertIsNone(
            _validate_provider_payload(
                {"title": "Сломан лифт", "similar_card_ids": [str(UUID(int=3))]}, allowed
            )
        )
        self.assertIsNone(
            _validate_provider_payload(
                {"title": "Сломан лифт", "similar_card_ids": [str(UUID(int=1))] * 2}, allowed
            )
        )
        self.assertIsNone(_validate_provider_payload(valid | {"action": "merge"}, allowed))


class IssueSuggestionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_staff_without_resident_grant_cannot_suggest(self) -> None:
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(archived_at=None)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        )
        with self.assertRaises(IssueError) as error:
            await _load_candidates(
                session, SimpleNamespace(id=UUID(int=1)), UUID(int=2), "Лифт сломан", None
            )
        self.assertEqual(error.exception.status_code, 403)

    async def test_outbound_is_disabled_without_explicit_opt_in(self) -> None:
        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest._groq_suggestion", new_callable=AsyncMock) as groq,
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=True),
        ):
            load.return_value = (candidates, None)
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Не работает лифт в доме"
            )
            groq.assert_not_awaited()
        self.assertEqual(result.source, "local")
        self.assertEqual(result.similar_card_ids, [UUID(int=2)])
        self.assertEqual(result.candidates, candidates)

    async def test_outbound_is_disabled_without_key(self) -> None:
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest._groq_suggestion", new_callable=AsyncMock) as groq,
            patch.dict("os.environ", {"GROQ_SEND_REAL_DATA": "1"}, clear=True),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Лифт сломан"
            )
            groq.assert_not_awaited()
        self.assertEqual(result.source, "local")

    async def test_valid_provider_result_is_only_a_suggestion(self) -> None:
        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest._groq_suggestion", new_callable=AsyncMock) as groq,
            patch.dict(
                "os.environ", {"GROQ_API_KEY": "test-key", "GROQ_SEND_REAL_DATA": "1"}, clear=True
            ),
        ):
            load.return_value = (candidates, "Лифт")
            groq.return_value = ("Не работает лифт", [UUID(int=2)])
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Не работает лифт в доме"
            )
        self.assertEqual(result.source, "groq")
        self.assertEqual(result.similar_card_ids, [UUID(int=2)])

    async def test_provider_timeout_returns_manual_path(self) -> None:
        class TimedOutSession:
            async def __aenter__(self):
                raise TimeoutError

            async def __aexit__(self, *_args):
                return None

        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest.aiohttp.ClientSession", return_value=TimedOutSession()),
            patch.dict(
                "os.environ", {"GROQ_API_KEY": "test-key", "GROQ_SEND_REAL_DATA": "1"}, clear=True
            ),
        ):
            load.return_value = (candidates, "Лифт")
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Не работает лифт в доме"
            )
        self.assertEqual(result.source, "local")
        self.assertEqual(result.suggested_title, "Не работает лифт в доме")

    async def test_provider_response_rejects_hallucinated_ids(self) -> None:
        class FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def json(self):
                return {
                    "choices": [{"message": {"content": (
                        '{"title":"Лифт сломан","similar_card_ids":'
                        '["00000000-0000-0000-0000-000000000003"]}'
                    )}}]
                }

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def post(self, *_args, **_kwargs):
                return FakeResponse()

        with patch("src.domain.issues.suggest.aiohttp.ClientSession", return_value=FakeSession()):
            result = await _groq_suggestion(
                api_key="test-key", model="openai/gpt-oss-20b",
                category_name=None, description="Лифт сломан",
                candidates=[Candidate(UUID(int=2), "Лифт сломан")],
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
