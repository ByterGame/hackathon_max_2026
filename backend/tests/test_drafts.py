"""Draft access, optimistic revisions, and bot-to-domain handoff."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.bot.handlers.drafts_text import _save_text, _send_text, handle_draft_text
from src.db.models import Draft
from src.domain.drafts.service import (
    DraftError,
    _validate_payload,
    get_draft,
    mark_submitted,
    save_draft,
)
from src.domain.issues.service import IssueError


class DraftValidationTests(unittest.TestCase):
    def test_only_known_flows_and_fields_but_partial_payload_is_valid(self) -> None:
        _validate_payload("issue_card", {"title": "Лифт остановился"})
        _validate_payload("resident_request", {"house_id": str(uuid4())})
        for kind, payload in (
            ("other", {}),
            ("issue_card", {"bot_token": "secret"}),
            ("resident_request", {"description": "другое действие"}),
        ):
            with self.subTest(kind=kind, payload=payload):
                with self.assertRaises(DraftError):
                    _validate_payload(kind, payload)


class DraftServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_is_uncommitted_and_starts_at_revision_one(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        session = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        draft = await save_draft(
            session, actor, flow_kind="resident_request", payload={"full_name": "Иванов Иван"}
        )
        self.assertIsInstance(draft, Draft)
        self.assertEqual(draft.owner_user_id, actor.id)
        self.assertEqual(draft.revision, 1)
        session.add.assert_called_once_with(draft)
        session.flush.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_update_and_submit_use_owner_revision_and_unsubmitted_conditions(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        row = SimpleNamespace(id=uuid4(), revision=2)
        session = SimpleNamespace(scalar=AsyncMock(return_value=row), commit=AsyncMock())
        updated = await save_draft(
            session, actor, flow_kind="issue_card", payload={"title": "Нет света"},
            draft_id=row.id, revision=1,
        )
        self.assertIs(updated, row)
        statement = str(session.scalar.await_args.args[0])
        self.assertIn("owner_user_id", statement)
        self.assertIn("revision", statement)
        self.assertIn("submitted_at IS NULL", statement)
        session.commit.assert_not_awaited()

        await mark_submitted(session, actor, draft_id=row.id, revision=2)
        statement = str(session.scalar.await_args.args[0])
        self.assertIn("owner_user_id", statement)
        self.assertIn("revision", statement)
        self.assertIn("submitted_at IS NULL", statement)

    async def test_foreign_draft_is_indistinguishable_from_missing_draft(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        session = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with self.assertRaises(DraftError) as raised:
            await get_draft(session, actor, uuid4())
        self.assertEqual((raised.exception.status_code, raised.exception.code), (404, "draft_not_found"))
        self.assertIn("owner_user_id", str(session.scalar.await_args.args[0]))

    async def test_stale_or_submitted_revision_is_rejected(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        draft_id = uuid4()
        for submitted_at, expected in (
            (None, "stale_revision"),
            (datetime.now(UTC), "draft_submitted"),
        ):
            with self.subTest(expected=expected):
                existing = SimpleNamespace(id=draft_id, submitted_at=submitted_at)
                session = SimpleNamespace(scalar=AsyncMock(side_effect=[None, existing]))
                with self.assertRaises(DraftError) as raised:
                    await mark_submitted(session, actor, draft_id=draft_id, revision=1)
                self.assertEqual(raised.exception.code, expected)


class DraftBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_save_resident_uses_shared_draft_service(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        session = SimpleNamespace()
        house_id = uuid4()
        row = SimpleNamespace(id=uuid4(), revision=1)
        with patch("src.bot.handlers.drafts_text.save_draft", new=AsyncMock(return_value=row)) as saved:
            result = await _save_text(
                session, actor,
                f"resident_request | {house_id} | 2 | 17 | Иванов Иван",
            )
        self.assertIn(str(row.id), result)
        self.assertEqual(saved.await_args.kwargs["payload"], {
            "house_id": str(house_id), "entrance_number": 2,
            "apartment_number": 17, "full_name": "Иванов Иван",
        })

    async def test_send_issue_marks_then_uses_existing_domain_operation(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        draft_id, card_id, report_id, file_id = uuid4(), uuid4(), uuid4(), uuid4()
        row = SimpleNamespace(
            id=draft_id, revision=3, flow_kind="issue_card",
            payload={
                "house_id": str(uuid4()), "category_id": str(uuid4()),
                "title": "Лифт", "description": "Не работает",
                "scope_all_house": True,
            },
        )
        session = SimpleNamespace(
            rollback=AsyncMock(), scalar=AsyncMock(return_value=report_id),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [file_id])),
        )
        order = []

        async def mark(*args, **kwargs):
            order.append("mark")
            return row

        async def create(*args, **kwargs):
            order.append("create")
            return SimpleNamespace(id=card_id)

        async def attach(*args, **kwargs):
            self.assertEqual(kwargs, {
                "file_id": file_id,
                "parent_kind": "issue_report",
                "parent_id": report_id,
            })
            order.append("attach")

        with (
            patch("src.bot.handlers.drafts_text.get_draft", new=AsyncMock(return_value=row)),
            patch("src.bot.handlers.drafts_text.mark_submitted", new=mark),
            patch("src.bot.handlers.drafts_text.create_card", new=create),
            patch("src.bot.handlers.drafts_text.attach", new=attach),
        ):
            result = await _send_text(session, actor, draft_id, 3)
        self.assertEqual(order, ["mark", "create", "attach"])
        self.assertIn(str(card_id), result)
        session.rollback.assert_not_awaited()

    async def test_failed_send_rolls_back_draft_marker(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        draft_id = uuid4()
        row = SimpleNamespace(
            id=draft_id, revision=1, flow_kind="issue_card",
            payload={
                "house_id": str(uuid4()), "category_id": str(uuid4()),
                "title": "Лифт", "description": "Не работает",
                "scope_all_house": True,
            },
        )
        session = SimpleNamespace(rollback=AsyncMock())
        with (
            patch("src.bot.handlers.drafts_text.get_draft", new=AsyncMock(return_value=row)),
            patch("src.bot.handlers.drafts_text.mark_submitted", new=AsyncMock(return_value=row)),
            patch(
                "src.bot.handlers.drafts_text.create_card",
                new=AsyncMock(side_effect=IssueError("invalid_category", "Категория не найдена")),
            ),
        ):
            result = await handle_draft_text(session, actor, f"/draft send {draft_id} 1")
        self.assertIn("Категория не найдена", result)
        session.rollback.assert_awaited_once()
