"""Regressions for issue status updates."""

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.domain.issues import service


class IssueStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_editing_closed_explanation_keeps_closure_time(self) -> None:
        closed_at = datetime(2026, 9, 28, 12, tzinfo=UTC)
        card = SimpleNamespace(
            id=uuid4(),
            house_id=uuid4(),
            status="closed",
            current_note="Мастер устранил неисправность",
            close_result="solved",
            closed_at=closed_at,
            updated_at=closed_at,
            version=1,
        )
        actor = SimpleNamespace(id=uuid4(), kind="admin")
        session = SimpleNamespace(flush=AsyncMock())
        with (
            patch.object(service, "_lock_issue_house", new=AsyncMock(return_value=card)),
            patch.object(service, "_house", new=AsyncMock(return_value=SimpleNamespace())),
            patch.object(service, "_staff_assignment", new=AsyncMock(return_value=None)),
            patch.object(service, "_record_issue_event"),
            patch.object(service, "_now", return_value=closed_at + timedelta(days=1)),
        ):
            result = await service.set_status(
                session,
                actor,
                card.id,
                status="closed",
                note="Работа завершена и проверена",
                close_result="solved",
            )

        self.assertIs(result, card)
        self.assertEqual(card.closed_at, closed_at)
        self.assertEqual(card.current_note, "Работа завершена и проверена")
        self.assertEqual(card.version, 2)


if __name__ == "__main__":
    unittest.main()
