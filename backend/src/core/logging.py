"""Compact, searchable logs without request bodies or raw exception values."""

import json
import logging
import os
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

from maxapi.exceptions.max import MaxApiError, MaxConnection
from pydantic import ValidationError
from sqlalchemy.exc import StatementError

_EXTRA_FIELDS = (
    "event",
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_code",
    "provider_error_code",
    "provider_error_kind",
    "exception_type",
    "exception_message",
    "stack",
    "event_type",
    "actor_id",
    "message_id",
    "callback_id",
    "attachment_types",
    "flow_kind",
    "step",
    "result",
    "cycle_id",
    "expanded_count",
    "delivery_count",
    "sent_count",
    "failed_count",
    "blocked_count",
    "muted_count",
    "phase",
    "timestamp_length",
    "age_seconds",
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<prefix>\b(?:authorization|bot_token|db_password|gigachat_auth_key|"
    r"api_key|access_token|token|password|secret|hash|signature|"
    r"init_data|input_value|body)\b[\"']?\s*[:=]\s*[\"']?)"
    r"[^\s,;&}\])\"']+",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_BASIC_TOKEN = re.compile(r"\bBasic\s+[^\s,;]+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\]\[<>()\"']+", re.IGNORECASE)
_RUSSIAN_PHONE = re.compile(r"(?<!\d)(?:\+?7|8)(?:[\s()\-]*\d){10}(?!\d)")
_SQLSTATE = re.compile(r"[0-9A-Z]{5}\Z")
_CONSTRAINT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_MAX_EXCEPTION_MESSAGE_LENGTH = 500
current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id", default=None
)


def _redact_known_secrets(message: str) -> str:
    message = _URL.sub("[URL]", message)
    message = _BEARER_TOKEN.sub("Bearer [REDACTED]", message)
    message = _BASIC_TOKEN.sub("Basic [REDACTED]", message)
    message = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: match.group("prefix") + "[REDACTED]", message
    )
    for name in ("BOT_TOKEN", "DB_PASSWORD", "GIGACHAT_AUTH_KEY"):
        secret = os.getenv(name)
        if secret and len(secret) >= 8:
            message = message.replace(secret, "[REDACTED]")
    message = _RUSSIAN_PHONE.sub("[PHONE]", message)
    return message


def safe_exception_message(
    error: BaseException, *, sensitive_values: tuple[str, ...] = ()
) -> str:
    """Keep useful error text, omitting SQL parameters and known private values."""
    if isinstance(error, MaxApiError):
        message = f"MAX API error: status={error.code}"
        raw_code = error.raw.get("code") if isinstance(error.raw, dict) else None
        if isinstance(raw_code, str) and re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", raw_code
        ):
            message += f" code={raw_code}"
        return message
    if isinstance(error, MaxConnection):
        return "MAX connection failure"
    if isinstance(error, ValidationError):
        categories = sorted(
            {
                category
                for item in error.errors(include_input=False)
                if isinstance(category := item.get("type"), str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category)
            }
        )
        suffix = f" types={','.join(categories[:5])}" if categories else ""
        return f"ValidationError: {error.error_count()} error(s){suffix}"
    if isinstance(error, StatementError):
        original = error.orig
        message = f"{type(error).__name__}: {type(original).__name__}"
        sqlstate = getattr(original, "sqlstate", None)
        constraint = getattr(original, "constraint_name", None)
        if isinstance(sqlstate, str) and _SQLSTATE.fullmatch(sqlstate):
            message += f" sqlstate={sqlstate}"
        if isinstance(constraint, str) and _CONSTRAINT.fullmatch(constraint):
            message += f" constraint={constraint}"
        return message

    message = str(error) or type(error).__name__
    for value in sensitive_values:
        if not isinstance(value, str) or not value:
            continue
        if len(value) >= 4:
            message = message.replace(value, "[REDACTED]")
        elif message.strip() == value.strip():
            message = "[REDACTED]"
    message = _redact_known_secrets(message)
    if len(message) > _MAX_EXCEPTION_MESSAGE_LENGTH:
        message = message[: _MAX_EXCEPTION_MESSAGE_LENGTH - 3] + "..."
    return message


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )
        entry: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_known_secrets(record.getMessage()),
        }
        for field in _EXTRA_FIELDS:
            if not hasattr(record, field):
                continue
            value = getattr(record, field)
            if value is None or isinstance(value, (str, int, float, bool)):
                entry[field] = (
                    _redact_known_secrets(value) if isinstance(value, str) else value
                )
            elif field in {"stack", "attachment_types"} and isinstance(value, list):
                entry[field] = [
                    _redact_known_secrets(item)
                    for item in value
                    if isinstance(item, str)
                ]
        if "request_id" not in entry:
            request_id = current_request_id.get()
            if request_id is not None:
                entry["request_id"] = request_id
        if record.exc_info is not None:
            exception_type, _, exception_traceback = record.exc_info
            if exception_type is not None:
                entry.setdefault("exception_type", exception_type.__name__)
            if exception_traceback is not None:
                entry.setdefault(
                    "stack",
                    [
                        f"{frame.filename}:{frame.lineno} in {frame.name}"
                        for frame in traceback.extract_tb(exception_traceback)[-12:]
                    ],
                )
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
