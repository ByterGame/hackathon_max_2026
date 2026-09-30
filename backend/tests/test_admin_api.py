"""The administrator API exposes bounded reads and named, validated actions."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.domain.access.rules import AccessRuleError
from src.domain.access.common import lock_phone_role
from src.domain.access.requests import add_discussion_message as add_access_discussion
from src.domain.access.staff import assign_staff
from src.domain.access.resident import create_resident_offer
from src.domain.admin import admin_action, admin_get, admin_list
from src.domain.admin.read import _search_clause, _summary
from src.domain.issues.service import IssueError, add_comment, require_unmerged_card
from src.main import create_app


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="admin")
        self.session = SimpleNamespace(scalar=AsyncMock(), execute=AsyncMock())

    async def test_role_is_reread_before_listing(self) -> None:
        self.session.scalar.return_value = SimpleNamespace(
            id=self.actor.id, kind="support"
        )
        with self.assertRaises(AccessRuleError) as caught:
            await admin_list(self.session, self.actor, "users")
        self.assertEqual(caught.exception.code, "admin_required")

    async def test_list_rejects_unbounded_page(self) -> None:
        self.session.scalar.return_value = self.actor
        with self.assertRaises(AccessRuleError) as caught:
            await admin_list(self.session, self.actor, "users", limit=101)
        self.assertEqual(caught.exception.code, "invalid_page")

    async def test_list_rejects_incorrect_kind_filter(self) -> None:
        self.session.scalar.return_value = self.actor
        with self.assertRaises(AccessRuleError) as caught:
            await admin_list(self.session, self.actor, "users", kind="resident")
        self.assertEqual(caught.exception.code, "invalid_kind_filter")

    async def test_phone_role_lock_uses_one_bigint_key(self) -> None:
        await lock_phone_role(self.session, "79991234567")
        statement = self.session.execute.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("pg_advisory_xact_lock", str(compiled))
        self.assertEqual(compiled.params["phone_role_key"], 79991234567)

    async def test_combined_request_list_builds_postgres_query(self) -> None:
        self.session.scalar.side_effect = [self.actor, 0]
        self.session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        result = await admin_list(self.session, self.actor, "access_requests", limit=10)
        self.assertEqual(result["items"], [])
        query = self.session.execute.await_args.args[0]
        sql = str(query.compile(dialect=postgresql.dialect()))
        self.assertIn("UNION ALL", sql)
        self.assertIn("ORDER BY", sql)
        self.assertIn("LIMIT", sql)

    async def test_issue_detail_contains_targets_reports_messages_and_history(
        self,
    ) -> None:
        from src.db.models import IssueCard

        card = IssueCard(
            id=uuid4(),
            house_id=uuid4(),
            author_user_id=uuid4(),
            category_id=uuid4(),
            title="Лифт не работает",
            status="open",
            scope_all_house=True,
            version=1,
        )
        self.session.scalar.return_value = self.actor
        self.session.get = AsyncMock(return_value=card)
        self.session.scalars = AsyncMock(
            side_effect=[SimpleNamespace(all=lambda: []) for _ in range(4)]
        )
        self.session.execute.return_value = SimpleNamespace(all=lambda: [])
        detail = await admin_get(self.session, self.actor, "issues", card.id)
        self.assertEqual(detail["id"], str(card.id))
        self.assertEqual(detail["targets"], [])
        self.assertEqual(detail["reports"], [])
        self.assertEqual(detail["messages"], [])
        self.assertEqual(detail["history"], [])

    async def test_unknown_action_is_not_dispatched(self) -> None:
        with patch(
            "src.domain.admin.actions.require_admin", new_callable=AsyncMock
        ) as guard:
            guard.return_value = self.actor
            with self.assertRaises(AccessRuleError) as caught:
                await admin_action(
                    self.session,
                    self.actor,
                    "update_any_table",
                    {"table": "identity.users"},
                )
        self.assertEqual(caught.exception.code, "invalid_action")

    async def test_action_rejects_extra_fields(self) -> None:
        with (
            patch("src.domain.admin.actions.require_admin", new_callable=AsyncMock),
            patch(
                "src.domain.admin.actions.invite_support", new_callable=AsyncMock
            ) as invite,
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await admin_action(
                    self.session,
                    self.actor,
                    "invite_support",
                    {"phone_number": "79990000000", "kind": "admin"},
                )
        self.assertEqual(caught.exception.code, "invalid_payload")
        invite.assert_not_awaited()

    async def test_support_invitation_delegates_to_guarded_service(self) -> None:
        invitation_id = uuid4()
        with (
            patch(
                "src.domain.admin.actions.require_admin", new_callable=AsyncMock
            ) as guard,
            patch(
                "src.domain.admin.actions.invite_support", new_callable=AsyncMock
            ) as invite,
        ):
            guard.return_value = self.actor
            invite.return_value = SimpleNamespace(id=invitation_id)
            result = await admin_action(
                self.session,
                self.actor,
                "invite_support",
                {"phone_number": "+7 999 000-00-00"},
            )
        invite.assert_awaited_once_with(
            self.session, self.actor, phone_number="+7 999 000-00-00"
        )
        self.assertEqual(result["id"], str(invitation_id))
        self.assertEqual(result["entity"], "support_invites")

    async def test_access_discussion_keeps_admin_author(self) -> None:
        request_id = uuid4()
        with (
            patch(
                "src.domain.admin.actions.require_admin", new_callable=AsyncMock
            ) as guard,
            patch(
                "src.domain.admin.actions.add_discussion_message",
                new_callable=AsyncMock,
            ) as add,
        ):
            guard.return_value = self.actor
            add.return_value = (SimpleNamespace(id=request_id), uuid4())
            result = await admin_action(
                self.session,
                self.actor,
                "add_access_message",
                {"kind": "resident", "request_id": str(request_id), "text": "Проверю"},
            )
        add.assert_awaited_once_with(
            self.session,
            self.actor,
            kind="resident",
            request_id=request_id,
            text="Проверю",
        )
        self.assertEqual(result["id"], str(request_id))

    async def test_access_discussion_snapshots_admin_author_kind(self) -> None:
        request = SimpleNamespace(
            id=uuid4(), status="reviewing", discussion=[], version=1, updated_at=None
        )
        with (
            patch(
                "src.domain.access.requests.load_request", new_callable=AsyncMock
            ) as load,
            patch(
                "src.domain.access.requests.require_request_party",
                new_callable=AsyncMock,
            ),
            patch("src.domain.access.requests.audit"),
            patch(
                "src.domain.access.requests.commit_or_conflict",
                new_callable=AsyncMock,
            ),
        ):
            load.return_value = request
            await add_access_discussion(
                self.session,
                self.actor,
                kind="resident",
                request_id=request.id,
                text="Проверю обращение",
            )
        self.assertEqual(request.discussion[0]["author_user_id"], str(self.actor.id))
        self.assertEqual(request.discussion[0]["author_kind"], "admin")

    async def test_admin_cannot_impersonate_uk_in_issue_comments(self) -> None:
        with self.assertRaises(IssueError) as caught:
            await add_comment(self.session, self.actor, uuid4(), "Ответ")
        self.assertEqual(caught.exception.code, "admin_comment_unavailable")

    async def test_merged_source_is_not_silently_edited_via_primary(self) -> None:
        source_id = uuid4()
        with patch(
            "src.domain.issues.service._lock_issue_house", new_callable=AsyncMock
        ) as lock:
            lock.return_value = SimpleNamespace(id=uuid4())
            with self.assertRaises(IssueError) as caught:
                await require_unmerged_card(self.session, source_id)
        self.assertEqual(caught.exception.code, "issue_merged")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_admin_status_action_refuses_merged_source(self) -> None:
        source_id = uuid4()
        with (
            patch(
                "src.domain.admin.actions.require_admin", new_callable=AsyncMock
            ) as admin,
            patch(
                "src.domain.admin.actions.require_unmerged_card",
                new_callable=AsyncMock,
            ) as check,
            patch(
                "src.domain.admin.actions.set_status", new_callable=AsyncMock
            ) as status,
        ):
            admin.return_value = self.actor
            check.side_effect = IssueError("issue_merged", "Откройте основную", 409)
            with self.assertRaises(AccessRuleError) as caught:
                await admin_action(
                    self.session,
                    self.actor,
                    "set_issue_status",
                    {"card_id": str(source_id), "status": "in_progress"},
                )
        self.assertEqual(caught.exception.code, "issue_merged")
        status.assert_not_awaited()

    async def test_staff_assignment_rejects_pending_support_invite(self) -> None:
        self.session.scalar.side_effect = [uuid4(), None]
        with (
            patch("src.domain.access.staff.require_row", new_callable=AsyncMock),
            patch("src.domain.access.staff.require_staff", new_callable=AsyncMock),
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await assign_staff(
                    self.session,
                    self.actor,
                    company_id=uuid4(),
                    phone_number="79991234567",
                    can_manage_staff=False,
                    can_manage_residents=False,
                    can_manage_issues=True,
                )
        self.assertEqual(caught.exception.code, "role_conflict")

    async def test_resident_offer_rejects_pending_support_invite(self) -> None:
        apartment = SimpleNamespace(id=uuid4())
        self.session.scalar.side_effect = [apartment, uuid4(), None]
        house = SimpleNamespace(id=uuid4(), company_id=uuid4(), entrance_count=3, apartment_count=40)
        with (
            patch(
                "src.domain.access.resident.require_row", new_callable=AsyncMock
            ) as get_house,
            patch("src.domain.access.resident.require_staff", new_callable=AsyncMock),
        ):
            get_house.return_value = house
            with self.assertRaises(AccessRuleError) as caught:
                await create_resident_offer(
                    self.session,
                    self.actor,
                    house_id=house.id,
                    entrance_number=1,
                    apartment_number=2,
                    phone_number="79991234567",
                    valid_to=None,
                )
        self.assertEqual(caught.exception.code, "role_conflict")


class AdminShapeTests(unittest.TestCase):
    def test_manual_routes_are_in_openapi(self) -> None:
        paths = create_app().openapi()["paths"]
        self.assertIn("/admin/list", paths)
        self.assertIn("/admin/action", paths)
        self.assertIn("/admin/support-invites/mine", paths)
        self.assertIn("/admin/support-invites/accept", paths)

    def test_request_summary_identifies_kind_and_discussion(self) -> None:
        from src.db.models import ResidentRequest

        row_id = uuid4()
        row = ResidentRequest(
            id=row_id,
            applicant_user_id=uuid4(),
            house_id=uuid4(),
            submitted_full_name="Иван Иванов",
            submitted_entrance_number=1,
            submitted_apartment_number=4,
            status="reviewing",
            discussion=[{"author_user_id": str(uuid4()), "text": "Уточните квартиру"}],
            version=1,
        )
        item = _summary("access_requests", row, request_kind="resident")
        self.assertEqual(item["id"], str(row_id))
        self.assertEqual(item["kind"], "resident")
        self.assertEqual(item["data"]["discussion"][0]["text"], "Уточните квартиру")

    def test_user_summary_uses_name_and_keeps_identity_visible(self) -> None:
        from src.db.models import User

        row = User(
            id=uuid4(),
            max_user_id="429817952",
            phone_number="79991234567",
            full_name="Анна",
            max_username="annamax",
            kind="resident",
        )
        item = _summary("users", row)
        self.assertEqual(item["title"], "Анна")
        self.assertEqual(item["subtitle"], "79991234567")
        self.assertEqual(item["data"]["max_username"], "annamax")
        search_sql = str(_search_clause("users", User, "annamax"))
        self.assertIn("max_username", search_sql)


if __name__ == "__main__":
    unittest.main()
