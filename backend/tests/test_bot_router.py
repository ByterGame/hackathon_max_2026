"""MAX adapter routes callbacks, text steps, and media without losing dialog state."""

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from maxapi.enums import ChatType, UpdateType
from maxapi.types import BotStarted
from maxapi.types.attachments import RequestContactButton
from maxapi.types.input_media import InputMediaBuffer

from src.bot.handlers import commands
from src.bot.ui import Button, UiReply
from src.domain.files.storage import FileError


class BotRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(
            id=uuid4(), kind="resident", phone_verified_at=object()
        )
        self.session = SimpleNamespace(
            in_transaction=lambda: True,
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        @asynccontextmanager
        async def session_factory():
            yield self.session

        router = commands.build_router(session_factory, "test-token")
        self.message_handler = next(
            handler.func_event
            for handler in router.event_handlers
            if handler.func_event.__name__ == "on_message"
        )
        self.callback_handler = next(
            handler.func_event
            for handler in router.event_handlers
            if handler.func_event.__name__ == "on_callback"
        )
        self.started_registration = next(
            handler
            for handler in router.event_handlers
            if handler.func_event.__name__ == "on_started"
        )
        self.started_handler = self.started_registration.func_event
        self.sender = SimpleNamespace(
            user_id=123, first_name="Иван", last_name="Иванов", is_bot=False
        )
        self.message = SimpleNamespace(
            sender=self.sender,
            recipient=SimpleNamespace(chat_type=ChatType.DIALOG),
            answer=AsyncMock(),
            bot=object(),
        )

    def _message_event(self, text: str, *, attachments=None):
        self.message.body = SimpleNamespace(
            text=text, mid="message-1", attachments=attachments or []
        )
        return SimpleNamespace(message=self.message)

    def _started_event(self) -> BotStarted:
        event = BotStarted(
            timestamp=123456,
            chat_id=987,
            user={
                "user_id": self.sender.user_id,
                "first_name": self.sender.first_name,
                "last_name": self.sender.last_name,
                "is_bot": False,
                "last_activity_time": 0,
            },
        )
        event.bot = SimpleNamespace(send_message=AsyncMock())
        return event

    async def test_start_button_sends_role_menu_without_text_message(self) -> None:
        self.assertEqual(self.started_registration.update_type, UpdateType.BOT_STARTED)
        event = self._started_event()
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ) as resolve_actor,
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ) as reserve,
            patch.object(commands, "clear_dialog", new=AsyncMock()) as clear,
        ):
            await self.started_handler(event)

        resolve_actor.assert_awaited_once_with(
            self.session, "123", full_name="Иван Иванов"
        )
        reserve.assert_awaited_once_with(
            self.session,
            user_id=self.actor.id,
            message_id="bot_started:987:123456",
            content="start",
        )
        clear.assert_awaited_once_with(self.session, self.actor.id)
        self.session.commit.assert_awaited_once()
        answer = event.bot.send_message.await_args.kwargs
        self.assertEqual((answer["chat_id"], answer["user_id"]), (987, 123))
        self.assertEqual(answer["text"], commands._home(self.actor).text)
        payloads = {
            button.payload
            for row in answer["attachments"][0].payload.buttons
            for button in row
        }
        self.assertIn("i:new", payloads)
        self.assertIn("menu", payloads)

    async def test_start_button_prompts_unverified_user_to_share_contact(self) -> None:
        self.actor.phone_verified_at = None
        event = self._started_event()
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "clear_dialog", new=AsyncMock()),
        ):
            await self.started_handler(event)

        answer = event.bot.send_message.await_args.kwargs
        self.assertIn("номер вашего аккаунта MAX", answer["text"])
        self.assertIsInstance(
            answer["attachments"][0].payload.buttons[0][0], RequestContactButton
        )

    async def test_duplicate_start_event_does_not_send_another_menu(self) -> None:
        event = self._started_event()
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=False)
            ),
            patch.object(commands, "clear_dialog", new=AsyncMock()) as clear,
        ):
            await self.started_handler(event)

        clear.assert_not_awaited()
        self.session.commit.assert_not_awaited()
        event.bot.send_message.assert_not_awaited()

    async def test_opening_lists_clears_previous_text_dialog(self) -> None:
        for payload, target in (
            ("d:list", "_draft_list"),
            ("n:list", "_notification_action"),
        ):
            with self.subTest(payload=payload):
                with (
                    patch.object(commands, "clear_dialog", new=AsyncMock()) as clear,
                    patch.object(
                        commands, target, new=AsyncMock(return_value=UiReply("Список"))
                    ),
                ):
                    reply = await commands._handle_action(
                        self.session, self.actor, payload
                    )
                clear.assert_awaited_once_with(self.session, self.actor.id)
                self.assertEqual(reply.text, "Список")

    async def test_callback_acknowledged_and_dispatched_with_home_button(self) -> None:
        calls = []

        async def acknowledge(*, notification: str):
            self.assertEqual(notification, "Обрабатываю...")
            calls.append("ack")

        async def action(*_args):
            calls.append("action")
            return UiReply("Открыто", [[Button("Карточка", f"i:card:{uuid4()}")]])

        event = SimpleNamespace(
            ack=AsyncMock(side_effect=acknowledge),
            message=self.message,
            callback=SimpleNamespace(
                user=self.sender, payload="i:houses", callback_id="callback-1"
            ),
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "_handle_action", new=AsyncMock(side_effect=action)),
        ):
            await self.callback_handler(event)
        self.assertEqual(calls, ["ack", "action"])
        event.ack.assert_awaited_once_with(notification="Обрабатываю...")
        self.session.commit.assert_awaited_once()
        answer = self.message.answer.await_args.kwargs
        self.assertEqual(answer["text"], "Открыто")
        payloads = [
            button.payload
            for row in answer["attachments"][0].payload.buttons
            for button in row
        ]
        self.assertIn("menu", payloads)

    async def test_file_callback_sends_private_bytes_as_max_attachment(self) -> None:
        card_id, file_id = uuid4(), uuid4()
        event = SimpleNamespace(
            ack=AsyncMock(),
            message=self.message,
            callback=SimpleNamespace(
                user=self.sender,
                payload=f"f:get:{card_id}:{file_id}",
                callback_id="file-callback",
            ),
        )
        media = InputMediaBuffer(b"%PDF-test", filename="repair", type="file")
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "clear_dialog", new=AsyncMock()),
            patch.object(
                commands,
                "get_card_attachment",
                new=AsyncMock(return_value=UiReply("Файл", media=media)),
            ) as fetch,
        ):
            await self.callback_handler(event)
        fetch.assert_awaited_once_with(self.session, self.actor, card_id, file_id)
        attachments = self.message.answer.await_args.kwargs["attachments"]
        self.assertIn(media, attachments)
        self.assertEqual(self.message.answer.await_args.kwargs["text"], "Файл")

    async def test_foreign_file_callback_sends_no_attachment(self) -> None:
        card_id, file_id = uuid4(), uuid4()
        event = SimpleNamespace(
            ack=AsyncMock(),
            message=self.message,
            callback=SimpleNamespace(
                user=self.sender,
                payload=f"f:get:{card_id}:{file_id}",
                callback_id="foreign-file-callback",
            ),
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "clear_dialog", new=AsyncMock()),
            patch.object(
                commands,
                "get_card_attachment",
                new=AsyncMock(
                    side_effect=FileError(404, "file_not_found", "Файл не найден")
                ),
            ),
        ):
            await self.callback_handler(event)
        answer = self.message.answer.await_args.kwargs
        self.assertIn("Файл не найден", answer["text"])
        self.assertFalse(
            any(isinstance(item, InputMediaBuffer) for item in answer["attachments"])
        )

    async def test_plain_text_is_consumed_by_active_issue_step(self) -> None:
        dialog = SimpleNamespace(flow_kind="issue_new", step="description")
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(
                commands,
                "_handle_dialog_text",
                new=AsyncMock(return_value=UiReply("Кого касается проблема?")),
            ) as handle_text,
        ):
            await self.message_handler(self._message_event("Не работает лифт"))
        handle_text.assert_awaited_once_with(
            self.session, self.actor, dialog, "Не работает лифт"
        )
        self.assertEqual(
            self.message.answer.await_args.kwargs["text"], "Кого касается проблема?"
        )

    async def test_file_text_commands_use_same_authorized_handlers(self) -> None:
        card_id, file_id = uuid4(), uuid4()
        media = InputMediaBuffer(b"%PDF-test", filename="repair", type="file")
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "get_dialog", new=AsyncMock(return_value=None)),
            patch.object(
                commands,
                "list_card_attachments",
                new=AsyncMock(return_value=UiReply("Список")),
            ) as listing,
            patch.object(
                commands,
                "get_card_attachment",
                new=AsyncMock(return_value=UiReply("Файл", media=media)),
            ) as fetching,
        ):
            await self.message_handler(self._message_event(f"/files {card_id}"))
            listing.assert_awaited_once_with(self.session, self.actor, card_id)
            await self.message_handler(
                self._message_event(f"/getfile {card_id} {file_id}")
            )
            fetching.assert_awaited_once_with(
                self.session, self.actor, card_id, file_id
            )
        self.assertIn(media, self.message.answer.await_args.kwargs["attachments"])

    async def test_cancel_clears_active_dialog_without_consuming_text(self) -> None:
        dialog = SimpleNamespace(flow_kind="issue_new", step="description")
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(commands, "clear_dialog", new=AsyncMock()) as clear,
            patch.object(
                commands, "_handle_dialog_text", new=AsyncMock()
            ) as handle_text,
        ):
            await self.message_handler(self._message_event("/cancel"))
        clear.assert_awaited_once_with(self.session, self.actor.id)
        handle_text.assert_not_awaited()
        self.assertIn(
            "Действие отменено", self.message.answer.await_args.kwargs["text"]
        )

    async def test_media_is_not_attached_to_issue_before_attachment_step(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="issue_new", step="review", draft_id=uuid4(), data={}
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(commands, "handle_media_text", new=AsyncMock()) as media,
        ):
            await self.message_handler(self._message_event("", attachments=[object()]))
        media.assert_not_awaited()
        self.assertIn("шага вложений", self.message.answer.await_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
