"""Checks for identity and current staff rights before access mutations."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.domain.access.common import require_staff
from src.domain.access.company import create_company_registration
from src.domain.access.rules import AccessRuleError


class CompanyRegistrationGuardsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_applicant_cannot_register_company(self) -> None:
        actor = SimpleNamespace(
            id=uuid4(), kind="unassigned", phone_number=None, phone_verified_at=None
        )
        session = SimpleNamespace(add=Mock())
        with self.assertRaises(AccessRuleError) as raised:
            await create_company_registration(
                session,
                actor,
                phone_number="79991234567",
                proposed_company_name="УК Тест",
                free_text="Просим подключить компанию",
            )
        self.assertEqual(raised.exception.code, "phone_required")
        session.add.assert_not_called()

    async def test_verified_applicant_can_name_another_first_employee(self) -> None:
        actor = SimpleNamespace(
            id=uuid4(),
            kind="unassigned",
            phone_number="79990000001",
            phone_verified_at=datetime.now(UTC),
        )
        session = SimpleNamespace(add=Mock())
        with (
            patch("src.domain.access.company.audit"),
            patch(
                "src.domain.access.company.commit_or_conflict", new_callable=AsyncMock
            ),
        ):
            request = await create_company_registration(
                session,
                actor,
                phone_number="79990000002",
                proposed_company_name="УК Тест",
                free_text="Просим подключить компанию",
            )
        self.assertEqual(request.applicant_user_id, actor.id)
        self.assertEqual(request.phone_number, "79990000002")
        session.add.assert_called_once_with(request)


class StaffAuthorizationGuardsTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_assignment_is_locked_and_refreshed(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="employee")
        company_id = uuid4()
        assignment = SimpleNamespace(can_manage_residents=True)
        session = SimpleNamespace(scalar=AsyncMock(return_value=assignment))
        with patch(
            "src.domain.access.common.bind_staff_by_verified_phone", new_callable=AsyncMock
        ):
            result = await require_staff(
                session, actor, company_id, "can_manage_residents"
            )
        self.assertIs(result, assignment)
        statement = session.scalar.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE", sql)
        self.assertTrue(statement.get_execution_options()["populate_existing"])

    async def test_revoked_assignment_is_denied(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="employee")
        session = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with patch(
            "src.domain.access.common.bind_staff_by_verified_phone", new_callable=AsyncMock
        ):
            with self.assertRaises(AccessRuleError) as raised:
                await require_staff(session, actor, uuid4(), "can_manage_residents")
        self.assertEqual(raised.exception.code, "staff_required")


if __name__ == "__main__":
    unittest.main()
