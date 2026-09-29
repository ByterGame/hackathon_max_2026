"""Правила доступа, общие для HTTP и текстового бота."""

import re
from datetime import datetime, timezone
from typing import Any


class AccessRuleError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_phone(value: str) -> str:
    """Формат 7XXXXXXXXXX совпадает с подтверждением контакта MAX."""
    if not isinstance(value, str) or re.search(r"[^\d\s+()\-]", value):
        raise AccessRuleError(400, "invalid_phone", "Укажите российский номер телефона")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        raise AccessRuleError(400, "invalid_phone", "Укажите российский номер телефона")
    return digits


def require_verified_phone(user: Any) -> str:
    phone = getattr(user, "phone_number", None)
    verified_at = getattr(user, "phone_verified_at", None)
    if not phone or verified_at is None:
        raise AccessRuleError(
            403, "phone_required", "Сначала подтвердите номер через MAX"
        )
    return normalize_phone(phone)


def require_user_kind(user: Any, allowed: set[str]) -> None:
    kind = str(getattr(user, "kind", "unassigned"))
    if kind not in allowed:
        raise AccessRuleError(
            403, "role_conflict", "Этот аккаунт не может выполнить действие"
        )


def require_future_expiry(value: datetime | None) -> None:
    if value is not None and value <= datetime.now(timezone.utc):
        raise AccessRuleError(
            400, "invalid_expiry", "Срок доступа должен быть в будущем"
        )


def require_note(value: str) -> str:
    note = value.strip()
    if not note:
        raise AccessRuleError(400, "note_required", "Укажите пояснение к решению")
    return note


def require_open_request(status: str) -> None:
    if status in {"closed", "cancelled"}:
        raise AccessRuleError(
            409, "request_finished", "По заявке уже принято окончательное решение"
        )


def ensure_other_party(requester_id: Any, resolver_id: Any) -> None:
    if requester_id == resolver_id:
        raise AccessRuleError(
            403, "self_confirmation_forbidden", "Отмену подтверждает другая сторона"
        )


def is_grant_active(grant: Any, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    return (
        grant.revoked_at is None
        and grant.valid_from <= at
        and (grant.valid_to is None or at < grant.valid_to)
    )
