"""Administrator MAX UI must stay separate from support and use domain actions."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers import admin_ui, commands
from src.domain.access.rules import AccessRuleError


class AdminBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = SimpleNamespace()
        self.admin = SimpleNamespace(id=uuid4(), kind="admin", phone_verified_at=None)

    async def test_admin_and_support_have_different_menus(self) -> None:
        admin = commands._home(self.admin)
        support = commands._home(
            SimpleNamespace(id=uuid4(), kind="support", phone_verified_at=None)
        )
        self.assertIn("Кабинет администратора", admin.text)
        self.assertIn("Кабинет поддержки", support.text)
        self.assertIn(
            "adm:new:invite_support",
            [button.payload for row in admin.buttons for button in row],
        )
        self.assertNotIn(
            "adm:new:invite_support",
            [button.payload for row in support.buttons for button in row],
        )

    async def test_non_admin_cannot_open_admin_callbacks_or_commands(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="support")
        forbidden = AccessRuleError(403, "admin_required", "Требуется администратор")
        with patch.object(
            admin_ui, "require_admin", new=AsyncMock(side_effect=forbidden)
        ):
            with self.assertRaises(AccessRuleError):
                await admin_ui.handle_action(self.session, actor, "adm:overview")
            with self.assertRaises(AccessRuleError):
                await admin_ui.handle_command(self.session, actor, "/admin")

    async def test_admin_can_receive_ready_file_from_access_request(self) -> None:
        file_id = uuid4()
        with (
            patch.object(admin_ui, "require_admin", new_callable=AsyncMock),
            patch.object(admin_ui, "admin_get", new_callable=AsyncMock) as get,
            patch.object(admin_ui, "get_file", new_callable=AsyncMock) as file_lookup,
            patch.object(admin_ui, "storage_root", return_value=Path("/tmp")),
            patch.object(admin_ui, "_read_private_file", return_value=b"sample"),
        ):
            get.return_value = {"id": str(file_id), "state": "ready", "original_name": "photo.jpg"}
            file_lookup.return_value = (
                SimpleNamespace(id=file_id, state="ready", original_name="photo.jpg", size_bytes=6),
                Path("/tmp/photo.jpg"),
            )
            detail = await admin_ui._detail(self.session, self.admin, "files", file_id, 0)
            self.assertIn(
                f"adm:file:{file_id}",
                [button.payload for row in detail.buttons for button in row],
            )
            reply = await admin_ui.handle_action(self.session, self.admin, f"adm:file:{file_id}")

        self.assertIsNotNone(reply.media)
        self.assertIn("photo.jpg", reply.text)

    async def test_admin_list_is_paginated_and_searchable(self) -> None:
        record_id = uuid4()
        with (
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
            patch.object(
                admin_ui,
                "admin_list",
                new=AsyncMock(
                    return_value={
                        "items": [
                            {
                                "id": str(record_id),
                                "title": "УК Альфа",
                                "status": "active",
                            }
                        ],
                        "total": 8,
                    }
                ),
            ) as listing,
            patch.object(admin_ui, "clear_dialog", new=AsyncMock()),
        ):
            reply = await admin_ui.handle_action(
                self.session, self.admin, "adm:list:companies:0"
            )
        listing.assert_awaited_once_with(
            self.session, self.admin, "companies", q=None, limit=7, offset=0
        )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"adm:get:companies:{record_id}:0", payloads)
        self.assertIn("adm:list:companies:7", payloads)
        self.assertIn("adm:search:companies", payloads)

    async def test_phone_invitation_form_requires_explicit_confirmation(self) -> None:
        dialog = SimpleNamespace(flow_kind="admin_action", step="start", data={})

        async def set_state(*_args, **kwargs):
            dialog.step = kwargs["step"]
            dialog.data = kwargs["data"]
            return dialog

        async def update_state(_dialog, **kwargs):
            if "step" in kwargs:
                dialog.step = kwargs["step"]
            if "data" in kwargs:
                dialog.data = kwargs["data"]

        result = {
            "id": str(uuid4()),
            "entity": "support_invites",
            "action": "invite_support",
        }
        with (
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
            patch.object(admin_ui, "set_dialog", new=AsyncMock(side_effect=set_state)),
            patch.object(
                admin_ui, "update_dialog", new=AsyncMock(side_effect=update_state)
            ),
            patch.object(admin_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(admin_ui, "clear_dialog", new=AsyncMock()),
            patch.object(
                admin_ui, "admin_action", new=AsyncMock(return_value=result)
            ) as action,
        ):
            prompt = await admin_ui.handle_action(
                self.session, self.admin, "adm:new:invite_support"
            )
            self.assertIn("Номер оператора", prompt.text)
            confirmation = await admin_ui.handle_text(
                self.session, self.admin, dialog, "+79991234567"
            )
            self.assertIn("Подтвердите", confirmation.text)
            action.assert_not_awaited()
            done = await admin_ui.handle_action(self.session, self.admin, "adm:commit")
        action.assert_awaited_once_with(
            self.session, self.admin, "invite_support", {"phone_number": "+79991234567"}
        )
        self.assertIn(result["id"], done.text)

    async def test_skipped_company_fields_are_not_cleared(self) -> None:
        company_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="admin_action",
            step="2",
            data={
                "action": "edit_company",
                "payload": {"company_id": str(company_id), "display_name": "Новое"},
            },
        )

        async def update_state(_dialog, **kwargs):
            if "step" in kwargs:
                dialog.step = kwargs["step"]
            if "data" in kwargs:
                dialog.data = kwargs["data"]

        with (
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
            patch.object(
                admin_ui, "update_dialog", new=AsyncMock(side_effect=update_state)
            ),
            patch.object(admin_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(admin_ui, "clear_dialog", new=AsyncMock()),
            patch.object(
                admin_ui,
                "admin_action",
                new=AsyncMock(
                    return_value={
                        "id": str(company_id),
                        "entity": "companies",
                        "action": "edit_company",
                    }
                ),
            ) as action,
        ):
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            await admin_ui.handle_action(self.session, self.admin, "adm:commit")
        action.assert_awaited_once_with(
            self.session,
            self.admin,
            "edit_company",
            {"company_id": str(company_id), "display_name": "Новое"},
        )

    async def test_merged_issue_links_to_primary_and_has_no_edit_actions(self) -> None:
        merged_id, primary_id = uuid4(), uuid4()
        with (
            patch.object(
                admin_ui,
                "admin_get",
                new=AsyncMock(
                    return_value={
                        "id": str(merged_id),
                        "title": "Лифт",
                        "status": "in_progress",
                        "merged_into_id": str(primary_id),
                    }
                ),
            ),
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
        ):
            reply = await admin_ui.handle_action(
                self.session, self.admin, f"adm:get:issues:{merged_id}:0"
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"adm:get:issues:{primary_id}:0", payloads)
        self.assertFalse(any(item.startswith("adm:form:") for item in payloads))

    async def test_closed_issue_does_not_offer_status_transition(self) -> None:
        card_id = uuid4()
        with (
            patch.object(
                admin_ui,
                "admin_get",
                new=AsyncMock(
                    return_value={
                        "id": str(card_id),
                        "title": "Лифт",
                        "status": "closed",
                        "merged_into_id": None,
                    }
                ),
            ),
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
        ):
            reply = await admin_ui.handle_action(
                self.session, self.admin, f"adm:get:issues:{card_id}:0"
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertNotIn(f"adm:form:set_issue_status:issues:{card_id}", payloads)
        self.assertIn(f"adm:form:edit_issue:issues:{card_id}", payloads)

    async def test_json_text_action_is_allowlisted_service_call(self) -> None:
        result = {
            "id": str(uuid4()),
            "entity": "support_invites",
            "action": "invite_support",
        }
        with (
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
            patch.object(
                admin_ui, "admin_action", new=AsyncMock(return_value=result)
            ) as action,
            patch.object(admin_ui, "clear_dialog", new=AsyncMock()),
        ):
            reply = await admin_ui.handle_command(
                self.session,
                self.admin,
                '/admin do invite_support {"phone_number": "+79991234567"}',
            )
        action.assert_awaited_once_with(
            self.session, self.admin, "invite_support", {"phone_number": "+79991234567"}
        )
        self.assertIn(result["id"], reply.text)

    async def test_issue_form_seeds_existing_values_and_house_scope_clears_targets(
        self,
    ) -> None:
        card_id, category_id = uuid4(), uuid4()
        dialog = SimpleNamespace(flow_kind="admin_action", step="start", data={})

        async def set_state(*_args, **kwargs):
            dialog.step = kwargs["step"]
            dialog.data = kwargs["data"]
            return dialog

        async def update_state(_dialog, **kwargs):
            if "step" in kwargs:
                dialog.step = kwargs["step"]
            if "data" in kwargs:
                dialog.data = kwargs["data"]

        with (
            patch.object(admin_ui, "require_admin", new=AsyncMock()),
            patch.object(
                admin_ui,
                "admin_get",
                new=AsyncMock(
                    return_value={
                        "id": str(card_id),
                        "version": 4,
                        "category_id": str(category_id),
                        "title": "Лифт",
                        "scope_all_house": False,
                        "status": "open",
                        "merged_into_id": None,
                        "targets": [{"entrance_number": 1, "apartment_number": None}],
                    }
                ),
            ),
            patch.object(admin_ui, "set_dialog", new=AsyncMock(side_effect=set_state)),
            patch.object(
                admin_ui, "update_dialog", new=AsyncMock(side_effect=update_state)
            ),
            patch.object(admin_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(admin_ui, "clear_dialog", new=AsyncMock()),
            patch.object(
                admin_ui,
                "admin_action",
                new=AsyncMock(
                    return_value={
                        "id": str(card_id),
                        "entity": "issues",
                        "action": "edit_issue",
                    }
                ),
            ) as action,
        ):
            reply = await admin_ui.handle_action(
                self.session, self.admin, f"adm:form:edit_issue:issues:{card_id}"
            )
            self.assertIn(str(category_id), reply.text)
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            await admin_ui.handle_text(self.session, self.admin, dialog, "да")
            await admin_ui.handle_text(self.session, self.admin, dialog, ".")
            confirmation = await admin_ui.handle_text(
                self.session, self.admin, dialog, "."
            )
            self.assertIn('"target_entrances": []', confirmation.text)
            await admin_ui.handle_action(self.session, self.admin, "adm:commit")
        action.assert_awaited_once_with(
            self.session,
            self.admin,
            "edit_issue",
            {
                "card_id": str(card_id),
                "expected_version": 4,
                "category_id": str(category_id),
                "title": "Лифт",
                "scope_all_house": True,
                "target_entrances": [],
                "target_apartments": [],
            },
        )

    async def test_support_invite_acceptance_does_not_require_admin_role(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="unassigned")
        invitation_id = uuid4()
        with (
            patch.object(
                admin_ui, "accept_support_invitation", new=AsyncMock()
            ) as accept,
            patch.object(admin_ui, "require_admin", new=AsyncMock()) as admin_check,
        ):
            reply = await admin_ui.handle_action(
                self.session, actor, f"adm:accept:{invitation_id}"
            )
        accept.assert_awaited_once_with(
            self.session, actor, invitation_id=invitation_id
        )
        admin_check.assert_not_awaited()
        self.assertIn("Приглашение принято", reply.text)


if __name__ == "__main__":
    unittest.main()
