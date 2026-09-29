"""Bot notification buttons open the matching subject, not a different request kind."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers.commands import _home, _notification_action
from src.bot.handlers.notifications_text import handle_notification_text


class BotNotificationUiTests(unittest.IsolatedAsyncioTestCase):
    def test_unassigned_home_shows_company_and_house_requests(self) -> None:
        reply = _home(
            SimpleNamespace(kind="unassigned", phone_verified_at=datetime.now(UTC))
        )
        payloads = {button.payload for row in reply.buttons for button in row}
        self.assertIn("a:requests:company_registration", payloads)
        self.assertIn("a:requests:house_addition", payloads)

    async def test_notification_buttons_reach_later_pages_and_stay_after_read(
        self,
    ) -> None:
        rows = [
            (
                SimpleNamespace(
                    subject_kind="issue_card",
                    subject_id=uuid4(),
                    id=uuid4(),
                    read_at=None,
                    created_at=datetime.now(UTC),
                ),
                SimpleNamespace(event_kind=f"event_{index}"),
            )
            for index in range(20)
        ]
        with (
            patch(
                "src.bot.handlers.commands.list_notifications",
                new_callable=AsyncMock,
                return_value=rows,
            ),
            patch(
                "src.bot.handlers.commands.mark_read", new_callable=AsyncMock
            ) as mark_read,
        ):
            second = await _notification_action(
                SimpleNamespace(), SimpleNamespace(kind="resident"), "n:list:1"
            )
            after_read = await _notification_action(
                SimpleNamespace(),
                SimpleNamespace(kind="resident"),
                f"n:read:{rows[8][0].id}:1",
            )
        self.assertIn("страница 2/3", second.text)
        self.assertEqual(
            len([line for line in second.text.splitlines() if "●" in line]), 8
        )
        payloads = [button.payload for row in second.buttons for button in row]
        self.assertIn("n:list:0", payloads)
        self.assertIn("n:list:2", payloads)
        self.assertIn(f"n:read:{rows[8][0].id}:1", payloads)
        mark_read.assert_awaited_once_with(
            unittest.mock.ANY, unittest.mock.ANY, rows[8][0].id
        )
        self.assertIn("страница 2/3", after_read.text)

    async def test_text_notifications_support_later_pages(self) -> None:
        rows = [
            (
                SimpleNamespace(
                    id=uuid4(),
                    subject_kind="issue_card",
                    subject_id=uuid4(),
                    read_at=None,
                ),
                SimpleNamespace(event_kind=f"event_{index}"),
            )
            for index in range(23)
        ]
        with patch(
            "src.bot.handlers.notifications_text.list_notifications",
            new_callable=AsyncMock,
            return_value=rows,
        ):
            second = await handle_notification_text(
                SimpleNamespace(), SimpleNamespace(), "/notifications 2"
            )
            third = await handle_notification_text(
                SimpleNamespace(), SimpleNamespace(), "/notifications 3"
            )
            beyond = await handle_notification_text(
                SimpleNamespace(), SimpleNamespace(), "/notifications 4"
            )
        self.assertIn("страница 2/3", second)
        self.assertIn("event_10", second)
        self.assertNotIn("event_9 ·", second)
        self.assertIn("/notifications 3", second)
        self.assertIn("event_22", third)
        self.assertNotIn("event_10 ·", third)
        self.assertIn("На этой странице уведомлений нет", beyond)

    async def test_each_access_request_notification_opens_its_own_kind(self) -> None:
        actor = SimpleNamespace(kind="support")
        for subject_kind, request_kind in (
            ("resident_request", "resident"),
            ("company_registration_request", "company_registration"),
            ("house_addition_request", "house_addition"),
        ):
            with self.subTest(subject_kind=subject_kind):
                request_id = uuid4()
                notification = SimpleNamespace(
                    subject_kind=subject_kind,
                    subject_id=request_id,
                    id=uuid4(),
                    read_at=None,
                    created_at=datetime.now(UTC),
                )
                with patch(
                    "src.bot.handlers.commands.list_notifications",
                    new_callable=AsyncMock,
                ) as list_rows:
                    list_rows.return_value = [
                        (notification, SimpleNamespace(event_kind="created"))
                    ]
                    reply = await _notification_action(
                        SimpleNamespace(), actor, "n:list"
                    )
                self.assertEqual(
                    reply.buttons[0][0].payload,
                    f"a:request:{request_kind}:{request_id}",
                )

    async def test_unknown_subject_has_no_misleading_open_button(self) -> None:
        notification = SimpleNamespace(
            subject_kind="unknown",
            subject_id=uuid4(),
            id=uuid4(),
            read_at=None,
            created_at=datetime.now(UTC),
        )
        with patch(
            "src.bot.handlers.commands.list_notifications", new_callable=AsyncMock
        ) as list_rows:
            list_rows.return_value = [
                (notification, SimpleNamespace(event_kind="created"))
            ]
            reply = await _notification_action(
                SimpleNamespace(), SimpleNamespace(kind="resident"), "n:list"
            )
        self.assertFalse(
            any(
                button.text.startswith("Открыть")
                for row in reply.buttons
                for button in row
            )
        )


if __name__ == "__main__":
    unittest.main()
