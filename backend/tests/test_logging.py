"""Server logs retain diagnostics without emitting attached sensitive data."""

import json
import logging
import sys
import unittest
from unittest.mock import patch

from maxapi.exceptions.max import MaxApiError, MaxConnection
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import StatementError

from src.__main__ import main
from src.core.logging import (
    JsonLogFormatter,
    current_request_id,
    safe_exception_message,
)


class JsonLogFormatterTests(unittest.TestCase):
    def test_exception_text_keeps_diagnostics_but_redacts_private_values(self) -> None:
        error = ValueError(
            "Expired contact for +7 (999) 123-45-67; token=raw-secret; "
            "Authorization: Bearer private-bearer; text=private description"
        )
        with patch.dict("os.environ", {"BOT_TOKEN": "raw-secret"}):
            message = safe_exception_message(
                error, sensitive_values=("private description",)
            )
        self.assertIn("Expired contact", message)
        for private in (
            "+7 (999) 123-45-67",
            "79991234567",
            "raw-secret",
            "private-bearer",
            "private description",
        ):
            self.assertNotIn(private, message)

    def test_sql_exception_excludes_statement_and_parameters(self) -> None:
        error = StatementError(
            "insert failed",
            "INSERT INTO users (phone) VALUES (:phone)",
            {"phone": "79991234567"},
            ValueError("duplicate phone 79991234567"),
        )
        message = safe_exception_message(error)
        self.assertIn("StatementError", message)
        self.assertNotIn("INSERT", message)
        self.assertNotIn("79991234567", message)

    def test_max_api_error_keeps_status_but_not_raw_response(self) -> None:
        error = MaxApiError(
            code=400,
            raw={
                "code": "attachment.not.ready",
                "message": "private resident text",
                "token": "private-provider-token",
            },
        )
        message = safe_exception_message(error)
        self.assertEqual(message, "MAX API error: status=400 code=attachment.not.ready")
        self.assertEqual(
            safe_exception_message(MaxConnection("https://example.com/?token=secret")),
            "MAX connection failure",
        )

    def test_library_log_message_redacts_complete_url(self) -> None:
        record = logging.LogRecord(
            name="bot",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="download failed: https://media.example.com/path/signed?token=private",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["message"], "download failed: [URL]")

    def test_validation_error_keeps_error_categories_without_input(self) -> None:
        class PrivateInput(BaseModel):
            age: int

        try:
            PrivateInput(age="private resident text")
        except ValidationError as error:
            message = safe_exception_message(error)
        self.assertIn("ValidationError: 1 error(s)", message)
        self.assertIn("int_parsing", message)
        self.assertNotIn("private resident text", message)

    def test_includes_searchable_fields_but_ignores_unapproved_extra(self) -> None:
        record = logging.LogRecord(
            name="src.http",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="http_request",
            args=(),
            exc_info=None,
        )
        record.event = "http_request"
        record.request_id = "request-123"
        record.status_code = 400
        record.error_code = "expired_contact_data"
        record.phone = "+79991234567"
        record.token = "secret-token"
        record.body = "secret-description"

        payload = json.loads(JsonLogFormatter().format(record))

        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["error_code"], "expired_contact_data")
        self.assertNotIn("+79991234567", json.dumps(payload))
        self.assertNotIn("secret-token", json.dumps(payload))
        self.assertNotIn("secret-description", json.dumps(payload))

    def test_nested_log_event_inherits_correlation_id(self) -> None:
        record = logging.LogRecord(
            name="src.domain.issues.suggest",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="issue_suggestion_gigachat",
            args=(),
            exc_info=None,
        )
        record.provider_error_code = 7
        record.provider_error_kind = "scope_mismatch"
        token = current_request_id.set("request-123")
        try:
            payload = json.loads(JsonLogFormatter().format(record))
        finally:
            current_request_id.reset(token)
        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["provider_error_code"], 7)
        self.assertEqual(payload["provider_error_kind"], "scope_mismatch")
        self.assertIsNone(current_request_id.get())

    def test_exception_info_is_not_implicitly_formatted(self) -> None:
        try:
            raise ValueError("sensitive submitted text")
        except ValueError:
            record = logging.LogRecord(
                name="src.http",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="http_request_failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        record.exception_type = "ValueError"
        record.stack = ["views/auth.py:42 in verify_contact"]

        payload = json.loads(JsonLogFormatter().format(record))

        self.assertEqual(payload["stack"], ["views/auth.py:42 in verify_contact"])
        self.assertNotIn("sensitive submitted text", json.dumps(payload))

    def test_exception_stack_without_explicit_extra_stays_sanitized(self) -> None:
        try:
            raise RuntimeError("private document contents")
        except RuntimeError:
            record = logging.LogRecord(
                name="src.worker",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="worker_failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        payload = json.loads(JsonLogFormatter().format(record))

        self.assertEqual(payload["exception_type"], "RuntimeError")
        self.assertTrue(any("test_logging.py" in frame for frame in payload["stack"]))
        self.assertNotIn("private document contents", json.dumps(payload))

    def test_known_secrets_are_redacted_from_library_messages(self) -> None:
        record = logging.LogRecord(
            name="third_party",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="upstream failed with token bot-token-secret",
            args=(),
            exc_info=None,
        )
        with patch.dict("os.environ", {"BOT_TOKEN": "bot-token-secret"}):
            payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["message"], "upstream failed with token [REDACTED]")

    def test_gigachat_basic_auth_and_configured_key_are_redacted(self) -> None:
        key = "private-gigachat-auth-key"
        with patch.dict("os.environ", {"GIGACHAT_AUTH_KEY": key}):
            message = safe_exception_message(
                ValueError(
                    f"Authorization: Basic {key}; GIGACHAT_AUTH_KEY={key}; "
                    f"provider echoed {key}"
                )
            )
            record = logging.LogRecord(
                name="third_party",
                level=logging.WARNING,
                pathname=__file__,
                lineno=1,
                msg=f"Basic {key}; provider echoed {key}",
                args=(),
                exc_info=None,
            )
            payload = json.loads(JsonLogFormatter().format(record))
        self.assertNotIn(key, message)
        self.assertNotIn(key, json.dumps(payload))

    def test_server_disables_raw_uvicorn_access_log(self) -> None:
        with (
            patch("src.__main__.configure_logging"),
            patch("src.__main__.load_port", return_value=8000),
            patch("src.__main__.uvicorn.run") as run,
        ):
            main()
        self.assertFalse(run.call_args.kwargs["access_log"])
        self.assertIsNone(run.call_args.kwargs["log_config"])


if __name__ == "__main__":
    unittest.main()
