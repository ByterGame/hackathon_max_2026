"""The temporary test bypass is server-gated and limited to the applicant."""

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.core.config import test_mode_enabled
from src.db.models import Company, House, StaffAssignment
from src.domain.access.rules import AccessRuleError
from src.domain.access.test_registration import activate_test_registration
from src.views.service.config import get_service_config


class TestModeConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_is_off_without_exact_server_flag(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(test_mode_enabled())
            self.assertFalse((await get_service_config()).test_mode)
        with patch.dict(os.environ, {"TEST_MODE": "true"}):
            self.assertFalse(test_mode_enabled())
        with patch.dict(os.environ, {"TEST_MODE": "1"}):
            self.assertTrue((await get_service_config()).test_mode)


class TestRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(
            id=uuid4(), kind="unassigned", phone_number="79990000001",
            phone_verified_at=datetime.now(UTC), version=1, updated_at=None,
        )
        self.request = SimpleNamespace(
            id=uuid4(), applicant_user_id=self.actor.id,
            phone_number="79990000001", proposed_company_name="Тестовая УК",
            status="open", outcome=None, decision_note=None,
            decided_by=None, decided_at=None, updated_at=None, version=1,
        )
        self.house_requests = [
            SimpleNamespace(
                id=uuid4(), registration_request_id=self.request.id,
                entered_address=f"ул. Пушкина, д. {number}",
                entrance_count=2, apartment_count=40,
                status="open", outcome=None, decision_note=None,
                decided_by=None, decided_at=None, updated_at=None, version=1,
                company_id=None, resolved_house_id=None,
            )
            for number in (5, 7)
        ]
        self.session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, None, None, None]),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: self.house_requests)),
            add=Mock(), flush=AsyncMock(),
        )

    async def test_disabled_mode_rejects_before_database_access(self) -> None:
        with (
            patch.dict(os.environ, {"TEST_MODE": "0"}),
            patch("src.domain.access.test_registration.require_row", new_callable=AsyncMock) as row,
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await activate_test_registration(
                    self.session, self.actor, request_id=self.request.id
                )
        self.assertEqual(caught.exception.status_code, 404)
        row.assert_not_awaited()

    async def test_only_applicant_can_activate(self) -> None:
        self.request.applicant_user_id = uuid4()
        with (
            patch.dict(os.environ, {"TEST_MODE": "1"}),
            patch(
                "src.domain.access.test_registration.require_row",
                new_callable=AsyncMock, return_value=self.request,
            ),
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await activate_test_registration(
                    self.session, self.actor, request_id=self.request.id
                )
        self.assertEqual(caught.exception.code, "forbidden")
        self.session.add.assert_not_called()

    async def test_first_employee_must_be_same_verified_number(self) -> None:
        self.request.phone_number = "79990000002"
        with (
            patch.dict(os.environ, {"TEST_MODE": "1"}),
            patch(
                "src.domain.access.test_registration.require_row",
                new_callable=AsyncMock, return_value=self.request,
            ),
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await activate_test_registration(
                    self.session, self.actor, request_id=self.request.id
                )
        self.assertEqual(caught.exception.code, "own_phone_required")
        self.session.add.assert_not_called()

    async def test_activation_creates_company_staff_and_houses_atomically(self) -> None:
        with (
            patch.dict(os.environ, {"TEST_MODE": "1"}),
            patch(
                "src.domain.access.test_registration.require_row",
                new_callable=AsyncMock,
                side_effect=[self.request, self.actor],
            ),
            patch(
                "src.domain.access.test_registration.lock_phone_role",
                new_callable=AsyncMock,
            ),
            patch("src.domain.access.test_registration.audit") as audit,
            patch(
                "src.domain.access.test_registration.commit_or_conflict",
                new_callable=AsyncMock,
            ) as commit,
        ):
            company, house_count = await activate_test_registration(
                self.session, self.actor, request_id=self.request.id
            )
        self.assertIsInstance(company, Company)
        self.assertEqual(house_count, 2)
        self.assertEqual(self.actor.kind, "employee")
        self.assertEqual(self.request.status, "closed")
        self.assertEqual(self.request.outcome, "approved")
        houses = [
            call.args[0] for call in self.session.add.call_args_list
            if isinstance(call.args[0], House)
        ]
        self.assertEqual(len(houses), 2)
        assignments = [
            call.args[0] for call in self.session.add.call_args_list
            if isinstance(call.args[0], StaffAssignment)
        ]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].user_id, self.actor.id)
        self.assertTrue(assignments[0].can_manage_staff)
        for house_request in self.house_requests:
            self.assertEqual(house_request.status, "closed")
            self.assertEqual(house_request.company_id, company.id)
            self.assertIsNotNone(house_request.resolved_house_id)
        self.assertEqual(audit.call_count, 3)
        commit.assert_awaited_once_with(self.session)

    async def test_duplicate_normalized_house_is_rejected_before_writes(self) -> None:
        self.house_requests[1].entered_address = "  УЛ. ПУШКИНА,   Д. 5 "
        with (
            patch.dict(os.environ, {"TEST_MODE": "1"}),
            patch(
                "src.domain.access.test_registration.require_row",
                new_callable=AsyncMock,
                side_effect=[self.request, self.actor],
            ),
            patch(
                "src.domain.access.test_registration.lock_phone_role",
                new_callable=AsyncMock,
            ),
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await activate_test_registration(
                    self.session, self.actor, request_id=self.request.id
                )
        self.assertEqual(caught.exception.code, "duplicate_house")
        self.session.add.assert_not_called()
