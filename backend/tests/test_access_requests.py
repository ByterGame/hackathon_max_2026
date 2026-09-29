"""Проверки переходов заявок без запуска PostgreSQL."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.domain.access.requests import request_cancellation, resolve_cancellation
from src.domain.access.rules import AccessRuleError


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.applicant_id = uuid4()
        self.employee_id = uuid4()
        self.request = SimpleNamespace(
            id=uuid4(),
            applicant_user_id=self.applicant_id,
            status="open",
            cancel_requested_by=None,
            cancel_requested_at=None,
            updated_at=None,
            version=1,
        )
        self.session = SimpleNamespace()

    async def test_author_cancels_open_resident_request_directly(self) -> None:
        actor = SimpleNamespace(id=self.applicant_id)
        with (
            patch(
                "src.domain.access.requests.load_request", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.access.requests.require_request_party",
                new_callable=AsyncMock,
            ),
            patch(
                "src.domain.access.requests.commit_or_conflict", new_callable=AsyncMock
            ),
            patch("src.domain.access.requests.audit"),
        ):
            load.return_value = self.request
            await request_cancellation(
                self.session, actor, kind="resident", request_id=self.request.id
            )
        self.assertEqual(self.request.status, "cancelled")
        self.assertIsNone(self.request.cancel_requested_by)

    async def test_after_review_author_needs_other_party_confirmation(self) -> None:
        self.request.status = "reviewing"
        actor = SimpleNamespace(id=self.applicant_id)
        with (
            patch(
                "src.domain.access.requests.load_request", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.access.requests.require_request_party",
                new_callable=AsyncMock,
            ),
            patch(
                "src.domain.access.requests.commit_or_conflict", new_callable=AsyncMock
            ),
            patch("src.domain.access.requests.audit"),
        ):
            load.return_value = self.request
            await request_cancellation(
                self.session, actor, kind="resident", request_id=self.request.id
            )
        self.assertEqual(self.request.status, "reviewing")
        self.assertEqual(self.request.cancel_requested_by, self.applicant_id)

    async def test_requester_cannot_confirm_own_cancellation(self) -> None:
        self.request.status = "reviewing"
        self.request.cancel_requested_by = self.applicant_id
        actor = SimpleNamespace(id=self.applicant_id)
        with (
            patch(
                "src.domain.access.requests.load_request", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.access.requests.require_request_party",
                new_callable=AsyncMock,
            ),
        ):
            load.return_value = self.request
            with self.assertRaises(AccessRuleError) as raised:
                await resolve_cancellation(
                    self.session,
                    actor,
                    kind="resident",
                    request_id=self.request.id,
                    accept=True,
                )
        self.assertEqual(raised.exception.code, "self_confirmation_forbidden")

    async def test_employee_accepts_applicant_cancellation(self) -> None:
        self.request.status = "reviewing"
        self.request.cancel_requested_by = self.applicant_id
        actor = SimpleNamespace(id=self.employee_id)
        with (
            patch(
                "src.domain.access.requests.load_request", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.access.requests.require_request_party",
                new_callable=AsyncMock,
            ),
            patch(
                "src.domain.access.requests.commit_or_conflict", new_callable=AsyncMock
            ),
            patch("src.domain.access.requests.audit"),
        ):
            load.return_value = self.request
            await resolve_cancellation(
                self.session,
                actor,
                kind="resident",
                request_id=self.request.id,
                accept=True,
            )
        self.assertEqual(self.request.status, "cancelled")
        self.assertIsNone(self.request.cancel_requested_by)


if __name__ == "__main__":
    unittest.main()
