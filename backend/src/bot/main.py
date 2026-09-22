import asyncio

from maxapi import Bot, Dispatcher

from ..core.config import load_bot_token
from ..core.logging import configure_logging
from .handlers.echo import router as echo_router


async def main() -> None:
    configure_logging()
    bot = Bot(token=load_bot_token())
    dispatcher = Dispatcher()
    dispatcher.include_routers(echo_router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
