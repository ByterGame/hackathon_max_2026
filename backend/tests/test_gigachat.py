import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from src.domain.issues import gigachat
from src.domain.issues.suggest import Candidate, Suggestion, suggest_issue
from src.gen.issues.api.suggest import Request as SuggestRequest
from src.views.issues.suggest import suggest as suggest_view


class FakeResponse:
    def __init__(self, status: int, payload: object = None) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def valid_completion(*, card_id: UUID | None = None) -> FakeResponse:
    content = {
        "title": "Не работает лифт",
        "similar_card_ids": [str(card_id)] if card_id else [],
        "description_check": "ok",
        "description_warning": None,
        "summary_description": "Лифт в доме не реагирует на вызов.",
    }
    return FakeResponse(
        200, {"choices": [{"message": {"content": json.dumps(content)}}]}
    )


class GigaChatSuggestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        gigachat._token_cache.clear()

    async def test_http_suggestion_preserves_gigachat_source_and_summary(self) -> None:
        with patch(
            "src.views.issues.suggest.suggest_issue", new_callable=AsyncMock
        ) as service:
            service.return_value = Suggestion(
                "Не работает лифт",
                [],
                [],
                "gigachat",
                "ok",
                None,
                "Лифт в доме не реагирует на вызов.",
            )
            response = await suggest_view(
                SuggestRequest(house_id=UUID(int=2), description="Лифт сломан"),
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
            )
        self.assertEqual(response.source, "gigachat")
        self.assertEqual(
            response.summary_description, "Лифт в доме не реагирует на вызов."
        )

    async def test_gigachat_needs_explicit_real_data_opt_in(self) -> None:
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
                },
                clear=True,
            ),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
            giga.assert_not_awaited()
        self.assertEqual(result.source, "local")
        self.assertIsNone(result.summary_description)

    async def test_oauth_completion_and_cached_token(self) -> None:
        self.assertEqual(
            gigachat.DEFAULT_AUTH_URL, "https://api.giga.chat/api/v2/oauth"
        )
        candidate_id = UUID(int=3)
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "private-access-token",
                        "expires_at": int((time.time() + 1800) * 1000),
                    },
                ),
                valid_completion(card_id=candidate_id),
                valid_completion(card_id=candidate_id),
            ]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
            patch("src.domain.issues.gigachat._logger") as logger,
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
                [Candidate(candidate_id, "Лифт остановился", "private candidate text")],
                "Лифт",
            )
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="private resident description",
            )
            second = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="private resident description",
            )
        self.assertEqual(result.source, "gigachat")
        self.assertEqual(second.source, "gigachat")
        self.assertEqual(result.similar_card_ids, [candidate_id])
        self.assertEqual(
            result.summary_description, "Лифт в доме не реагирует на вызов."
        )
        self.assertEqual(
            [url for url, _ in client.calls],
            [gigachat.DEFAULT_AUTH_URL, gigachat.CHAT_URL, gigachat.CHAT_URL],
        )
        auth = client.calls[0][1]
        self.assertEqual(auth["data"], {"scope": "GIGACHAT_API_PERS"})
        self.assertEqual(auth["headers"]["Authorization"], "Basic private-auth-key")
        self.assertEqual(UUID(auth["headers"]["RqUID"]).version, 4)
        self.assertIs(auth["ssl"], True)
        request = client.calls[1][1]
        self.assertEqual(
            request["headers"]["Authorization"], "Bearer private-access-token"
        )
        self.assertIs(request["ssl"], True)
        self.assertEqual(request["json"]["model"], "GigaChat-2-Pro")
        self.assertEqual(request["json"]["response_format"]["type"], "json_schema")
        self.assertIn(
            "summary_description",
            request["json"]["response_format"]["schema"]["required"],
        )
        supplied = json.loads(request["json"]["messages"][1]["content"])
        self.assertEqual(
            supplied["candidates"][0]["description"], "private candidate text"
        )
        log_fields = json.dumps(
            [
                call.kwargs["extra"]
                for call in logger.info.call_args_list + logger.warning.call_args_list
            ]
        )
        for private in (
            "private-auth-key",
            "private-access-token",
            "private resident description",
            "private candidate text",
        ):
            self.assertNotIn(private, log_fields)

    async def test_schema_422_retries_once_without_response_format(self) -> None:
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "private-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                FakeResponse(422),
                valid_completion(),
            ]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
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
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
        self.assertEqual(result.source, "gigachat")
        self.assertEqual(len(client.calls), 3)
        self.assertIn("response_format", client.calls[1][1]["json"])
        plain = client.calls[2][1]["json"]
        self.assertNotIn("response_format", plain)
        self.assertIn("summary_description", plain["messages"][0]["content"])

    async def test_auth_failure_falls_back_without_logging_secrets(self) -> None:
        client = FakeClient(
            [FakeResponse(403, {"error": "private resident description"})]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
            patch("src.domain.issues.gigachat._logger") as logger,
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
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="private resident description",
            )
        self.assertEqual(result.source, "local")
        self.assertIsNone(result.summary_description)
        self.assertEqual(len(client.calls), 1)
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["error_code"], "auth_http_status")
        self.assertEqual(failure["status_code"], 403)
        self.assertNotIn("private-auth-key", json.dumps(failure))
        self.assertNotIn("private resident description", json.dumps(failure))

    async def test_malformed_model_json_does_not_enter_logs(self) -> None:
        private_response = "private model response"
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "private-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                FakeResponse(
                    200, {"choices": [{"message": {"content": private_response}}]}
                ),
            ]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
            patch("src.domain.issues.gigachat._logger") as logger,
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
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="private resident description",
            )
        self.assertEqual(result.source, "local")
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["error_code"], "invalid_response")
        self.assertNotIn(private_response, json.dumps(failure))
        self.assertNotIn("private resident description", json.dumps(failure))
        self.assertNotIn("private-access-token", json.dumps(failure))

    async def test_timeout_does_not_log_exception_private_content(self) -> None:
        class TimedOutSession:
            async def __aenter__(self):
                raise TimeoutError("private resident description; private-auth-key")

            async def __aexit__(self, *_args):
                return None

        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession",
                return_value=TimedOutSession(),
            ),
            patch("src.domain.issues.gigachat._logger") as logger,
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
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="private resident description",
            )
        self.assertEqual(result.source, "local")
        failure = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(failure["error_code"], "timeout")
        self.assertEqual(failure["exception_type"], "TimeoutError")
        self.assertNotIn("private resident description", json.dumps(failure))
        self.assertNotIn("private-auth-key", json.dumps(failure))

    async def test_invalid_auth_url_never_sends_key(self) -> None:
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch("src.domain.issues.gigachat.aiohttp.ClientSession") as session,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                    "GIGACHAT_AUTH_URL": "https://example.invalid/oauth",
                },
                clear=True,
            ),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
            session.assert_not_called()
        self.assertEqual(result.source, "local")

    async def test_custom_ca_keeps_verification_enabled_for_both_requests(self) -> None:
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "private-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                valid_completion(),
            ]
        )
        ca_context = Mock()
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
            patch(
                "src.domain.issues.gigachat.ssl.create_default_context",
                return_value=ca_context,
            ) as ca,
            patch.dict(
                "os.environ",
                {
                    "ISSUE_AI_PROVIDER": "gigachat",
                    "GIGACHAT_AUTH_KEY": "private-auth-key",
                    "GIGACHAT_SEND_REAL_DATA": "1",
                    "GIGACHAT_AUTH_URL": gigachat.LEGACY_AUTH_URL,
                    "GIGACHAT_CA_BUNDLE": "/trusted/gigachat-ca.pem",
                    "GIGACHAT_SCOPE": "B2B",
                    "GIGACHAT_MODEL": "GigaChat-2-Max",
                },
                clear=True,
            ),
        ):
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
        self.assertEqual(result.source, "gigachat")
        ca.assert_called_once_with()
        ca_context.load_verify_locations.assert_called_once_with(
            cafile="/trusted/gigachat-ca.pem"
        )
        self.assertEqual(client.calls[0][0], gigachat.LEGACY_AUTH_URL)
        self.assertEqual(client.calls[0][1]["data"], {"scope": "GIGACHAT_API_B2B"})
        self.assertIs(client.calls[0][1]["ssl"], ca_context)
        self.assertIs(client.calls[1][1]["ssl"], ca_context)
        self.assertEqual(client.calls[1][1]["json"]["model"], "GigaChat-2-Max")

    async def test_completion_401_refreshes_token_once_within_same_request(
        self,
    ) -> None:
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "expired-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                FakeResponse(401),
                FakeResponse(
                    200,
                    {
                        "access_token": "renewed-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                valid_completion(),
            ]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
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
            load.return_value = ([], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
        self.assertEqual(result.source, "gigachat")
        self.assertEqual(
            [url for url, _ in client.calls],
            [
                gigachat.DEFAULT_AUTH_URL,
                gigachat.CHAT_URL,
                gigachat.DEFAULT_AUTH_URL,
                gigachat.CHAT_URL,
            ],
        )
        self.assertEqual(
            client.calls[1][1]["headers"]["Authorization"],
            "Bearer expired-access-token",
        )
        self.assertEqual(
            client.calls[3][1]["headers"]["Authorization"],
            "Bearer renewed-access-token",
        )

    async def test_hallucinated_card_id_falls_back_locally(self) -> None:
        client = FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "private-access-token",
                        "expires_at": time.time() + 1800,
                    },
                ),
                valid_completion(card_id=UUID(int=99)),
            ]
        )
        with (
            patch(
                "src.domain.issues.suggest._load_candidates", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.issues.gigachat.aiohttp.ClientSession", return_value=client
            ),
            patch("src.domain.issues.gigachat._logger") as logger,
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
            load.return_value = ([Candidate(UUID(int=3), "Лифт остановился")], None)
            result = await suggest_issue(
                SimpleNamespace(),
                SimpleNamespace(id=UUID(int=1)),
                house_id=UUID(int=2),
                description="Лифт сломан",
            )
        self.assertEqual(result.source, "local")
        self.assertIsNone(result.summary_description)
        self.assertEqual(
            logger.warning.call_args.kwargs["extra"]["error_code"], "invalid_response"
        )


if __name__ == "__main__":
    unittest.main()
