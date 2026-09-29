"""MAX profile updates must not undo names entered by administrators."""

import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.common.auth import get_or_create_user, max_profile_name
from src.db.models import User
from src.domain.admin.actions import EditUserName, _edit_user_name
from src.domain.admin.system import _item, system_patch


class MaxProfileNameTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_name_tolerates_missing_or_malformed_fields(self) -> None:
        self.assertEqual(max_profile_name("  Иван  ", " Иванов "), "Иван Иванов")
        self.assertEqual(max_profile_name(None, "Иванов"), "Иванов")
        self.assertIsNone(max_profile_name(None, 42))

    async def test_new_user_uses_max_name_then_nickname_as_fallback(self) -> None:
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=None), add=Mock(), commit=AsyncMock()
        )
        user = await get_or_create_user(
            session, "123", full_name="  Иван   Иванов  ", max_username="ivan"
        )
        self.assertEqual(user.full_name, "Иван Иванов")
        self.assertEqual(user.max_display_name, "Иван Иванов")
        self.assertEqual(user.max_username, "ivan")
        self.assertFalse(user.full_name_is_manual)
        session.commit.assert_awaited_once()

        session.commit.reset_mock()
        nickname_only = await get_or_create_user(
            session, "456", max_username="@another_user"
        )
        self.assertEqual(nickname_only.full_name, "@another_user")
        self.assertEqual(nickname_only.max_username, "another_user")
        session.commit.assert_awaited_once()

    async def test_existing_auto_name_tracks_new_max_profile(self) -> None:
        user = User(
            id=uuid4(), max_user_id="123", kind="unassigned", version=3,
            full_name="Старое имя", max_display_name="Старое имя",
            max_username="old", full_name_is_manual=False,
        )
        session = SimpleNamespace(scalar=AsyncMock(return_value=user), commit=AsyncMock())
        result = await get_or_create_user(
            session, "123", full_name="Новое имя", max_username="new"
        )
        self.assertIs(result, user)
        self.assertEqual(user.full_name, "Новое имя")
        self.assertEqual(user.max_display_name, "Новое имя")
        self.assertEqual(user.max_username, "new")
        self.assertEqual(user.version, 4)
        session.commit.assert_awaited_once()

        session.commit.reset_mock()
        await get_or_create_user(
            session, "123", full_name="Новое имя", max_username="new"
        )
        session.commit.assert_not_awaited()

    async def test_manual_name_survives_max_profile_change(self) -> None:
        user = User(
            id=uuid4(), max_user_id="123", kind="unassigned", version=3,
            full_name="Имя от администратора", max_display_name="Старое имя",
            max_username="old", full_name_is_manual=True,
        )
        session = SimpleNamespace(scalar=AsyncMock(return_value=user), commit=AsyncMock())
        await get_or_create_user(
            session, "123", full_name="Новое имя MAX", max_username="new"
        )
        self.assertEqual(user.full_name, "Имя от администратора")
        self.assertEqual(user.max_display_name, "Новое имя MAX")
        self.assertEqual(user.max_username, "new")
        session.commit.assert_awaited_once()

    async def test_clearing_manual_name_restores_max_name(self) -> None:
        user = User(
            id=uuid4(), max_user_id="123", kind="unassigned", version=3,
            full_name="Ручное имя", max_display_name="Имя MAX",
            max_username="ivan", full_name_is_manual=True,
            full_name_confirmed_at=datetime.now(UTC),
        )
        session = SimpleNamespace()
        with (
            patch("src.domain.admin.actions.require_row", new_callable=AsyncMock, return_value=user),
            patch("src.domain.admin.actions.commit_or_conflict", new_callable=AsyncMock),
            patch("src.domain.admin.actions._audit_edit") as audit,
        ):
            result = await _edit_user_name(
                session, User(id=uuid4(), kind="admin"),
                EditUserName(user_id=user.id, full_name=None),
            )
        self.assertIs(result, user)
        self.assertFalse(user.full_name_is_manual)
        self.assertEqual(user.full_name, "Имя MAX")
        self.assertIsNone(user.full_name_confirmed_at)
        self.assertTrue(audit.called)

    async def test_setting_manual_name_overrides_max_name(self) -> None:
        user = User(
            id=uuid4(), max_user_id="123", kind="unassigned", version=3,
            full_name="Имя MAX", max_display_name="Имя MAX",
            full_name_is_manual=False,
        )
        with (
            patch("src.domain.admin.actions.require_row", new_callable=AsyncMock, return_value=user),
            patch("src.domain.admin.actions.commit_or_conflict", new_callable=AsyncMock),
            patch("src.domain.admin.actions._audit_edit"),
        ):
            await _edit_user_name(
                SimpleNamespace(), User(id=uuid4(), kind="admin"),
                EditUserName(user_id=user.id, full_name="Наше имя"),
            )
        self.assertTrue(user.full_name_is_manual)
        self.assertEqual(user.full_name, "Наше имя")

    async def test_system_editor_preserves_and_can_clear_manual_override(self) -> None:
        actor = User(id=uuid4(), max_user_id="987", kind="admin", version=1)
        user = User(
            id=uuid4(), max_user_id="123", kind="unassigned", version=1,
            full_name="Имя MAX", max_display_name="Имя MAX",
            max_username="ivan", full_name_is_manual=False,
        )
        session = SimpleNamespace(
            execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
            commit=AsyncMock(), add=Mock(), no_autoflush=nullcontext(),
        )
        with (
            patch("src.domain.admin.system.require_admin", new_callable=AsyncMock, return_value=actor),
            patch("src.domain.admin.system._lock_admin_rows", new_callable=AsyncMock),
            patch("src.domain.admin.system._load_row", new_callable=AsyncMock, return_value=user),
            patch(
                "src.domain.admin.system._validate_user_change",
                new_callable=AsyncMock,
                side_effect=lambda _session, _row, values, **_kwargs: values,
            ),
        ):
            await system_patch(
                session, actor, "identity.users", str(user.id), _item(user)["etag"],
                "Исправлено имя человека", {"full_name": "Наше имя"},
            )
            self.assertEqual(user.full_name, "Наше имя")
            self.assertTrue(user.full_name_is_manual)
            self.assertIsNone(user.full_name_confirmed_at)

            user.full_name_confirmed_at = datetime.now(UTC)

            await system_patch(
                session, actor, "identity.users", str(user.id), _item(user)["etag"],
                "Снят ручной приоритет имени", {"full_name": None},
            )
        self.assertEqual(user.full_name, "Имя MAX")
        self.assertFalse(user.full_name_is_manual)
        self.assertIsNone(user.full_name_confirmed_at)


if __name__ == "__main__":
    unittest.main()
