"""First employee of an approved company obeys the same phone-role rules."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import Company, StaffAssignment
from src.domain.access.company import decide_company_registration
from src.domain.access.rules import AccessRuleError


class CompanyFirstEmployeeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="support")
        self.request = SimpleNamespace(
            id=uuid4(),
            phone_number="+7 999 000-00-01",
            proposed_company_name="Тестовая УК",
            status="open",
            outcome=None,
            decision_note=None,
            decided_by=None,
            decided_at=None,
            updated_at=None,
            version=1,
        )
        self.session = SimpleNamespace(scalar=AsyncMock(), add=Mock())

    async def _approve(self):
        with (
            patch(
                "src.domain.access.company.require_row", new_callable=AsyncMock
            ) as require_row,
            patch(
                "src.domain.access.company.lock_phone_role", new_callable=AsyncMock
            ) as lock_phone,
            patch("src.domain.access.company.audit"),
            patch(
                "src.domain.access.company.commit_or_conflict",
                new_callable=AsyncMock,
            ) as commit,
        ):
            require_row.return_value = self.request
            try:
                result = await decide_company_registration(
                    self.session,
                    self.actor,
                    request_id=self.request.id,
                    outcome="approved",
                    decision_note="Документы проверены",
                    display_name=None,
                )
            except AccessRuleError:
                lock_phone.assert_awaited_once_with(self.session, "79990000001")
                commit.assert_not_awaited()
                raise
            lock_phone.assert_awaited_once_with(self.session, "79990000001")
            commit.assert_awaited_once_with(self.session)
            return result

    async def test_pending_support_invite_blocks_first_employee(self) -> None:
        self.session.scalar.side_effect = [uuid4(), None]
        with self.assertRaises(AccessRuleError) as caught:
            await self._approve()
        self.assertEqual(caught.exception.code, "role_conflict")
        self.session.add.assert_not_called()

    async def test_pending_resident_offer_blocks_first_employee(self) -> None:
        self.session.scalar.side_effect = [None, uuid4()]
        with self.assertRaises(AccessRuleError) as caught:
            await self._approve()
        self.assertEqual(caught.exception.code, "role_conflict")
        self.session.add.assert_not_called()

    async def test_no_conflict_creates_company_and_bound_employee(self) -> None:
        employee = SimpleNamespace(id=uuid4(), kind="unassigned")
        self.session.scalar.side_effect = [None, None, employee]
        request, company = await self._approve()
        self.assertIs(request, self.request)
        self.assertIsInstance(company, Company)
        self.assertEqual(employee.kind, "employee")
        self.assertEqual(self.request.status, "closed")
        assignment = next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], StaffAssignment)
        )
        self.assertEqual(assignment.phone_number, "79990000001")
        self.assertEqual(assignment.user_id, employee.id)


if __name__ == "__main__":
    unittest.main()
