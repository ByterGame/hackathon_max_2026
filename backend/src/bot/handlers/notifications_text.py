"""Text commands for the same notification settings as the mini-app."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.domain.notifications.service import (
    NotificationError,
    can_view_subject,
    is_muted,
    list_notifications,
    mark_read,
    set_mute,
)

NOTIFICATION_HELP = (
    "Уведомления: /notifications [страница] — события постранично; "
    "/read UUID_уведомления — пометить прочитанным.\n"
    "/mute issue UUID_карточки или /mute access UUID_заявки — "
    "отключить сообщения бота; /unmute — включить; /mutestatus — проверить. "
    "Уведомления внутри мини-приложения остаются доступными."
)
_PAGE_SIZE = 10


def _uuid(value: str) -> UUID:
    try:
        return UUID(value.strip())
    except ValueError as error:
        raise ValueError("Укажите UUID уведомления или заявки") from error


def _subject(argument: str) -> tuple[str, UUID]:
    kind, _, identifier = argument.partition(" ")
    aliases = {"issue": "issue_card", "access": "resident_request"}
    subject_kind = aliases.get(kind.strip().lower())
    if subject_kind is None or not identifier.strip():
        raise ValueError("Укажите issue или access и UUID заявки")
    return subject_kind, _uuid(identifier)


def _page(argument: str) -> int:
    if not argument.strip():
        return 1
    try:
        page = int(argument.strip())
    except ValueError as error:
        raise ValueError("Укажите положительный номер страницы") from error
    if page < 1 or page > 10_000:
        raise ValueError("Укажите положительный номер страницы")
    return page


async def handle_notification_text(
    session: AsyncSession, actor: User, text: str
) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command = command.lower()
    if command not in {
        "/notificationhelp",
        "/notifications",
        "/read",
        "/mute",
        "/unmute",
        "/mutestatus",
    }:
        return None
    if command == "/notificationhelp":
        return NOTIFICATION_HELP
    try:
        if command == "/notifications":
            page = _page(argument)
            rows = await list_notifications(session, actor)
            if not rows:
                return "Уведомлений пока нет."
            start = (page - 1) * _PAGE_SIZE
            selected = rows[start : start + _PAGE_SIZE]
            if not selected:
                return "На этой странице уведомлений нет. Начните с /notifications 1."
            total_pages = (len(rows) + _PAGE_SIZE - 1) // _PAGE_SIZE
            lines = [f"Уведомления · страница {page}/{total_pages}:"]
            for notification, event in selected:
                marker = "●" if notification.read_at is None else "✓"
                lines.append(
                    f"{marker} {event.event_kind} · {notification.subject_kind} "
                    f"{notification.subject_id}\nID уведомления: {notification.id}"
                )
            if page < total_pages:
                lines.append(f"Дальше: /notifications {page + 1}")
            if page > 1:
                lines.append(f"Назад: /notifications {page - 1}")
            return "\n".join(lines)[:3900]
        if command == "/read":
            await mark_read(session, actor, _uuid(argument))
            return "Уведомление отмечено прочитанным."
        kind, subject_id = _subject(argument)
        if command == "/mutestatus":
            if not await can_view_subject(session, actor, kind, subject_id):
                raise NotificationError(404, "Заявка не найдена")
            muted = await is_muted(session, actor.id, kind, subject_id)
            return "Сообщения бота отключены." if muted else "Сообщения бота включены."
        muted = await set_mute(
            session,
            actor,
            subject_kind=kind,
            subject_id=subject_id,
            is_muted=command == "/mute",
        )
        return "Сообщения бота отключены." if muted else "Сообщения бота включены."
    except (ValueError, NotificationError) as error:
        await session.rollback()
        return f"Не получилось: {error}"
