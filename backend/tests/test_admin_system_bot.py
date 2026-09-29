"""The MAX system editor exposes the same guarded operations as the mini-app."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers import admin_system


class AdminSystemBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = SimpleNamespace()
        self.actor = SimpleNamespace(id=uuid4(), kind="admin")
        self.row_id = str(uuid4())
        self.entity = "identity.users"
        self.schema = {
            "entities": [{
                "key": self.entity,
                "label": self.entity,
                "fields": [
                    {"name": "full_name", "type": "text", "nullable": True, "editable": True},
                    {"name": "phone_verified_at", "type": "datetime", "nullable": True,
                     "editable": True, "clear_only": True},
                ],
            }]
        }

    async def test_home_and_row_offer_ledger_and_both_delete_modes(self) -> None:
        with (
            patch.object(admin_system, "system_schema", new=AsyncMock(return_value=self.schema)),
            patch.object(admin_system, "system_get", new=AsyncMock(return_value={
                "item": {"id": self.row_id, "etag": "a" * 64, "data": {"full_name": "Иван"}},
                "dependencies": [], "delete_modes": ["soft", "hard"],
            })),
        ):
            home = await admin_system.home(self.session, self.actor)
            detail = await admin_system._detail(
                self.session, self.actor, self.entity, self.row_id, 0
            )
        home_payloads = [button.payload for row in home.buttons for button in row]
        detail_payloads = [button.payload for row in detail.buttons for button in row]
        self.assertIn("adm:sys:operations:0", home_payloads)
        self.assertIn(f"adm:sys:remove:soft:{self.entity}:{self.row_id}", detail_payloads)
        self.assertIn(f"adm:sys:remove:hard:{self.entity}:{self.row_id}", detail_payloads)

    async def test_ledger_uses_stable_ids_and_is_editable(self) -> None:
        entry = {
            "id": str(uuid4()), "operation": "patch", "entity_key": self.entity,
            "row_key": self.row_id, "reason": "Исправлена опечатка",
        }
        with (
            patch.object(admin_system, "system_operations", new=AsyncMock(
                return_value={"items": [entry], "total": 7}
            )) as operations,
            patch.object(admin_system, "system_schema", new=AsyncMock(return_value={
                "entities": [{"key": "system.admin_operations", "label": "system.admin_operations",
                              "fields": [{"name": "reason", "type": "text", "nullable": False,
                                          "editable": True}]},
                             ]
            })),
            patch.object(admin_system, "system_get", new=AsyncMock(return_value={
                "item": {"id": entry["id"], "etag": "a" * 64, "data": entry},
                "dependencies": [], "delete_modes": ["hard"],
            })),
            patch.object(admin_system, "clear_dialog", new=AsyncMock()),
        ):
            listing = await admin_system.handle_action(
                self.session, self.actor, "adm:sys:operations:0"
            )
            detail = await admin_system.handle_action(
                self.session, self.actor,
                f"adm:sys:open:system.admin_operations:{entry['id']}:0",
            )
        payloads = [button.payload for row in listing.buttons for button in row]
        self.assertIn("adm:sys:operations:6", payloads)
        self.assertIn("adm:sys:search-operations", payloads)
        self.assertIn(f"adm:sys:open:system.admin_operations:{entry['id']}:0", payloads)
        self.assertIn(entry["id"], detail.text)
        self.assertIn(
            f"adm:sys:remove:hard:system.admin_operations:{entry['id']}",
            [button.payload for row in detail.buttons for button in row],
        )
        operations.assert_any_await(self.session, self.actor, q=None, limit=6, offset=0)

    async def test_ledger_search_keeps_query_for_pagination(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="admin_system_operations_search", step="query", data={}
        )

        async def update_state(_dialog, **kwargs):
            dialog.step = kwargs["step"]
            dialog.data = kwargs["data"]

        with (
            patch.object(admin_system, "update_dialog", new=AsyncMock(side_effect=update_state)),
            patch.object(admin_system, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(admin_system, "system_operations", new=AsyncMock(
                return_value={"items": [], "total": 0}
            )) as operations,
        ):
            await admin_system.handle_text(
                self.session, self.actor, dialog, "Исправлена опечатка"
            )
            await admin_system.handle_action(
                self.session, self.actor, "adm:sys:operations-page:6"
            )
        operations.assert_awaited_with(
            self.session, self.actor, q="Исправлена опечатка", limit=6, offset=6
        )

    def test_nullable_text_preserves_literal_word_and_clear_uses_command(self) -> None:
        field = {"type": "text", "nullable": True}
        self.assertEqual(admin_system._parse_value("очистить", field), "очистить")
        self.assertIsNone(admin_system._parse_value("/null", field))


if __name__ == "__main__":
    unittest.main()
