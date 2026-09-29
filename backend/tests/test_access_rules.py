"""Базовые инварианты доступа без внешнего PostgreSQL."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.common.auth import normalize_phone_number
from src.domain.access.rules import (
    AccessRuleError,
    ensure_other_party,
    is_grant_active,
    normalize_phone,
    require_future_expiry,
    require_user_kind,
    require_verified_phone,
)


class AccessRuleTests(unittest.TestCase):
    def test_pending_phone_matches_verified_max_contact(self) -> None:
        for typed in ("+7 (999) 123-45-67", "8 999 123 45 67", "9991234567"):
            self.assertEqual(normalize_phone(typed), normalize_phone_number(typed))

    def test_invalid_phone_is_rejected(self) -> None:
        with self.assertRaises(AccessRuleError):
            normalize_phone("test")

    def test_verified_phone_is_required_for_resident(self) -> None:
        user = SimpleNamespace(phone_number="79991234567", phone_verified_at=None)
        with self.assertRaises(AccessRuleError) as raised:
            require_verified_phone(user)
        self.assertEqual(raised.exception.code, "phone_required")

    def test_employee_cannot_become_resident(self) -> None:
        with self.assertRaises(AccessRuleError) as raised:
            require_user_kind(
                SimpleNamespace(kind="employee"), {"unassigned", "resident"}
            )
        self.assertEqual(raised.exception.code, "role_conflict")

    def test_cancellation_must_be_confirmed_by_other_account(self) -> None:
        same_id = uuid4()
        with self.assertRaises(AccessRuleError):
            ensure_other_party(same_id, same_id)

    def test_grant_expires_at_exclusive_boundary(self) -> None:
        now = datetime.now(timezone.utc)
        grant = SimpleNamespace(
            valid_from=now - timedelta(days=1),
            valid_to=now,
            revoked_at=None,
        )
        self.assertFalse(is_grant_active(grant, now))
        grant.valid_to = now + timedelta(days=1)
        self.assertTrue(is_grant_active(grant, now))
        grant.revoked_at = now
        self.assertFalse(is_grant_active(grant, now))

    def test_expiry_must_be_future(self) -> None:
        with self.assertRaises(AccessRuleError):
            require_future_expiry(datetime.now(timezone.utc) - timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
