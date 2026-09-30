"""An explicit full name is shared by all homes, while closed requests keep history."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import AuditEvent, ResidentRequest, User
from src.domain.access.resident import create_resident_request, update_resident_request
from src.domain.access.queries import list_residents
from src.domain.access.rules import AccessRuleError
from src.domain.profile import (
    has_confirmed_full_name,
    normalize_full_name,
    resolve_request_full_name,
    update_profile_name,
)


class ProfileFullNameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user = User(
            id=uuid4(), full_name="Имя из MAX", full_name_is_manual=True,
            full_name_confirmed_at=None, version=2,
        )
        self.open_request = ResidentRequest(
            id=uuid4(), applicant_user_id=self.user.id,
            submitted_full_name="Старое ФИО", status="open", version=1,
        )
        self.closed_request = ResidentRequest(
            id=uuid4(), applicant_user_id=self.user.id,
            submitted_full_name="Историческое ФИО", status="closed", version=1,
        )
        self.session = SimpleNamespace(
            scalar=AsyncMock(return_value=self.user),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [self.open_request])),
            add=Mock(), commit=AsyncMock(), rollback=AsyncMock(),
        )

    def test_name_is_normalized_and_bounded(self) -> None:
        self.assertEqual(normalize_full_name("  Иван  Иванов \n"), "Иван Иванов")
        for value in (" ", "а" * 256):
            with self.assertRaises(AccessRuleError):
                normalize_full_name(value)

    def test_confirmation_requires_both_marker_and_manual_priority(self) -> None:
        self.user.full_name_confirmed_at = datetime.now(UTC)
        self.user.full_name_is_manual = False
        self.assertFalse(has_confirmed_full_name(self.user))
        self.user.full_name_is_manual = True
        self.assertTrue(has_confirmed_full_name(self.user))

    async def test_legacy_manual_flag_alone_does_not_confirm_name(self) -> None:
        with self.assertRaises(AccessRuleError) as error:
            await resolve_request_full_name(self.session, self.user, None)
        self.assertEqual(error.exception.code, "name_confirmation_required")
        self.session.scalars.assert_not_awaited()

    async def test_first_explicit_submission_confirms_profile_and_syncs_open_request(self) -> None:
        name = await resolve_request_full_name(
            self.session, self.user, "  Иван  Иванов  "
        )
        self.assertEqual(name, "Иван Иванов")
        self.assertEqual(self.user.full_name, "Иван Иванов")
        self.assertTrue(self.user.full_name_is_manual)
        self.assertIsNotNone(self.user.full_name_confirmed_at)
        self.assertEqual(self.open_request.submitted_full_name, "Иван Иванов")
        self.assertEqual(self.open_request.version, 2)
        self.assertEqual(self.closed_request.submitted_full_name, "Историческое ФИО")
        events = [call.args[0] for call in self.session.add.call_args_list]
        self.assertEqual(
            {(event.entity_kind, event.action) for event in events if isinstance(event, AuditEvent)},
            {("user", "full_name_confirmed"), ("resident_request", "profile_name_synced")},
        )

    async def test_later_request_reuses_profile_and_rejects_different_name(self) -> None:
        self.user.full_name = "Иван Иванов"
        self.user.full_name_confirmed_at = datetime.now(UTC)
        self.assertEqual(
            await resolve_request_full_name(self.session, self.user, None),
            "Иван Иванов",
        )
        with self.assertRaises(AccessRuleError) as error:
            await resolve_request_full_name(self.session, self.user, "Пётр Петров")
        self.assertEqual(error.exception.code, "profile_name_mismatch")
        self.session.scalars.assert_not_awaited()

    async def test_explicit_update_changes_account_and_unfinished_requests_atomically(self) -> None:
        self.user.full_name = "Иван Иванов"
        self.user.full_name_confirmed_at = datetime.now(UTC)
        with patch("src.domain.profile.commit_or_conflict", new_callable=AsyncMock) as commit:
            result = await update_profile_name(
                self.session, self.user, full_name="Пётр Петров"
            )
        self.assertIs(result, self.user)
        self.assertEqual(self.user.full_name, "Пётр Петров")
        self.assertEqual(self.open_request.submitted_full_name, "Пётр Петров")
        self.assertEqual(self.closed_request.submitted_full_name, "Историческое ФИО")
        commit.assert_awaited_once_with(self.session)

    async def test_first_application_saves_confirmed_name_snapshot(self) -> None:
        self.user.kind = "unassigned"
        self.user.phone_number = "79990000000"
        self.user.phone_verified_at = datetime.now(UTC)
        house_id = uuid4()
        self.session.scalar.side_effect = [self.user, None]
        with (
            patch("src.domain.access.resident.require_row", new_callable=AsyncMock) as require,
            patch("src.domain.access.resident.commit_or_conflict", new_callable=AsyncMock) as commit,
            patch("src.domain.access.resident.audit"),
        ):
            require.return_value = SimpleNamespace(id=house_id, archived_at=None, entrance_count=5)
            request = await create_resident_request(
                self.session, self.user,
                house_id=house_id, entrance_number=2, apartment_number=12, full_name="Иван Иванов",
            )
        self.assertEqual(request.submitted_full_name, "Иван Иванов")
        self.assertEqual(self.user.full_name, "Иван Иванов")
        self.assertIsNotNone(self.user.full_name_confirmed_at)
        commit.assert_awaited_once_with(self.session)

    async def test_edit_application_changes_apartment_but_not_name_snapshot(self) -> None:
        self.user.full_name = "Иван Иванов"
        self.user.full_name_confirmed_at = datetime.now(UTC)
        self.open_request.house_id = uuid4()
        self.open_request.submitted_apartment_number = 12
        self.open_request.submitted_entrance_number = None
        with (
            patch("src.domain.access.resident.require_row", new_callable=AsyncMock) as require,
            patch("src.domain.access.resident.commit_or_conflict", new_callable=AsyncMock),
            patch("src.domain.access.resident.audit") as audit,
        ):
            require.side_effect = [
                self.open_request,
                SimpleNamespace(id=self.open_request.house_id, entrance_count=5),
            ]
            await update_resident_request(
                self.session, self.user,
                request_id=self.open_request.id, entrance_number=3, apartment_number=24,
            )
        self.assertEqual(self.open_request.submitted_apartment_number, 24)
        self.assertEqual(self.open_request.submitted_full_name, "Старое ФИО")
        self.assertNotIn("full_name", audit.call_args.kwargs["after"])

    async def test_staff_resident_list_shows_current_confirmed_name_not_closed_snapshot(self) -> None:
        self.user.full_name = "Новое ФИО"
        self.user.full_name_confirmed_at = datetime.now(UTC)
        grant = SimpleNamespace(
            id=uuid4(), revoked_at=None, valid_to=None, valid_from=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        apartment = SimpleNamespace(entrance_number=None, apartment_number=12)
        house = SimpleNamespace(id=uuid4(), address_display="Пушкина, 5")
        request = SimpleNamespace(
            submitted_full_name="Историческое ФИО", decided_by=uuid4(),
            decided_at=datetime.now(UTC), decision_note="Проверено",
        )
        self.session.execute = AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(grant, apartment, house, self.user, request, None)]
            )
        )
        with patch("src.domain.access.queries.require_staff", new_callable=AsyncMock):
            rows = await list_residents(
                self.session, self.user, company_id=uuid4(), house_id=house.id
            )
        self.assertEqual(rows[0]["full_name"], "Новое ФИО")
        self.assertEqual(request.submitted_full_name, "Историческое ФИО")


if __name__ == "__main__":
    unittest.main()
