"""Shared issue descriptions stay separate from original resident reports."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import AuditEvent, IssueCard, IssueReport
from src.domain.issues.service import IssueError, create_card, edit_card


class IssueSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="resident")
        self.house = SimpleNamespace(id=uuid4(), entrance_count=None)
        self.category = SimpleNamespace(id=uuid4(), is_active=True)
        self.session = SimpleNamespace(
            get=AsyncMock(return_value=self.category),
            add=Mock(),
            flush=AsyncMock(),
        )

    async def _create(self, *, description: str, summary_description: str | None = None) -> IssueCard:
        with (
            patch("src.domain.issues.service._house", new=AsyncMock(return_value=self.house)),
            patch(
                "src.domain.issues.service._resident_locations",
                new=AsyncMock(return_value=({uuid4()}, set())),
            ),
        ):
            return await create_card(
                self.session,
                self.actor,
                house_id=self.house.id,
                category_id=self.category.id,
                title="Лифт не работает",
                description=description,
                summary_description=summary_description,
                scope_all_house=True,
                target_entrances=[],
                target_apartments=[],
            )

    async def test_create_keeps_original_report_and_confirmed_summary_separate(self) -> None:
        card = await self._create(
            description="  Вчера  лифт\nостановился в подъезде 2.  ",
            summary_description="Лифт  остановился\nв подъезде 2.",
        )
        report = next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], IssueReport)
        )
        self.assertEqual(card.summary_description, "Лифт  остановился\nв подъезде 2.")
        self.assertEqual(report.raw_description, "Вчера  лифт\nостановился в подъезде 2.")
        self.assertEqual(report.card_id, card.id)

    async def test_old_client_gets_full_normalized_fallback_up_to_limit(self) -> None:
        description = "  Лифт\n" + "а" * 1600
        card = await self._create(description=description)
        report = next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], IssueReport)
        )
        self.assertEqual(card.summary_description, ("Лифт " + "а" * 1600)[:1500])
        self.assertEqual(report.raw_description, description.strip())

    async def test_explicit_summary_must_have_one_to_1500_characters(self) -> None:
        for invalid in ("   ", "а" * 1501):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(IssueError) as caught:
                    await self._create(description="Лифт сломан", summary_description=invalid)
                self.assertEqual(caught.exception.code, "invalid_summary_description")
        self.session.add.assert_not_called()

    async def test_staff_edit_changes_only_shared_summary_and_audits_it(self) -> None:
        self.actor.kind = "admin"
        card = SimpleNamespace(
            id=uuid4(),
            house_id=self.house.id,
            category_id=self.category.id,
            title="Сломан лифт",
            summary_description="Лифт не работает",
            scope_all_house=True,
            version=2,
        )
        self.session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        self.session.execute = AsyncMock()
        with (
            patch("src.domain.issues.service._lock_issue_house", new=AsyncMock(return_value=card)),
            patch("src.domain.issues.service._house", new=AsyncMock(return_value=self.house)),
        ):
            result = await edit_card(
                self.session,
                self.actor,
                card.id,
                expected_version=2,
                category_id=self.category.id,
                title="Сломан лифт",
                summary_description="Кабина  лифта\nне движется",
                scope_all_house=True,
                target_entrances=[],
                target_apartments=[],
            )
        audit = next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], AuditEvent)
        )
        self.assertIs(result, card)
        self.assertEqual(card.summary_description, "Кабина  лифта\nне движется")
        self.assertEqual(card.version, 3)
        self.assertEqual(audit.before_data["summary_description"], "Лифт не работает")
        self.assertEqual(audit.after_data["summary_description"], "Кабина  лифта\nне движется")
        self.assertFalse(
            any(isinstance(call.args[0], IssueReport) for call in self.session.add.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
