"""Bot diagnostics keep failures traceable without exposing MAX user content."""

import asyncio
import traceback
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from maxapi.enums import ChatType
from maxapi.types.attachments import Contact

from src.bot import main as bot_main
from src.bot import notification_worker
from src.bot.diagnostics import callback_event_type
from src.bot.handlers import commands
from src.core.logging import current_request_id
from src.domain.notifications import service as notification_service


class BotUpdateDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def test_file_callback_has_fixed_diagnostic_category(self) -> None:
        self.assertEqual(callback_event_type("f:get:private-file-id"), "callback_file")

    def setUp(self) -> None:
        self.actor = SimpleNamespace(
            id=uuid4(), kind="resident", phone_verified_at=None
        )
        self.session = SimpleNamespace(
            in_transaction=lambda: True,
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        @asynccontextmanager
        async def session_factory():
            yield self.session

        router = commands.build_router(session_factory, "private-bot-token")
        self.on_message = next(
            handler.func_event
            for handler in router.event_handlers
            if handler.func_event.__name__ == "on_message"
        )
        self.on_callback = next(
            handler.func_event
            for handler in router.event_handlers
            if handler.func_event.__name__ == "on_callback"
        )
        self.sender = SimpleNamespace(
            user_id=123, first_name="Private", last_name="Name", is_bot=False
        )
        self.message = SimpleNamespace(
            sender=self.sender,
            recipient=SimpleNamespace(chat_type=ChatType.DIALOG),
            answer=AsyncMock(),
            bot=object(),
        )

    def _message(self, text: str, *, attachments=None):
        self.message.body = SimpleNamespace(
            text=text, mid="message-1", attachments=attachments or []
        )
        return SimpleNamespace(message=self.message)

    async def test_text_update_has_correlated_start_and_finish_without_content(
        self,
    ) -> None:
        secret = "private apartment note 79991234567"
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(commands, "get_dialog", new=AsyncMock(return_value=None)),
            self.assertLogs(commands.logger, level="INFO") as captured,
        ):
            await self.on_message(self._message(secret))

        self.assertEqual(len(captured.records), 2)
        received, finished = captured.records
        self.assertEqual(received.event, "bot_update_received")
        self.assertEqual(finished.event, "bot_update_finished")
        self.assertEqual(received.request_id, finished.request_id)
        self.assertEqual(finished.actor_id, str(self.actor.id))
        self.assertEqual(finished.message_id, "message-1")
        self.assertEqual(finished.event_type, "text")
        self.assertEqual(finished.result, "processed")
        self.assertGreaterEqual(finished.duration_ms, 0)
        self.assertNotIn(secret, str([record.__dict__ for record in captured.records]))

    async def test_contact_rejection_has_safe_machine_code_and_original_phase(
        self,
    ) -> None:
        contact = Contact(
            type="contact",
            payload={"vcf_info": "TEL:79991234567", "hash": "private-signature"},
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(
                commands,
                "validate_bot_contact",
                side_effect=ValueError("Invalid MAX contact signature"),
            ),
            self.assertLogs(commands.logger, level="INFO") as captured,
        ):
            await self.on_message(self._message("", attachments=[contact]))

        finished = captured.records[-1]
        self.assertEqual(finished.event_type, "contact")
        self.assertEqual(finished.attachment_types, ["contact"])
        self.assertEqual(finished.result, "rejected")
        self.assertEqual(finished.phase, "validate_contact")
        self.assertEqual(finished.error_code, "contact_signature_invalid")
        self.assertEqual(finished.exception_type, "ValueError")
        self.assertEqual(finished.exception_message, "Invalid MAX contact signature")
        self.assertNotIn(
            "79991234567", str([record.__dict__ for record in captured.records])
        )
        self.assertNotIn(
            "private-signature", str([record.__dict__ for record in captured.records])
        )

    async def test_callback_logs_fixed_category_not_raw_payload(self) -> None:
        payload = "a:offer:private-phone-79991234567"
        event = SimpleNamespace(
            ack=AsyncMock(),
            message=self.message,
            callback=SimpleNamespace(
                user=self.sender, payload=payload, callback_id="callback-1"
            ),
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=False)
            ),
            self.assertLogs(commands.logger, level="INFO") as captured,
        ):
            await self.on_callback(event)

        finished = captured.records[-1]
        self.assertEqual(finished.event_type, "callback_access")
        self.assertEqual(finished.callback_id, "callback-1")
        self.assertEqual(finished.result, "duplicate")
        self.assertNotIn(payload, str([record.__dict__ for record in captured.records]))

    async def test_callback_error_logs_sanitized_diagnostic_text(self) -> None:
        payload = "a:offer:private-resident-note"
        seen_request_ids: list[str | None] = []

        async def fail_action(*_args):
            seen_request_ids.append(current_request_id.get())
            raise ValueError(f"Invalid callback {payload}")

        event = SimpleNamespace(
            ack=AsyncMock(),
            message=self.message,
            callback=SimpleNamespace(
                user=self.sender, payload=payload, callback_id="callback-2"
            ),
        )
        with (
            patch.object(
                commands, "get_or_create_user", new=AsyncMock(return_value=self.actor)
            ),
            patch.object(
                commands, "_reserve_message", new=AsyncMock(return_value=True)
            ),
            patch.object(
                commands,
                "_handle_action",
                new=AsyncMock(side_effect=fail_action),
            ),
            self.assertLogs(commands.logger, level="INFO") as captured,
        ):
            await self.on_callback(event)

        finished = captured.records[-1]
        self.assertEqual(finished.result, "rejected")
        self.assertEqual(seen_request_ids, [finished.request_id])
        self.assertIsNone(current_request_id.get())
        self.assertIn("Invalid callback", finished.exception_message)
        self.assertNotIn(payload, finished.exception_message)


class NotificationWorkerDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    @asynccontextmanager
    async def session_factory():
        yield object()

    async def test_cycle_logs_counters_and_cancellation(self) -> None:
        with (
            patch.object(
                notification_worker,
                "expand_outbox_once",
                new=AsyncMock(return_value=2),
            ),
            patch.object(
                notification_worker,
                "deliver_bot_notifications_once",
                new=AsyncMock(
                    return_value=notification_service.BotDeliveryStats(
                        processed=3,
                        sent=2,
                        failed=1,
                        failure_types={"TimeoutError": 1},
                        failure_messages={"TimeoutError": "MAX request timed out"},
                    )
                ),
            ),
            patch.object(
                notification_worker.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            self.assertLogs(notification_worker.logger, level="INFO") as captured,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker.run_notification_worker(
                    self.session_factory, object()
                )

        cycle = captured.records[0]
        self.assertEqual(cycle.event, "bot_notification_cycle_finished")
        self.assertEqual((cycle.expanded_count, cycle.delivery_count), (2, 3))
        self.assertEqual((cycle.sent_count, cycle.failed_count), (2, 1))
        self.assertEqual(cycle.result, "processed")
        failures = [
            record
            for record in captured.records
            if record.event == "bot_notification_delivery_failed"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].exception_type, "TimeoutError")
        self.assertEqual(failures[0].exception_message, "MAX request timed out")
        self.assertEqual(failures[0].failed_count, 1)
        self.assertEqual(captured.records[-1].cycle_id, cycle.cycle_id)

    async def test_cycle_failure_logs_type_phase_and_redacted_exception_text(
        self,
    ) -> None:
        with (
            patch.object(
                notification_worker,
                "expand_outbox_once",
                new=AsyncMock(side_effect=RuntimeError("private phone 79991234567")),
            ),
            patch.object(
                notification_worker.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            self.assertLogs(notification_worker.logger, level="INFO") as captured,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker.run_notification_worker(
                    self.session_factory, object()
                )

        failed = captured.records[0]
        self.assertEqual(failed.event, "bot_notification_cycle_failed")
        self.assertEqual(failed.phase, "expand_outbox")
        self.assertEqual(failed.exception_type, "RuntimeError")
        self.assertIn("private phone", failed.exception_message)
        self.assertEqual(failed.expanded_count, 0)
        self.assertNotIn(
            "79991234567", str([record.__dict__ for record in captured.records])
        )


class DeliveryStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_batch_counts_each_outcome_without_exception_values(
        self,
    ) -> None:
        recipient_ids = [uuid4() for _ in range(4)]
        notifications = [
            SimpleNamespace(
                recipient_user_id=recipient_id,
                subject_kind="issue_card",
                subject_id=uuid4(),
                bot_state="pending",
                bot_attempts=0,
                last_error=None,
            )
            for recipient_id in recipient_ids
        ]
        users = {
            recipient_ids[1]: SimpleNamespace(id=recipient_ids[1], max_user_id="2"),
            recipient_ids[2]: SimpleNamespace(id=recipient_ids[2], max_user_id="3"),
            recipient_ids[3]: SimpleNamespace(id=recipient_ids[3], max_user_id="4"),
        }
        session = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: notifications)),
            get=AsyncMock(side_effect=lambda _model, user_id: users.get(user_id)),
            commit=AsyncMock(),
        )

        async def send_message(*, user_id, text):
            if user_id == 4:
                raise RuntimeError("private phone 79991234567")

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message))

        async def is_muted(_session, user_id, _kind, _subject_id):
            return user_id == recipient_ids[1]

        with (
            patch.object(
                notification_service,
                "can_view_subject",
                new=AsyncMock(return_value=True),
            ),
            patch.object(notification_service, "is_muted", side_effect=is_muted),
        ):
            stats = await notification_service.deliver_bot_notifications_once(
                session, bot
            )

        self.assertEqual(
            (stats.processed, stats.sent, stats.failed, stats.blocked, stats.muted),
            (4, 1, 1, 1, 1),
        )
        self.assertEqual(stats.failure_types, {"RuntimeError": 1})
        self.assertIn("private phone", stats.failure_messages["RuntimeError"])
        self.assertNotIn("79991234567", stats.failure_messages["RuntimeError"])
        self.assertEqual(
            [item.bot_state for item in notifications],
            ["blocked", "muted", "sent", "failed"],
        )
        self.assertEqual(notifications[3].last_error, "RuntimeError")
        session.commit.assert_awaited_once()


class BotProcessEntryPointTests(unittest.TestCase):
    def test_console_exit_hides_exception_text(self) -> None:
        def fail_run(coroutine):
            coroutine.close()
            raise RuntimeError("private bot token 79991234567")

        with patch.object(
            bot_main.asyncio,
            "run",
            side_effect=fail_run,
        ):
            with self.assertRaises(SystemExit) as caught:
                bot_main.run()

        self.assertEqual(caught.exception.code, 1)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn(
            "private bot token",
            "".join(traceback.format_exception(caught.exception)),
        )


class BotProcessLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_failure_has_exception_message(self) -> None:
        with (
            patch.object(bot_main, "configure_logging"),
            patch.object(
                bot_main, "load_bot_token", side_effect=RuntimeError("missing config")
            ),
            patch.object(bot_main.logger, "error") as log_error,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing config"):
                await bot_main.main()

        fields = log_error.call_args.kwargs["extra"]
        self.assertEqual(fields["event"], "bot_process_failed")
        self.assertEqual(fields["exception_message"], "missing config")


if __name__ == "__main__":
    unittest.main()
