"""Revoking staff access keeps the user's single role in sync with assignments."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.domain.access.rules import AccessRuleError
from src.domain.access.staff import revoke_staff


class StaffRevocationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="admin")
        self.employee = SimpleNamespace(
            id=uuid4(),
            kind="employee",
            phone_number="79990000001",
            phone_verified_at=datetime.now(UTC),
            version=2,
            updated_at=None,
        )
        self.assignment = SimpleNamespace(
            id=uuid4(),
            company_id=uuid4(),
            phone_number=self.employee.phone_number,
            user_id=self.employee.id,
            revoked_at=None,
            version=3,
        )
        self.session = SimpleNamespace(scalar=AsyncMock(return_value=None))

    async def _revoke(self, other_assignment_id=None):
        self.session.scalar.return_value = other_assignment_id
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.employee, self.assignment],
            ) as require_row,
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit") as audit,
            patch(
                "src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock
            ) as commit,
        ):
            result = await revoke_staff(
                self.session, self.actor, assignment_id=self.assignment.id
            )
        return result, require_row, audit, commit

    async def test_last_assignment_resets_employee_role(self) -> None:
        result, require_row, audit, commit = await self._revoke()
        self.assertIs(result, self.assignment)
        self.assertIsNotNone(self.assignment.revoked_at)
        self.assertEqual(self.assignment.version, 4)
        self.assertEqual(self.employee.kind, "unassigned")
        self.assertEqual(self.employee.version, 3)
        self.assertIsNotNone(self.employee.updated_at)
        self.assertTrue(require_row.await_args_list[1].kwargs["for_update"])
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(audit.call_args_list[0].kwargs["action"], "employee_revoked")
        commit.assert_awaited_once_with(self.session)

    async def test_other_active_assignment_keeps_employee_role(self) -> None:
        _, _, audit, _ = await self._revoke(uuid4())
        self.assertEqual(self.employee.kind, "employee")
        self.assertEqual(self.employee.version, 2)
        audit.assert_called_once()
        statement = self.session.scalar.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("staff_assignments.user_id", sql)
        self.assertIn("staff_assignments.phone_number", sql)
        self.assertIn("staff_assignments.revoked_at IS NULL", sql)

    async def test_revoking_unclaimed_assignment_does_not_change_user_role(self) -> None:
        self.assignment.user_id = None
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.assignment],
            ),
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit") as audit,
            patch(
                "src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock
            ),
        ):
            await revoke_staff(
                self.session, self.actor, assignment_id=self.assignment.id
            )
        self.assertIsNotNone(self.assignment.revoked_at)
        self.assertEqual(self.employee.kind, "employee")
        self.session.scalar.assert_awaited_once()
        audit.assert_called_once()

    async def test_revoking_last_unclaimed_assignment_clears_verified_employee_role(
        self,
    ) -> None:
        self.assignment.user_id = None
        self.session.scalar.side_effect = [self.employee, None]
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.assignment],
            ),
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit") as audit,
            patch(
                "src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock
            ),
        ):
            await revoke_staff(
                self.session, self.actor, assignment_id=self.assignment.id
            )
        self.assertEqual(self.employee.kind, "unassigned")
        self.assertEqual(audit.call_count, 2)

    async def test_actor_binding_during_permission_check_is_not_stale(self) -> None:
        self.assignment.user_id = None

        async def bind_actor(*_args, **_kwargs):
            self.assignment.user_id = self.employee.id

        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.employee, self.assignment],
            ),
            patch(
                "src.domain.access.staff.require_staff",
                new_callable=AsyncMock,
                side_effect=bind_actor,
            ),
            patch("src.domain.access.staff.audit"),
            patch(
                "src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock
            ),
        ):
            await revoke_staff(
                self.session, self.actor, assignment_id=self.assignment.id
            )
        self.assertEqual(self.employee.kind, "unassigned")

    async def test_rebinding_after_initial_read_requires_retry(self) -> None:
        self.assignment.user_id = None
        changed = SimpleNamespace(**vars(self.assignment))
        changed.user_id = self.employee.id
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, changed],
            ),
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit") as audit,
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await revoke_staff(
                    self.session, self.actor, assignment_id=self.assignment.id
                )
        self.assertEqual(caught.exception.code, "assignment_changed")
        self.assertIsNone(changed.revoked_at)
        audit.assert_not_called()

    async def test_last_company_employee_cannot_revoke_own_access(self) -> None:
        self.actor = self.employee
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.employee, self.assignment, SimpleNamespace()],
            ) as require_row,
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit") as audit,
            patch("src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock) as commit,
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await revoke_staff(
                    self.session, self.actor, assignment_id=self.assignment.id
                )
        self.assertEqual(caught.exception.code, "last_staff_self_revoke")
        self.assertIsNone(self.assignment.revoked_at)
        self.assertTrue(require_row.await_args_list[-1].kwargs["for_update"])
        self.assertEqual(require_row.await_args_list[-1].args[2], self.assignment.company_id)
        audit.assert_not_called()
        commit.assert_not_awaited()

    async def test_employee_can_revoke_self_when_another_employee_remains(self) -> None:
        self.actor = self.employee
        self.session.scalar.side_effect = [uuid4(), None]
        with (
            patch(
                "src.domain.access.staff.require_row",
                new_callable=AsyncMock,
                side_effect=[self.assignment, self.employee, self.assignment, SimpleNamespace()],
            ),
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
            patch("src.domain.access.staff.audit"),
            patch("src.domain.access.staff.commit_or_conflict", new_callable=AsyncMock) as commit,
        ):
            await revoke_staff(self.session, self.actor, assignment_id=self.assignment.id)
        self.assertIsNotNone(self.assignment.revoked_at)
        self.assertEqual(self.employee.kind, "unassigned")
        commit.assert_awaited_once_with(self.session)


if __name__ == "__main__":
    unittest.main()
