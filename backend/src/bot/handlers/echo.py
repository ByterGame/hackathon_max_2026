from maxapi import Router
from maxapi.types import MessageCreated

router = Router()


@router.message_created()
async def echo_message(event: MessageCreated) -> None:
    body = event.message.body
    if body is None:
        return

    attachments = body.attachments or None
    if body.text is None and attachments is None:
        return

    await event.message.answer(text=body.text, attachments=attachments)
