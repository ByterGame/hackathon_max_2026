import io
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from src.core.logging import JsonLogFormatter
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


def fake_provider_session(status: int, content: str | None = None):
    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self):
            if content is None:
                raise AssertionError("response body should not be read")
            return {"choices": [{"message": {"content": content}}]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            response = FakeResponse()
            response.status = status
            return response

    return FakeSession()


def captured_suggestion_logs() -> tuple[logging.Logger, io.StringIO]:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.Logger("test.issue_suggestion", level=logging.INFO)
    logger.addHandler(handler)
    return logger, output


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
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=True),
        ):
            load.return_value = (candidates, None)
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Не работает лифт в доме"
            )
            groq.assert_not_awaited()
        local_logs = [call.kwargs["extra"] for call in logger.info.call_args_list]
        self.assertEqual([event["phase"] for event in local_logs], ["attempt", "complete"])
        self.assertEqual({event["error_code"] for event in local_logs}, {"outbound_disabled"})
        self.assertEqual(local_logs[-1]["result"], "success")
        self.assertGreaterEqual(local_logs[-1]["duration_ms"], 0)
        self.assertEqual(result.source, "local")
        self.assertEqual(result.similar_card_ids, [UUID(int=2)])
        self.assertEqual(result.candidates, candidates)

    async def test_outbound_is_disabled_without_key(self) -> None:
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest._groq_suggestion", new_callable=AsyncMock) as groq,
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict("os.environ", {"GROQ_SEND_REAL_DATA": "1"}, clear=True),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="Лифт сломан"
            )
            groq.assert_not_awaited()
        self.assertEqual(logger.info.call_args.kwargs["extra"]["error_code"], "missing_api_key")
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
                raise TimeoutError(
                    "Provider timed out for private resident description; "
                    "api_key=private-provider-key"
                )

            async def __aexit__(self, *_args):
                return None

        candidates = [Candidate(UUID(int=2), "Не работает лифт")]
        with (
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest.aiohttp.ClientSession", return_value=TimedOutSession()),
            patch("src.domain.issues.suggest.logger") as logger,
            patch.dict(
                "os.environ", {
                    "GROQ_API_KEY": "private-provider-key",
                    "GROQ_SEND_REAL_DATA": "1",
                }, clear=True
            ),
        ):
            load.return_value = (candidates, "Лифт")
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description="private resident description"
            )
        provider_logs = [call.kwargs["extra"] for call in logger.info.call_args_list]
        self.assertEqual(provider_logs[0]["phase"], "attempt")
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["event"], "issue_suggestion_groq")
        self.assertEqual(failure["error_code"], "timeout")
        self.assertEqual(failure["exception_type"], "TimeoutError")
        self.assertIn("Provider timed out", failure["exception_message"])
        self.assertNotIn("private resident description", failure["exception_message"])
        self.assertNotIn("private-provider-key", failure["exception_message"])
        self.assertGreaterEqual(failure["duration_ms"], 0)
        self.assertEqual(logger.info.call_args.kwargs["extra"]["error_code"], "provider_unavailable")
        self.assertEqual(result.source, "local")
        self.assertEqual(result.suggested_title, "private resident description")

    async def test_provider_http_status_is_logged_without_reading_body(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest.aiohttp.ClientSession",
                return_value=fake_provider_session(429),
            ),
            patch("src.domain.issues.suggest.logger") as logger,
        ):
            result = await _groq_suggestion(
                api_key="private-provider-key", model="openai/gpt-oss-20b",
                category_name=None, description="private resident description", candidates=[],
            )
        self.assertIsNone(result)
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["status_code"], 429)
        self.assertEqual(failure["error_code"], "http_status")
        self.assertGreaterEqual(failure["duration_ms"], 0)
        self.assertNotIn("private resident description", json.dumps(failure))
        self.assertNotIn("private-provider-key", json.dumps(failure))

    async def test_provider_success_logs_status_and_duration_without_content(self) -> None:
        response = '{"title":"private model title","similar_card_ids":[]}'
        with (
            patch(
                "src.domain.issues.suggest.aiohttp.ClientSession",
                return_value=fake_provider_session(200, response),
            ),
            patch("src.domain.issues.suggest.logger") as logger,
        ):
            result = await _groq_suggestion(
                api_key="private-provider-key", model="openai/gpt-oss-20b",
                category_name=None, description="private resident description", candidates=[],
            )
        self.assertEqual(result, ("private model title", []))
        events = [call.kwargs["extra"] for call in logger.info.call_args_list]
        self.assertEqual([event["phase"] for event in events], ["attempt", "complete"])
        self.assertEqual(events[-1]["result"], "success")
        self.assertEqual(events[-1]["status_code"], 200)
        self.assertGreaterEqual(events[-1]["duration_ms"], 0)
        self.assertNotIn("private model title", json.dumps(events))
        self.assertNotIn("private resident description", json.dumps(events))

    async def test_formatted_failure_and_fallback_keep_diagnostics_without_private_data(self) -> None:
        description = "private resident description"
        api_key = "private-provider-key"
        candidate_title = "private candidate title"

        class TimedOutSession:
            async def __aenter__(self):
                raise TimeoutError(
                    f"Provider timed out for {description}; api_key={api_key}; "
                    f"candidate={candidate_title}"
                )

            async def __aexit__(self, *_args):
                return None

        test_logger, output = captured_suggestion_logs()
        with (
            patch("src.domain.issues.suggest.logger", test_logger),
            patch("src.domain.issues.suggest._load_candidates", new_callable=AsyncMock) as load,
            patch("src.domain.issues.suggest.aiohttp.ClientSession", return_value=TimedOutSession()),
            patch.dict(
                "os.environ", {"GROQ_API_KEY": api_key, "GROQ_SEND_REAL_DATA": "1"}, clear=True
            ),
        ):
            load.return_value = ([Candidate(UUID(int=2), candidate_title)], "private category")
            result = await suggest_issue(
                SimpleNamespace(), SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=3), description=description,
            )

        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(result.source, "local")
        self.assertEqual(
            [(event["event"], event["phase"]) for event in events],
            [
                ("issue_suggestion_groq", "attempt"),
                ("issue_suggestion_groq", "complete"),
                ("issue_suggestion_local", "attempt"),
                ("issue_suggestion_local", "complete"),
            ],
        )
        self.assertEqual(events[1]["error_code"], "timeout")
        self.assertEqual(events[1]["exception_type"], "TimeoutError")
        self.assertIn("Provider timed out", events[1]["exception_message"])
        self.assertGreaterEqual(events[1]["duration_ms"], 0)
        self.assertEqual(events[3]["error_code"], "provider_unavailable")
        self.assertGreaterEqual(events[3]["duration_ms"], 0)
        for private in (description, api_key, candidate_title, "private category"):
            self.assertNotIn(private, output.getvalue())
        self.assertNotIn("Сформулируй краткое русское название", output.getvalue())

    async def test_formatted_invalid_json_excludes_model_response(self) -> None:
        response = "private model response"
        test_logger, output = captured_suggestion_logs()
        with (
            patch("src.domain.issues.suggest.logger", test_logger),
            patch(
                "src.domain.issues.suggest.aiohttp.ClientSession",
                return_value=fake_provider_session(200, response),
            ),
        ):
            result = await _groq_suggestion(
                api_key="private-provider-key", model="openai/gpt-oss-20b",
                category_name=None, description="private resident description", candidates=[],
            )
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIsNone(result)
        self.assertEqual(events[1]["error_code"], "invalid_response")
        self.assertEqual(events[1]["status_code"], 200)
        self.assertEqual(events[1]["exception_type"], "JSONDecodeError")
        self.assertIn("Expecting value", events[1]["exception_message"])
        for private in (response, "private resident description", "private-provider-key"):
            self.assertNotIn(private, output.getvalue())

    async def test_provider_response_rejects_hallucinated_ids(self) -> None:
        response = (
            '{"title":"Лифт сломан","similar_card_ids":'
            '["00000000-0000-0000-0000-000000000003"]}'
        )
        with (
            patch(
                "src.domain.issues.suggest.aiohttp.ClientSession",
                return_value=fake_provider_session(200, response),
            ),
            patch("src.domain.issues.suggest.logger") as logger,
        ):
            result = await _groq_suggestion(
                api_key="test-key", model="openai/gpt-oss-20b",
                category_name=None, description="Лифт сломан",
                candidates=[Candidate(UUID(int=2), "Лифт сломан")],
            )
        self.assertIsNone(result)
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["error_code"], "invalid_response")
        self.assertEqual(failure["status_code"], 200)
        self.assertNotIn("Лифт сломан", json.dumps(failure, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
