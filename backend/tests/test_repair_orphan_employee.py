"""A one-time role repair must be explicit and refuse active staff access."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.cli.repair_orphan_employee import repair_in_session
from src.db.models import AuditEvent


class RepairOrphanEmployeeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user = SimpleNamespace(
            id=uuid4(), kind="employee", phone_number="79991234567",
            phone_verified_at=datetime.now(UTC), version=2, updated_at=None,
        )
        self.session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[self.user, self.user, None]),
            add=Mock(), commit=AsyncMock(),
        )

    async def test_dry_run_does_not_write(self) -> None:
        with patch("src.cli.repair_orphan_employee.lock_phone_role", new_callable=AsyncMock) as lock:
            result = await repair_in_session(self.session, "12345")
        self.assertIn("--apply", result)
        self.assertEqual(self.user.kind, "employee")
        self.session.add.assert_not_called()
        self.session.commit.assert_not_awaited()
        lock.assert_awaited_once_with(self.session, self.user.phone_number)

    async def test_apply_writes_role_and_audit(self) -> None:
        with patch("src.cli.repair_orphan_employee.lock_phone_role", new_callable=AsyncMock):
            result = await repair_in_session(self.session, "12345", apply=True)
        self.assertIn("Changed user", result)
        self.assertEqual(self.user.kind, "unassigned")
        self.assertEqual(self.user.version, 3)
        self.assertIsNotNone(self.user.updated_at)
        self.assertIsInstance(self.session.add.call_args.args[0], AuditEvent)
        self.session.commit.assert_awaited_once()

    async def test_active_assignment_refuses_repair(self) -> None:
        self.session.scalar.side_effect = [self.user, self.user, uuid4()]
        with patch("src.cli.repair_orphan_employee.lock_phone_role", new_callable=AsyncMock):
            with self.assertRaisesRegex(ValueError, "active staff assignment"):
                await repair_in_session(self.session, "12345", apply=True)
        self.assertEqual(self.user.kind, "employee")
        self.session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
