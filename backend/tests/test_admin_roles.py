"""Security boundaries for administrator and support role transitions."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.cli.bootstrap_admin import bootstrap_in_session
from src.db.models import AuditEvent, SupportInvitation, User
from src.domain.access.common import bind_staff_by_verified_phone
from src.domain.access.rules import AccessRuleError
from src.domain.admin.service import (
    accept_support_invitation,
    invite_support,
    pending_support_invitations,
    revoke_admin,
    revoke_support,
    revoke_support_invitation,
)


def rows(*values: object) -> SimpleNamespace:
    return SimpleNamespace(all=lambda: list(values))


def user(kind: str, *, phone: str | None = None, verified: bool = False) -> User:
    return User(
        id=uuid4(),
        max_user_id="123",
        kind=kind,
        phone_number=phone,
        phone_verified_at=datetime.now(UTC) if verified else None,
        version=1,
    )


def invitation(phone: str) -> SupportInvitation:
    return SupportInvitation(
        id=uuid4(), phone_number=phone, invited_by=uuid4(), version=1
    )


class AdministratorRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_phone_verification_without_staff_offer_is_allowed(
        self,
    ) -> None:
        admin = user("admin", phone="79990000001", verified=True)
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=admin),
            scalars=AsyncMock(return_value=rows()),
            flush=AsyncMock(),
        )
        self.assertEqual(await bind_staff_by_verified_phone(session, admin), [])
        self.assertEqual(admin.kind, "admin")
        session.flush.assert_not_awaited()

    async def test_only_current_admin_can_invite_support(self) -> None:
        stale_actor = user("admin")
        current = user("support")
        current.id = stale_actor.id
        session = SimpleNamespace(scalar=AsyncMock(return_value=current), add=Mock())
        with self.assertRaises(AccessRuleError) as raised:
            await invite_support(session, stale_actor, phone_number="+7 999 123-45-67")
        self.assertEqual(raised.exception.code, "admin_required")
        session.add.assert_not_called()

    async def test_invite_is_pending_and_uses_normalized_phone(self) -> None:
        admin = user("admin")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[admin, None, None, None, None]),
            execute=AsyncMock(),
            add=Mock(),
        )
        with patch(
            "src.domain.admin.service.commit_or_conflict", new_callable=AsyncMock
        ):
            offer = await invite_support(
                session, admin, phone_number="8 (999) 123-45-67"
            )
        self.assertEqual(offer.phone_number, "79991234567")
        self.assertIsNone(offer.accepted_at)
        self.assertIsNone(offer.accepted_by)
        added = [call.args[0] for call in session.add.call_args_list]
        self.assertIn(offer, added)
        self.assertEqual(
            [
                (event.entity_kind, event.action)
                for event in added
                if isinstance(event, AuditEvent)
            ],
            [("support_invitation", "created")],
        )

    async def test_invite_rejects_an_existing_resident(self) -> None:
        admin = user("admin")
        resident = user("resident", phone="79991234567", verified=True)
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[admin, resident]),
            execute=AsyncMock(),
            add=Mock(),
        )
        with self.assertRaises(AccessRuleError) as raised:
            await invite_support(session, admin, phone_number="79991234567")
        self.assertEqual(raised.exception.code, "role_conflict")
        session.add.assert_not_called()

    async def test_invite_rejects_pending_staff_assignment(self) -> None:
        admin = user("admin")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[admin, None, uuid4(), None]),
            execute=AsyncMock(),
            add=Mock(),
        )
        with self.assertRaises(AccessRuleError) as raised:
            await invite_support(session, admin, phone_number="79991234567")
        self.assertEqual(raised.exception.code, "role_conflict")
        session.add.assert_not_called()

    async def test_pending_offer_is_only_shown_after_verification(self) -> None:
        account = user("unassigned", phone="79991234567", verified=False)
        offer = invitation("79991234567")
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=account),
            scalars=AsyncMock(return_value=rows(offer)),
        )
        self.assertEqual(await pending_support_invitations(session, account), [])
        session.scalars.assert_not_awaited()
        account.phone_verified_at = datetime.now(UTC)
        self.assertEqual(await pending_support_invitations(session, account), [offer])

    async def test_acceptance_requires_matching_max_verified_phone(self) -> None:
        account = user("unassigned", phone="79990000001", verified=True)
        offer = invitation("79990000002")
        session = SimpleNamespace(
            get=AsyncMock(return_value=offer),
            scalar=AsyncMock(return_value=account),
            add=Mock(),
        )
        with self.assertRaises(AccessRuleError) as raised:
            await accept_support_invitation(session, account, invitation_id=offer.id)
        self.assertEqual(raised.exception.code, "not_found")
        self.assertEqual(account.kind, "unassigned")
        session.add.assert_not_called()

    async def test_unverified_recipient_cannot_accept_offer(self) -> None:
        account = user("unassigned", phone="79990000001")
        offer = invitation("79990000001")
        session = SimpleNamespace(
            get=AsyncMock(return_value=offer),
            scalar=AsyncMock(return_value=account),
            add=Mock(),
        )
        with self.assertRaises(AccessRuleError) as raised:
            await accept_support_invitation(session, account, invitation_id=offer.id)
        self.assertEqual(raised.exception.code, "phone_required")
        self.assertEqual(account.kind, "unassigned")
        session.add.assert_not_called()

    async def test_admin_cannot_accept_support_offer(self) -> None:
        account = user("admin", phone="79990000001", verified=True)
        offer = invitation("79990000001")
        session = SimpleNamespace(
            get=AsyncMock(return_value=offer),
            scalar=AsyncMock(side_effect=[account, offer]),
            add=Mock(),
        )
        with self.assertRaises(AccessRuleError) as raised:
            await accept_support_invitation(session, account, invitation_id=offer.id)
        self.assertEqual(raised.exception.code, "role_conflict")
        self.assertEqual(account.kind, "admin")

    async def test_acceptance_changes_role_only_after_explicit_call(self) -> None:
        account = user("unassigned", phone="79990000001", verified=True)
        offer = invitation("79990000001")
        session = SimpleNamespace(
            get=AsyncMock(return_value=offer),
            scalar=AsyncMock(side_effect=[account, offer]),
            add=Mock(),
        )
        with patch(
            "src.domain.admin.service.commit_or_conflict", new_callable=AsyncMock
        ):
            accepted = await accept_support_invitation(
                session, account, invitation_id=offer.id
            )
        self.assertIs(accepted, account)
        self.assertEqual(account.kind, "support")
        self.assertEqual(offer.accepted_by, account.id)
        self.assertIsNotNone(offer.accepted_at)
        added = [call.args[0] for call in session.add.call_args_list]
        self.assertEqual(
            {event.action for event in added if isinstance(event, AuditEvent)},
            {"accepted", "support_granted"},
        )

    async def test_revoke_support_changes_role_without_deleting_user(self) -> None:
        admin = user("admin")
        operator = user("support", phone="79990000001", verified=True)
        pending = invitation("79990000001")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[admin, operator]),
            scalars=AsyncMock(return_value=rows(pending)),
            add=Mock(),
        )
        with patch(
            "src.domain.admin.service.commit_or_conflict", new_callable=AsyncMock
        ):
            result = await revoke_support(session, admin, target_user_id=operator.id)
        self.assertIs(result, operator)
        self.assertEqual(operator.kind, "unassigned")
        self.assertEqual(pending.revoked_by, admin.id)
        self.assertIsNotNone(pending.revoked_at)
        self.assertTrue(
            any(
                isinstance(call.args[0], AuditEvent)
                and call.args[0].action == "support_revoked"
                for call in session.add.call_args_list
            )
        )

    async def test_admin_can_close_pending_invitation(self) -> None:
        admin = user("admin")
        offer = invitation("79990000001")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[admin, offer]), add=Mock()
        )
        with patch(
            "src.domain.admin.service.commit_or_conflict", new_callable=AsyncMock
        ):
            closed = await revoke_support_invitation(
                session, admin, invitation_id=offer.id
            )
        self.assertIs(closed, offer)
        self.assertEqual(offer.revoked_by, admin.id)
        self.assertIsNotNone(offer.revoked_at)

    async def test_last_admin_cannot_be_revoked(self) -> None:
        admin = user("admin")
        session = SimpleNamespace(
            scalars=AsyncMock(return_value=rows(admin)), add=Mock()
        )
        with self.assertRaises(AccessRuleError) as raised:
            await revoke_admin(session, admin, target_user_id=admin.id)
        self.assertEqual(raised.exception.code, "last_admin")
        self.assertEqual(admin.kind, "admin")
        statement = session.scalars.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("ORDER BY", sql)

    async def test_second_admin_can_be_revoked(self) -> None:
        admin = user("admin")
        target = user("admin")
        session = SimpleNamespace(
            scalars=AsyncMock(side_effect=[rows(admin, target), rows()]),
            add=Mock(),
        )
        with patch(
            "src.domain.admin.service.commit_or_conflict", new_callable=AsyncMock
        ):
            revoked = await revoke_admin(session, admin, target_user_id=target.id)
        self.assertIs(revoked, target)
        self.assertEqual(target.kind, "unassigned")


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_can_promote_support_with_audit(self) -> None:
        operator = user("support")
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=operator),
            add=Mock(),
            commit=AsyncMock(),
        )
        result = await bootstrap_in_session(session, "123")
        self.assertIs(result, operator)
        self.assertEqual(operator.kind, "admin")
        session.commit.assert_awaited_once()
        audit = next(
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], AuditEvent)
        )
        self.assertEqual(audit.action, "admin_bootstrapped")
        self.assertIsNone(audit.actor_user_id)

    async def test_bootstrap_rejects_resident_and_employee(self) -> None:
        for role in ("resident", "employee"):
            with self.subTest(role=role):
                account = user(role)
                session = SimpleNamespace(
                    scalar=AsyncMock(return_value=account),
                    add=Mock(),
                    commit=AsyncMock(),
                )
                with self.assertRaises(ValueError):
                    await bootstrap_in_session(session, "123")
                self.assertEqual(account.kind, role)
                session.commit.assert_not_awaited()

    async def test_bootstrap_rejects_noncanonical_max_id(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock(), add=Mock(), commit=AsyncMock())
        for max_id in ("0", "00123", "-123", "not-a-max-id"):
            with self.subTest(max_id=max_id), self.assertRaises(ValueError):
                await bootstrap_in_session(session, max_id)
        session.scalar.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
