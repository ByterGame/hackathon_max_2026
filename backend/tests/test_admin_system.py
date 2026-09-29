"""System-editor boundaries: typed patches, identity safety, and durable ledger."""

import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import AdminOperation, AuditEvent, File, IssueSupport, OutboxEvent, ResidentGrant, StaffAssignment, User
from src.domain.access.rules import AccessRuleError
from src.domain.admin.system import (
    CascadeNode,
    CascadePlan,
    _cascade_plan,
    _check_etag,
    _delete_order,
    _item,
    _lock_cascade_tables,
    _model,
    _parse_row_key,
    _registry,
    _related_rows,
    _row_data,
    _schema_entry,
    _soft_delete,
    _validate_user_change,
    cleanup_operation_files,
    system_delete,
    system_patch,
)
from src.main import create_app


def user(kind: str = "admin") -> User:
    return User(
        id=uuid4(),
        max_user_id="12345",
        phone_number="79991234567",
        phone_verified_at=datetime.now(UTC),
        kind=kind,
        version=1,
    )


class SystemShapeTests(unittest.TestCase):
    def test_registry_covers_data_and_both_journals(self) -> None:
        entities = _registry()
        self.assertIn("identity.users", entities)
        self.assertIn("system.audit_events", entities)
        self.assertIn("issues.supports", entities)
        self.assertIn("system.bot_dialogs", entities)
        self.assertIn("system.admin_operations", entities)
        self.assertIs(_model("system.audit_events"), AuditEvent)
        self.assertIs(_model("system.admin_operations"), AdminOperation)
        ledger_schema = _schema_entry("system.admin_operations", AdminOperation)
        self.assertEqual(ledger_schema["delete_modes"], ["hard"])
        self.assertTrue(next(field for field in ledger_schema["fields"] if field["name"] == "reason")["editable"])

    def test_composite_key_and_clear_only_schema(self) -> None:
        first, second = uuid4(), uuid4()
        self.assertEqual(
            _parse_row_key(IssueSupport, f"{first}:{second}"),
            (first, second),
        )
        with self.assertRaises(AccessRuleError):
            _parse_row_key(IssueSupport, str(first))
        fields = _schema_entry("identity.users", User)["fields"]
        verified = next(item for item in fields if item["name"] == "phone_verified_at")
        self.assertTrue(verified["clear_only"])
        self.assertTrue(verified["editable"])

    def test_file_storage_key_and_primary_key_are_not_editable(self) -> None:
        fields = _schema_entry("system.files", File)["fields"]
        by_name = {item["name"]: item for item in fields}
        self.assertFalse(by_name["id"]["editable"])
        self.assertFalse(by_name["storage_key"]["editable"])
        self.assertFalse(by_name["sha256"]["editable"])
        self.assertEqual(_schema_entry("system.files", File)["delete_mode"], "soft")
        self.assertEqual(_schema_entry("system.files", File)["delete_modes"], ["soft", "hard"])

    def test_system_routes_are_in_openapi(self) -> None:
        paths = create_app().openapi()["paths"]
        for action in ("schema", "list", "get", "operations", "patch", "delete", "cleanup"):
            self.assertIn(f"/admin/system/{action}", paths)

    def test_stale_etag_rejected(self) -> None:
        row = user("unassigned")
        with self.assertRaises(AccessRuleError) as caught:
            _check_etag(row, "0" * 64)
        self.assertEqual(caught.exception.code, "stale_record")


class SystemMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_correct_audit_event_with_database_guard_enabled(self) -> None:
        admin = user()
        row = AuditEvent(
            id=uuid4(), entity_kind="user", entity_id=uuid4(),
            action="created", actor_user_id=admin.id,
        )
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
            commit=AsyncMock(), add=Mock(), no_autoflush=nullcontext(),
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ):
            result = await system_patch(
                session, admin, "system.audit_events", str(row.id),
                _item(row)["etag"], "Исправлено ошибочное действие",
                {"action": "updated"},
            )
        self.assertEqual(result["item"]["data"]["action"], "updated")
        self.assertEqual(session.execute.await_count, 2)
        self.assertIn("set_config", str(session.execute.await_args_list[1].args[0]))
        self.assertEqual(session.add.call_args.args[0].entity_key, "system.audit_events")

    async def test_admin_can_correct_journal_entry_and_correction_is_logged(self) -> None:
        admin = user()
        row = AdminOperation(
            id=uuid4(), entity_key="identity.users", row_key=str(uuid4()),
            operation="patch", actor_user_id=admin.id, reason="Опечатка в причине",
            expected_etag="0" * 64, before_data={"kind": "support"},
            after_data={"kind": "admin"},
        )
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
            commit=AsyncMock(), add=Mock(), no_autoflush=nullcontext(),
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ):
            result = await system_patch(
                session, admin, "system.admin_operations", str(row.id),
                _item(row)["etag"], "Уточнена причина исправления журнала",
                {"reason": "Проверенная причина"},
            )
        self.assertEqual(result["item"]["data"]["reason"], "Проверенная причина")
        self.assertEqual(session.execute.await_count, 2)
        self.assertIn("set_config", str(session.execute.await_args_list[1].args[0]))
        correction = session.add.call_args.args[0]
        self.assertEqual(correction.entity_key, "system.admin_operations")
        self.assertEqual(correction.row_key, str(row.id))
        self.assertEqual(correction.operation, "patch")
        session.commit.assert_awaited_once()

    async def test_admin_can_delete_journal_entry_and_deletion_is_logged(self) -> None:
        admin = user()
        row = AdminOperation(
            id=uuid4(), entity_key="identity.users", row_key=str(uuid4()),
            operation="patch", actor_user_id=admin.id, reason="Ошибочная запись",
            expected_etag="0" * 64, before_data={}, after_data={},
        )
        session = SimpleNamespace(
            execute=AsyncMock(), delete=AsyncMock(), flush=AsyncMock(),
            commit=AsyncMock(), add=Mock(),
        )
        key = ("system.admin_operations", str(row.id))
        plan = CascadePlan(
            nodes={key: CascadeNode(*key, row)}, delete_order=[key],
            preview={"hash": "a" * 64, "blockers": [], "total": 1,
                     "counts": [{"entity": key[0], "count": 1}],
                     "rows": [{"entity": key[0], "id": key[1], "via": []}],
                     "updates": [], "files": []},
            role_updates=[], private_files=[],
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ), patch(
            "src.domain.admin.system._cascade_plan", new_callable=AsyncMock, return_value=plan
        ), patch(
            "src.domain.admin.system._lock_cascade_tables", new_callable=AsyncMock
        ):
            result = await system_delete(
                session, admin, "system.admin_operations", str(row.id),
                _item(row)["etag"], "Удалена ошибочная запись журнала", "hard", "a" * 64,
            )
        self.assertEqual(result["mode"], "hard")
        self.assertEqual(result["deleted"], 1)
        self.assertIn("set_config", str(session.execute.await_args_list[-1].args[0]))
        session.delete.assert_awaited_once_with(row)
        deletion = session.add.call_args.args[0]
        self.assertEqual(deletion.entity_key, "system.admin_operations")
        self.assertEqual(deletion.row_key, str(row.id))
        self.assertEqual(deletion.operation, "hard_delete")
        session.commit.assert_awaited_once()

    async def test_phone_change_clears_verification(self) -> None:
        row = user("support")
        session = SimpleNamespace(scalar=AsyncMock(return_value=0))
        values = await _validate_user_change(
            session, row, {"phone_number": "+7 (999) 000-00-00"}, actor_id=uuid4()
        )
        self.assertEqual(values["phone_number"], "79990000000")
        self.assertIsNone(values["phone_verified_at"])

    async def test_max_id_change_clears_phone_and_cannot_target_self(self) -> None:
        row = user("admin")
        session = SimpleNamespace(scalar=AsyncMock(return_value=0))
        with self.assertRaises(AccessRuleError) as caught:
            await _validate_user_change(
                session, row, {"max_user_id": "54321"}, actor_id=row.id
            )
        self.assertEqual(caught.exception.code, "self_identity_change")
        values = await _validate_user_change(
            session, row, {"max_user_id": "54321"}, actor_id=uuid4()
        )
        self.assertIsNone(values["phone_number"])
        self.assertIsNone(values["phone_verified_at"])

    async def test_verification_cannot_be_forged(self) -> None:
        row = user("unassigned")
        with self.assertRaises(AccessRuleError) as caught:
            await _validate_user_change(
                SimpleNamespace(), row, {"phone_verified_at": datetime.now(UTC)}, actor_id=uuid4()
            )
        self.assertEqual(caught.exception.code, "verification_forbidden")

    async def test_last_admin_cannot_be_demoted_by_system_patch(self) -> None:
        row = user("admin")
        session = SimpleNamespace(scalar=AsyncMock(return_value=1))
        with self.assertRaises(AccessRuleError) as caught:
            await _validate_user_change(
                session, row, {"kind": "unassigned"}, actor_id=uuid4()
            )
        self.assertEqual(caught.exception.code, "last_admin")

    async def test_admin_requires_login_id(self) -> None:
        row = user("unassigned")
        row.max_user_id = None
        session = SimpleNamespace(scalar=AsyncMock(return_value=0))
        with self.assertRaises(AccessRuleError) as caught:
            await _validate_user_change(session, row, {"kind": "admin"}, actor_id=uuid4())
        self.assertEqual(caught.exception.code, "admin_login_required")

    async def test_patch_writes_ledger_in_same_session(self) -> None:
        row = user("support")
        admin = user("admin")
        session = SimpleNamespace(
            execute=AsyncMock(),
            scalars=AsyncMock(return_value=SimpleNamespace(all=Mock(return_value=[]))),
            scalar=AsyncMock(return_value=0),
            flush=AsyncMock(),
            refresh=AsyncMock(),
            commit=AsyncMock(),
            add=Mock(),
            no_autoflush=nullcontext(),
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock) as guard, patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock
        ) as load:
            guard.return_value = admin
            load.return_value = row
            result = await system_patch(
                session,
                admin,
                "identity.users",
                str(row.id),
                _item(row)["etag"],
                "Исправлен номер после проверки",
                {"phone_number": "79990000000"},
            )
        self.assertEqual(result["item"]["data"]["phone_number"], "79990000000")
        self.assertIsNone(row.phone_verified_at)
        added = [call.args[0] for call in session.add.call_args_list]
        self.assertEqual(len(added), 1)
        self.assertIsInstance(added[0], AdminOperation)
        self.assertEqual(added[0].operation, "patch")
        session.commit.assert_awaited_once()

    async def test_hard_delete_refuses_missing_or_stale_preview_without_deleting(self) -> None:
        row = AuditEvent(
            id=uuid4(), entity_kind="user", entity_id=uuid4(), action="created"
        )
        admin = user()
        session = SimpleNamespace(delete=AsyncMock(), execute=AsyncMock())
        plan = SimpleNamespace(preview={"hash": "b" * 64, "blockers": []})
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ), patch(
            "src.domain.admin.system._cascade_plan", new_callable=AsyncMock, return_value=plan
        ), patch(
            "src.domain.admin.system._lock_cascade_tables", new_callable=AsyncMock
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await system_delete(
                    session, admin, "system.audit_events", str(row.id), _item(row)["etag"],
                    "Исправление ошибочной записи", "hard"
                )
            self.assertEqual(caught.exception.code, "preview_required")
            with self.assertRaises(AccessRuleError) as caught:
                await system_delete(
                    session, admin, "system.audit_events", str(row.id), _item(row)["etag"],
                    "Исправление ошибочной записи", "hard", "a" * 64
                )
        self.assertEqual(caught.exception.code, "preview_stale")
        session.delete.assert_not_awaited()

    async def test_file_hard_delete_requires_precise_preview(self) -> None:
        row = File(
            id=uuid4(), storage_key="a" * 64, uploader_user_id=uuid4(),
            original_name="a.pdf", mime_type="application/pdf", size_bytes=1,
            sha256="0" * 64, state="ready",
        )
        admin = user()
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock) as guard, patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock
        ) as load, patch("src.domain.admin.system._lock_cascade_tables", new_callable=AsyncMock):
            guard.return_value = admin
            load.return_value = row
            with self.assertRaises(AccessRuleError) as caught:
                await system_delete(
                    SimpleNamespace(execute=AsyncMock()), admin, "system.files", str(row.id), _item(row)["etag"],
                    "Удалить вложение навсегда", "hard"
                )
        self.assertEqual(caught.exception.code, "preview_required")

    async def test_soft_staff_revocation_drops_last_employee_role(self) -> None:
        employee = user("employee")
        assignment = StaffAssignment(
            id=uuid4(), company_id=uuid4(), phone_number=employee.phone_number,
            user_id=employee.id, can_manage_staff=True,
            can_manage_residents=False, can_manage_issues=False,
            granted_by=uuid4(), version=1,
        )
        session = SimpleNamespace(scalar=AsyncMock(side_effect=[employee, None]))
        side_effects = await _soft_delete(
            session, assignment, "identity.staff_assignments", user(), "Отзыв доступа"
        )
        self.assertIsNotNone(assignment.revoked_at)
        self.assertEqual(employee.kind, "unassigned")
        self.assertEqual(side_effects[0]["after_kind"], "unassigned")

    async def test_staff_transfer_reconciles_previous_and_new_user(self) -> None:
        old_user_id, new_user_id = uuid4(), uuid4()
        assignment = StaffAssignment(
            id=uuid4(), company_id=uuid4(), phone_number="79991234567",
            user_id=old_user_id, can_manage_staff=False,
            can_manage_residents=False, can_manage_issues=False,
            granted_by=uuid4(), version=1,
        )
        admin = user()
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
            commit=AsyncMock(), add=Mock(), no_autoflush=nullcontext(),
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=assignment
        ), patch(
            "src.domain.admin.system._reconcile_staff_role", new_callable=AsyncMock
        ) as reconcile, patch(
            "src.domain.admin.system._validate_staff_change", new_callable=AsyncMock
        ):
            await system_patch(
                session, admin, "identity.staff_assignments", str(assignment.id),
                _item(assignment)["etag"], "Исправлена привязка сотрудника",
                {"user_id": str(new_user_id), "phone_number": "79998887766"},
            )
        self.assertEqual(reconcile.await_count, 2)
        self.assertEqual(reconcile.await_args_list[0].kwargs, {
            "user_id": old_user_id, "force_inactive": True,
        })
        self.assertEqual(reconcile.await_args_list[1].kwargs, {})

    async def test_resident_transfer_reconciles_previous_and_new_user(self) -> None:
        old_user_id, new_user_id = uuid4(), uuid4()
        grant = ResidentGrant(
            id=uuid4(), user_id=old_user_id, apartment_id=uuid4(),
            valid_from=datetime.now(UTC), source_request_id=uuid4(),
            granted_by=uuid4(),
        )
        admin = user()
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
            commit=AsyncMock(), add=Mock(), no_autoflush=nullcontext(),
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=grant
        ), patch(
            "src.domain.admin.system._reconcile_resident_role", new_callable=AsyncMock
        ) as reconcile:
            await system_patch(
                session, admin, "access.resident_grants", str(grant.id),
                _item(grant)["etag"], "Исправлена привязка жильца",
                {"user_id": str(new_user_id)},
            )
        self.assertEqual(reconcile.await_count, 2)
        self.assertEqual(reconcile.await_args_list[0].kwargs, {
            "user_id": old_user_id, "force_inactive": True,
        })
        self.assertEqual(reconcile.await_args_list[1].kwargs, {})

    async def test_hard_staff_delete_records_role_change(self) -> None:
        employee = user("employee")
        assignment = StaffAssignment(
            id=uuid4(), company_id=uuid4(), phone_number=employee.phone_number,
            user_id=employee.id, can_manage_staff=False,
            can_manage_residents=False, can_manage_issues=False,
            granted_by=uuid4(), version=1,
        )
        admin = user()
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), delete=AsyncMock(),
            commit=AsyncMock(), add=Mock(),
        )
        key = ("identity.staff_assignments", str(assignment.id))
        plan = CascadePlan(
            nodes={key: CascadeNode(*key, assignment)}, delete_order=[key],
            preview={"hash": "a" * 64, "blockers": [], "total": 1,
                     "counts": [{"entity": key[0], "count": 1}],
                     "rows": [{"entity": key[0], "id": key[1], "via": []}],
                     "updates": [{"entity": "identity.users", "id": str(employee.id),
                                  "field": "kind", "before": "employee", "after": "unassigned"}],
                     "files": []},
            role_updates=[(employee, "unassigned")], private_files=[],
        )
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=assignment
        ), patch(
            "src.domain.admin.system._cascade_plan", new_callable=AsyncMock, return_value=plan
        ), patch(
            "src.domain.admin.system._lock_cascade_tables", new_callable=AsyncMock
        ):
            await system_delete(
                session, admin, "identity.staff_assignments", str(assignment.id),
                _item(assignment)["etag"], "Удалено ошибочное назначение", "hard", "a" * 64,
            )
        self.assertEqual(session.add.call_args.args[0].after_data["cascade"]["updates"], plan.preview["updates"])
        self.assertEqual(employee.kind, "unassigned")
        session.delete.assert_awaited_once()

    async def test_cascade_preview_deduplicates_shared_children_and_orders_them_first(self) -> None:
        admin = user()
        target = user("unassigned")
        target.id = uuid4()
        audit = AuditEvent(
            id=uuid4(), entity_kind="user", entity_id=target.id,
            actor_user_id=target.id, action="created",
        )

        async def related(_session, node, _incoming, *, for_update):
            if node.entity == "identity.users":
                return [
                    ("system.audit_events", audit, "foreign_key", "actor_user_id"),
                    ("system.audit_events", audit, "polymorphic", "entity_kind/entity_id"),
                ]
            return []

        with patch("src.domain.admin.system._related_rows", side_effect=related), patch(
            "src.domain.admin.system._planned_role_updates", new_callable=AsyncMock, return_value=[]
        ):
            plan = await _cascade_plan(SimpleNamespace(), admin, "identity.users", target, for_update=False)
        self.assertEqual(plan.preview["total"], 2)
        self.assertEqual(plan.preview["counts"], [
            {"entity": "identity.users", "count": 1},
            {"entity": "system.audit_events", "count": 1},
        ])
        self.assertEqual(plan.delete_order, [
            ("system.audit_events", str(audit.id)), ("identity.users", str(target.id)),
        ])
        audit_preview = next(row for row in plan.preview["rows"] if row["entity"] == "system.audit_events")
        self.assertEqual({edge["kind"] for edge in audit_preview["via"]}, {"foreign_key", "polymorphic"})

    async def test_user_cascade_includes_outbox_actor_reference(self) -> None:
        target = user("unassigned")
        event = OutboxEvent(
            id=uuid4(), event_kind="issues.card.opened", subject_kind="issue_card",
            subject_id=uuid4(), payload={"actor_user_id": str(target.id)},
        )

        async def scalars(statement):
            values = statement.compile().params.values()
            return SimpleNamespace(all=lambda: [event] if "actor_user_id" in values else [])

        session = SimpleNamespace(scalars=AsyncMock(side_effect=scalars))
        node = CascadeNode("identity.users", str(target.id), target)
        related = await _related_rows(session, node, {}, for_update=False)
        self.assertIn(
            ("system.outbox_events", event, "polymorphic", "payload.actor_user_id"),
            related,
        )

    async def test_cascade_preview_blocks_cycles_and_own_admin(self) -> None:
        actor = user()

        async def self_reference(_session, node, _incoming, *, for_update):
            return [("identity.users", actor, "polymorphic", "entity_key/row_key")]

        with patch("src.domain.admin.system._related_rows", side_effect=self_reference), patch(
            "src.domain.admin.system._planned_role_updates", new_callable=AsyncMock, return_value=[]
        ):
            plan = await _cascade_plan(
                SimpleNamespace(scalar=AsyncMock(return_value=1)),
                actor, "identity.users", actor, for_update=False,
            )
        self.assertEqual(plan.preview["total"], 1)
        self.assertTrue(any("цикл" in reason for reason in plan.preview["blockers"]))
        self.assertTrue(any("собственный аккаунт" in reason for reason in plan.preview["blockers"]))
        self.assertTrue(any("последнего администратора" in reason for reason in plan.preview["blockers"]))
        order, cycle = _delete_order(plan.nodes, ("identity.users", str(actor.id)))
        self.assertTrue(cycle)
        self.assertEqual(len(order), 1)

    async def test_cascade_file_preview_exposes_metadata_but_not_storage_key(self) -> None:
        admin = user()
        attachment = File(
            id=uuid4(), storage_key="a" * 64, uploader_user_id=admin.id,
            original_name="photo.jpg", mime_type="image/jpeg", size_bytes=42,
            sha256="0" * 64, state="ready",
        )
        with patch("src.domain.admin.system._related_rows", new_callable=AsyncMock, return_value=[]), patch(
            "src.domain.admin.system._planned_role_updates", new_callable=AsyncMock, return_value=[]
        ):
            first = await _cascade_plan(SimpleNamespace(), admin, "system.files", attachment, for_update=False)
            attachment.storage_key = "b" * 64
            second = await _cascade_plan(SimpleNamespace(), admin, "system.files", attachment, for_update=False)
        self.assertEqual(first.preview["files"], [{
            "id": str(attachment.id), "original_name": "photo.jpg", "size_bytes": 42,
        }])
        self.assertNotIn("a" * 64, str(first.preview))
        self.assertNotEqual(first.preview["hash"], second.preview["hash"])

    async def test_hard_delete_commits_manifest_before_file_cleanup(self) -> None:
        admin = user()
        attachment = File(
            id=uuid4(), storage_key="a" * 64, uploader_user_id=admin.id,
            original_name="photo.jpg", mime_type="image/jpeg", size_bytes=42,
            sha256="0" * 64, state="ready",
        )
        key = ("system.files", str(attachment.id))
        plan = CascadePlan(
            nodes={key: CascadeNode(*key, attachment)}, delete_order=[key],
            preview={"hash": "a" * 64, "blockers": [], "total": 1,
                     "counts": [{"entity": key[0], "count": 1}],
                     "rows": [{"entity": key[0], "id": key[1], "via": []}],
                     "updates": [], "files": [{"id": key[1], "original_name": "photo.jpg", "size_bytes": 42}]},
            role_updates=[], private_files=[{"id": key[1], "key": attachment.storage_key}],
        )
        session = SimpleNamespace(
            execute=AsyncMock(), delete=AsyncMock(), flush=AsyncMock(),
            commit=AsyncMock(), add=Mock(),
        )

        async def cleanup(_session, _admin, _operation_id):
            session.commit.assert_awaited_once()
            return {"status": "done", "deleted": 1, "failed": 0, "failed_file_ids": []}

        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=attachment
        ), patch("src.domain.admin.system._cascade_plan", new_callable=AsyncMock, return_value=plan), patch(
            "src.domain.admin.system._lock_cascade_tables", new_callable=AsyncMock
        ), patch("src.domain.admin.system._preflight_private_files"), patch(
            "src.domain.admin.system.cleanup_operation_files", side_effect=cleanup
        ):
            result = await system_delete(
                session, admin, "system.files", str(attachment.id), _item(attachment)["etag"],
                "Удалить вложение и байты", "hard", "a" * 64,
            )
        self.assertEqual(result["file_cleanup"]["status"], "done")
        self.assertNotIn(attachment.storage_key, str(result))
        self.assertEqual(session.add.call_args.args[0].after_data["_file_cleanup"]["pending"], plan.private_files)

    async def test_hard_delete_table_lock_is_nowait(self) -> None:
        session = SimpleNamespace(execute=AsyncMock())
        await _lock_cascade_tables(session)
        statement = str(session.execute.await_args.args[0])
        self.assertIn('"identity"."users"', statement)
        self.assertIn('"system"."admin_operations"', statement)
        self.assertTrue(statement.endswith("IN EXCLUSIVE MODE NOWAIT"))

    async def test_file_cleanup_is_retryable_and_never_returns_storage_key(self) -> None:
        admin = user()
        file_id = uuid4()
        key = "a" * 64
        operation = AdminOperation(
            id=uuid4(), entity_key="system.files", row_key=str(file_id),
            operation="hard_delete", actor_user_id=admin.id,
            reason="Удалено ошибочное вложение", expected_etag="0" * 64,
            before_data={}, after_data={"_file_cleanup": {
                "status": "pending", "pending": [{"id": str(file_id), "key": key}],
                "deleted": 0, "failed": 0,
            }},
        )
        session = SimpleNamespace(
            execute=AsyncMock(), scalar=AsyncMock(return_value=operation),
            commit=AsyncMock(), rollback=AsyncMock(),
        )
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"FILE_STORAGE_ROOT": directory}):
            path = Path(directory) / "aa" / key
            path.parent.mkdir()
            path.write_bytes(b"private")
            with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
                "src.domain.admin.system.asyncio.to_thread", new_callable=AsyncMock,
                side_effect=OSError("temporary filesystem error"),
            ):
                first = await cleanup_operation_files(session, admin, operation.id)
            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["failed_file_ids"], [str(file_id)])
            self.assertTrue(path.exists())
            with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin):
                second = await cleanup_operation_files(session, admin, operation.id)
            self.assertEqual(second["status"], "done")
            self.assertEqual(second["deleted"], 1)
            self.assertFalse(path.exists())
            self.assertNotIn(key, str(first))
            self.assertNotIn(key, str(second))
            self.assertNotIn(key, str(_row_data(operation)))

    async def test_pending_file_cleanup_journal_cannot_be_edited(self) -> None:
        admin = user()
        row = AdminOperation(
            id=uuid4(), entity_key="system.files", row_key=str(uuid4()),
            operation="hard_delete", actor_user_id=admin.id,
            reason="Файлы ожидают очистки", expected_etag="0" * 64,
            before_data={}, after_data={"_file_cleanup": {
                "status": "pending", "pending": [{"id": str(uuid4()), "key": "a" * 64}],
            }},
        )
        session = SimpleNamespace(execute=AsyncMock(), add=Mock())
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await system_patch(
                    session, admin, "system.admin_operations", str(row.id),
                    _item(row)["etag"], "Исправить запись журнала", {"reason": "Иная причина"},
                )
        self.assertEqual(caught.exception.code, "cleanup_pending")
        session.add.assert_not_called()

    async def test_journal_patch_cannot_forge_file_cleanup_manifest(self) -> None:
        admin = user()
        row = AdminOperation(
            id=uuid4(), entity_key="system.files", row_key=str(uuid4()),
            operation="patch", actor_user_id=admin.id,
            reason="Исправлена карточка файла", expected_etag="0" * 64,
            before_data={}, after_data={"state": "rejected"},
        )
        session = SimpleNamespace(execute=AsyncMock(), add=Mock())
        with patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=admin), patch(
            "src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=row
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await system_patch(
                    session, admin, "system.admin_operations", str(row.id),
                    _item(row)["etag"], "Подмена служебного манифеста", {
                        "after_data": {"_file_cleanup": {
                            "status": "pending", "pending": [{"id": str(uuid4()), "key": "a" * 64}],
                        }},
                    },
                )
        self.assertEqual(caught.exception.code, "cleanup_manifest_protected")
        session.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
