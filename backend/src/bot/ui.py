"""Shared presentation and persistent conversation state for the MAX bot."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from maxapi.enums import AttachmentType
from maxapi.types import CallbackButton
from maxapi.types.attachments import (
    AttachmentButton,
    ButtonsPayload,
    RequestContactButton,
)
from maxapi.types.input_media import InputMediaBuffer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import BotDialog


@dataclass(frozen=True)
class Button:
    text: str
    payload: str


@dataclass
class UiReply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    request_contact: bool = False
    media: InputMediaBuffer | None = None


def keyboard_for(reply: UiReply) -> list[AttachmentButton]:
    """Build the current inline keyboard; never leave stale buttons attached."""
    rows = [
        [CallbackButton(text=button.text, payload=button.payload) for button in row]
        for row in reply.buttons
        if row
    ]
    if reply.request_contact:
        rows.insert(0, [RequestContactButton(text="Поделиться номером")])
    if not any(button.payload == "menu" for row in reply.buttons for button in row):
        rows.append([CallbackButton(text="Главное меню", payload="menu")])
    return [
        AttachmentButton(
            type=AttachmentType.INLINE_KEYBOARD,
            payload=ButtonsPayload(buttons=rows),
        )
    ]


async def get_dialog(session: AsyncSession, user_id: UUID) -> BotDialog | None:
    """Lock an existing dialog until the current bot event is handled."""
    return await session.scalar(
        select(BotDialog).where(BotDialog.user_id == user_id).with_for_update()
    )


async def set_dialog(
    session: AsyncSession,
    user_id: UUID,
    *,
    flow_kind: str,
    step: str,
    data: dict[str, object] | None = None,
    draft_id: UUID | None = None,
) -> BotDialog:
    dialog = await get_dialog(session, user_id)
    if dialog is None:
        dialog = BotDialog(user_id=user_id)
        session.add(dialog)
    dialog.flow_kind = flow_kind
    dialog.step = step
    dialog.data = dict(data or {})
    dialog.draft_id = draft_id
    dialog.updated_at = datetime.now(UTC)
    await session.flush()
    return dialog


async def update_dialog(
    dialog: BotDialog,
    *,
    step: str | None = None,
    data: dict[str, object] | None = None,
    draft_id: UUID | None = None,
) -> None:
    if step is not None:
        dialog.step = step
    if data is not None:
        dialog.data = dict(data)
    if draft_id is not None:
        dialog.draft_id = draft_id
    dialog.updated_at = datetime.now(UTC)


async def clear_dialog(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(delete(BotDialog).where(BotDialog.user_id == user_id))
