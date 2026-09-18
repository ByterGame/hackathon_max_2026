import asyncio
import logging
import os

from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated


dispatcher = Dispatcher()


@dispatcher.message_created()
async def echo_message(event: MessageCreated) -> None:
    body = event.message.body
    if body is None:
        return

    attachments = body.attachments or None
    if body.text is None and attachments is None:
        return

    await event.message.answer(
        text=body.text,
        attachments=attachments,
    )


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    bot = Bot(token=token)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
