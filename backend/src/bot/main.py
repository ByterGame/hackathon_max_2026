import asyncio
import logging
from contextlib import suppress

from maxapi import Bot, Dispatcher

from ..core.config import load_bot_token, load_database_config
from ..core.logging import configure_logging, safe_exception_message
from ..db.session import create_database_engine, create_session_factory
from .diagnostics import safe_stack
from .handlers.commands import build_router
from .notification_worker import run_notification_worker

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    phase = "load_configuration"
    logger.info(
        "bot_process_starting",
        extra={"event": "bot_process_starting", "phase": phase},
    )
    try:
        token = load_bot_token()
        phase = "create_database_engine"
        engine = create_database_engine(load_database_config())
        try:
            phase = "initialize_bot"
            bot = Bot(token=token)
            dispatcher = Dispatcher()
            session_factory = create_session_factory(engine)
            dispatcher.include_routers(build_router(session_factory, token))
            notification_task = asyncio.create_task(
                run_notification_worker(session_factory, bot)
            )
            try:
                phase = "polling"
                logger.info(
                    "bot_polling_started",
                    extra={"event": "bot_polling_started", "phase": phase},
                )
                await dispatcher.start_polling(bot)
            finally:
                phase = "stop_notification_worker"
                notification_task.cancel()
                with suppress(asyncio.CancelledError):
                    await notification_task
        finally:
            phase = "dispose_database_engine"
            await engine.dispose()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.error(
            "bot_process_failed",
            extra={
                "event": "bot_process_failed",
                "phase": phase,
                "result": "failed",
                "error_code": "bot_process_failed",
                "exception_type": type(error).__name__,
                "exception_message": safe_exception_message(error),
                "stack": safe_stack(error),
            },
        )
        raise
    finally:
        logger.info("bot_process_stopped", extra={"event": "bot_process_stopped"})


def run() -> None:
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
