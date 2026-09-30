"""Issue supporters are unique and their identities stay staff-only."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import IssueReport
from src.domain.issues.service import support_card
from src.views.issues.get_card import _supporters


class IssueSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrying_same_support_reuses_report_without_new_event(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        card = SimpleNamespace(id=uuid4(), house_id=uuid4(), status="open")
        report_id = uuid4()
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, report_id]),
            add=Mock(),
            flush=AsyncMock(),
        )
        with (
            patch("src.domain.issues.service._lock_visible_card", new=AsyncMock(return_value=card)),
            patch("src.domain.issues.service._house", new=AsyncMock(return_value=SimpleNamespace())),
            patch("src.domain.issues.service._resident_locations", new=AsyncMock(return_value=({uuid4()}, set()))),
            patch("src.domain.issues.service._record_issue_event") as record_event,
        ):
            result = await support_card(session, actor, card.id, "Нет света")

        self.assertEqual(result, (card, report_id))
        session.add.assert_not_called()
        record_event.assert_not_called()

    async def test_new_description_can_still_be_added_to_existing_support(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        card = SimpleNamespace(id=uuid4(), house_id=uuid4(), status="open")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, None]),
            add=Mock(),
            flush=AsyncMock(),
        )
        with (
            patch("src.domain.issues.service._lock_visible_card", new=AsyncMock(return_value=card)),
            patch("src.domain.issues.service._house", new=AsyncMock(return_value=SimpleNamespace())),
            patch("src.domain.issues.service._resident_locations", new=AsyncMock(return_value=({uuid4()}, set()))),
        ):
            _, report_id = await support_card(session, actor, card.id, "Новая подробность")

        self.assertIsNotNone(report_id)
        reports = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], IssueReport)]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].raw_description, "Новая подробность")


class IssueSupporterVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_never_receives_supporter_identity(self) -> None:
        session = SimpleNamespace(execute=AsyncMock())
        result = await _supporters(session, SimpleNamespace(kind="resident"), uuid4())
        self.assertEqual(result, [])
        session.execute.assert_not_called()

    async def test_employee_receives_name_and_phone_of_unique_supporter(self) -> None:
        user_id = uuid4()
        support = SimpleNamespace(supported_at=datetime(2026, 9, 30, tzinfo=UTC))
        user = SimpleNamespace(
            id=user_id,
            full_name="Иван Петров",
            max_display_name="Иван",
            phone_number="79990000000",
        )
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(support, user)])))
        result = await _supporters(session, SimpleNamespace(kind="employee"), uuid4())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].user_id, user_id)
        self.assertEqual(result[0].display_name, "Иван Петров")
        self.assertEqual(result[0].phone_number, "79990000000")


if __name__ == "__main__":
    unittest.main()
