"""Background fan-out and MAX delivery for committed outbox events."""

import asyncio
import logging
import time
from uuid import uuid4

from maxapi import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.diagnostics import safe_stack
from src.core.logging import safe_exception_message
from src.domain.notifications.service import (
    BotDeliveryStats,
    deliver_bot_notifications_once,
    expand_outbox_once,
)

logger = logging.getLogger(__name__)


async def run_notification_worker(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot
) -> None:
    last_heartbeat = 0.0
    while True:
        cycle_id = uuid4().hex
        started = time.monotonic()
        expanded_count = 0
        delivery_stats = BotDeliveryStats()
        phase = "expand_outbox"
        try:
            async with session_factory() as session:
                expanded_count = await expand_outbox_once(session)
            phase = "deliver_notifications"
            async with session_factory() as session:
                delivery_stats = await deliver_bot_notifications_once(session, bot)
        except asyncio.CancelledError:
            logger.info(
                "bot_notification_worker_stopped",
                extra={
                    "event": "bot_notification_worker_stopped",
                    "cycle_id": cycle_id,
                    "phase": phase,
                    "result": "cancelled",
                    "expanded_count": expanded_count,
                    "delivery_count": delivery_stats.processed,
                    "sent_count": delivery_stats.sent,
                    "failed_count": delivery_stats.failed,
                    "blocked_count": delivery_stats.blocked,
                    "muted_count": delivery_stats.muted,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        except Exception as error:
            logger.error(
                "bot_notification_cycle_failed",
                extra={
                    "event": "bot_notification_cycle_failed",
                    "cycle_id": cycle_id,
                    "phase": phase,
                    "result": "failed",
                    "expanded_count": expanded_count,
                    "delivery_count": delivery_stats.processed,
                    "sent_count": delivery_stats.sent,
                    "failed_count": delivery_stats.failed,
                    "blocked_count": delivery_stats.blocked,
                    "muted_count": delivery_stats.muted,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error_code": "worker_cycle_failed",
                    "exception_type": type(error).__name__,
                    "exception_message": safe_exception_message(error),
                    "stack": safe_stack(error),
                },
            )
        else:
            now = time.monotonic()
            active = bool(expanded_count or delivery_stats.processed)
            if active or now - last_heartbeat >= 60:
                logger.info(
                    "bot_notification_cycle_finished",
                    extra={
                        "event": "bot_notification_cycle_finished",
                        "cycle_id": cycle_id,
                        "phase": "complete",
                        "result": "processed" if active else "idle",
                        "expanded_count": expanded_count,
                        "delivery_count": delivery_stats.processed,
                        "sent_count": delivery_stats.sent,
                        "failed_count": delivery_stats.failed,
                        "blocked_count": delivery_stats.blocked,
                        "muted_count": delivery_stats.muted,
                        "duration_ms": round((now - started) * 1000),
                    },
                )
                last_heartbeat = now
            for exception_type, count in delivery_stats.failure_types.items():
                logger.warning(
                    "bot_notification_delivery_failed",
                    extra={
                        "event": "bot_notification_delivery_failed",
                        "cycle_id": cycle_id,
                        "phase": "deliver_notifications",
                        "result": "failed",
                        "exception_type": exception_type,
                        "exception_message": delivery_stats.failure_messages.get(
                            exception_type
                        ),
                        "failed_count": count,
                    },
                )
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info(
                "bot_notification_worker_stopped",
                extra={
                    "event": "bot_notification_worker_stopped",
                    "cycle_id": cycle_id,
                    "phase": "sleep",
                    "result": "cancelled",
                },
            )
            raise
