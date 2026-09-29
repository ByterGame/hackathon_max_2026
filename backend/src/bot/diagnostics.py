"""Safe, correlated diagnostics for MAX bot updates.

Only fixed categories and identifiers are recorded. User content is passed only
to the shared exception sanitizer and is never logged as an event field.
"""

import logging
import re
import time
import traceback
from uuid import uuid4

from maxapi.types.attachments import (
    Audio,
    Contact,
    File,
    Image,
    Location,
    Share,
    Sticker,
    Video,
)

from src.core.logging import safe_exception_message

_ATTACHMENT_TYPES = (
    (Contact, "contact"),
    (Image, "image"),
    (Video, "video"),
    (Audio, "audio"),
    (File, "file"),
    (Location, "location"),
    (Sticker, "sticker"),
    (Share, "share"),
)
_EXTERNAL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_CONTACT_ERROR_CODES = {
    "Контакт MAX не содержит проверочных данных": "contact_payload_missing",
    "MAX contact verification data is missing": "contact_verification_missing",
    "The shared contact is not the message sender": "contact_sender_mismatch",
    "Invalid MAX contact signature": "contact_signature_invalid",
    "The shared contact must contain one phone number": "contact_phone_count_invalid",
    "Phone number is empty": "contact_phone_empty",
    "Invalid phone number": "contact_phone_invalid",
    "Expected a Russian phone number": "contact_phone_format_invalid",
    "MAX account is not connected": "account_not_connected",
    "Phone number change is not supported": "contact_phone_change_unsupported",
    "Phone number is already linked to another account": "contact_phone_already_linked",
}


def safe_external_id(value: object) -> str | None:
    """Keep usable MAX IDs while rejecting control characters and long input."""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        candidate = str(value)
        if _EXTERNAL_ID.fullmatch(candidate):
            return candidate
    return None


def safe_identifier(value: object) -> str | None:
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
        return value
    return None


def attachment_types(attachments: object) -> list[str]:
    if not isinstance(attachments, (list, tuple)):
        return []
    kinds = {
        next(
            (
                name
                for attachment_type, name in _ATTACHMENT_TYPES
                if isinstance(item, attachment_type)
            ),
            "other",
        )
        for item in attachments
    }
    return sorted(kinds)


def message_event_type(body: object) -> str:
    if body is None:
        return "message"
    attachments = getattr(body, "attachments", None) or []
    kinds = attachment_types(attachments)
    if "contact" in kinds:
        return "contact"
    text = getattr(body, "text", None)
    if isinstance(text, str) and text.lstrip().startswith("/"):
        return "text_command"
    if kinds:
        return "media"
    if text:
        return "text"
    return "empty_message"


def callback_event_type(payload: object) -> str:
    if payload == "menu":
        return "callback_menu"
    if isinstance(payload, str):
        for prefix, event_type in (
            ("a:", "callback_access"),
            ("i:", "callback_issue"),
            ("n:", "callback_notification"),
            ("d:", "callback_draft"),
            ("f:", "callback_file"),
        ):
            if payload.startswith(prefix):
                return event_type
    return "callback_unknown"


def safe_stack(error: BaseException) -> list[str]:
    """Traceback locations without exception text or local variable values."""
    return [
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)[-12:]
    ]


def error_code(error: Exception, *, stage: str) -> str:
    if stage == "validate_contact" and isinstance(error, ValueError):
        return _CONTACT_ERROR_CODES.get(str(error), "contact_validation_error")
    if isinstance(error, ValueError):
        return "invalid_input"
    return safe_identifier(type(error).__name__.lower()) or "unexpected_error"


class UpdateTrace:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        event_type: str,
        message_id: object = None,
        callback_id: object = None,
        attachments: object = None,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        self.logger = logger
        self.request_id = uuid4().hex
        self.started = time.monotonic()
        self.event_type = event_type
        self.message_id = safe_external_id(message_id)
        self.callback_id = safe_external_id(callback_id)
        self.attachment_types = attachment_types(attachments)
        self._sensitive_values = sensitive_values
        self.actor_id: str | None = None
        self.flow_kind: str | None = None
        self.step: str | None = None
        self.phase: str = "received"
        self.result: str = "processed"
        self.error_code: str | None = None
        self.exception_type: str | None = None
        self.exception_message: str | None = None
        self.stack: list[str] | None = None

    def fields(self, *, event: str) -> dict:
        return {
            "event": event,
            "request_id": self.request_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "message_id": self.message_id,
            "callback_id": self.callback_id,
            "attachment_types": self.attachment_types,
            "flow_kind": self.flow_kind,
            "step": self.step,
            "phase": self.phase,
            "result": self.result,
            "duration_ms": round((time.monotonic() - self.started) * 1000),
            "error_code": self.error_code,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "stack": self.stack,
        }

    def received(self) -> None:
        fields = self.fields(event="bot_update_received")
        fields["result"] = "received"
        self.logger.info("bot_update_received", extra=fields)

    def set_dialog(self, dialog: object) -> None:
        self.flow_kind = safe_identifier(getattr(dialog, "flow_kind", None))
        self.step = safe_identifier(getattr(dialog, "step", None))

    def fail(self, error: Exception, *, result: str = "rejected") -> None:
        self.result = result
        self.error_code = error_code(error, stage=self.phase)
        self.exception_type = type(error).__name__
        self.exception_message = self.safe_message(error)
        if result == "failed":
            self.stack = safe_stack(error)

    def safe_message(self, error: BaseException) -> str:
        return safe_exception_message(error, sensitive_values=self._sensitive_values)

    def finished(self) -> None:
        level = (
            logging.ERROR
            if self.result == "failed"
            else logging.WARNING if self.result == "rejected" else logging.INFO
        )
        self.logger.log(
            level, "bot_update_finished", extra=self.fields(event="bot_update_finished")
        )
