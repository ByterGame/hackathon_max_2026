"""Notification fan-out with current-rights checks and per-subject bot mutes."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from maxapi import Bot
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Apartment,
    BotMute,
    CompanyRegistrationRequest,
    House,
    HouseAdditionRequest,
    IssueCard,
    Notification,
    OutboxEvent,
    ResidentGrant,
    ResidentOffer,
    ResidentRequest,
    StaffAssignment,
    User,
)
from src.domain.access.requests import load_request, require_request_party
from src.domain.access.common import require_staff
from src.domain.access.rules import AccessRuleError
from src.domain.issues.service import IssueError, get_visible_card, resolve_card


class NotificationError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


REQUEST_KINDS = {
    "resident_request": "resident",
    "company_registration_request": "company_registration",
    "house_addition_request": "house_addition",
}


def _now() -> datetime:
    return datetime.now(UTC)


async def can_view_subject(
    session: AsyncSession, actor: User, kind: str, subject_id: UUID
) -> bool:
    try:
        if kind == "issue_card":
            await get_visible_card(session, actor, subject_id)
            return True
        if kind == "resident_offer":
            offer = await session.get(ResidentOffer, subject_id)
            if offer is None:
                return False
            if (
                actor.phone_verified_at is not None
                and actor.phone_number == offer.phone_number
            ):
                return True
            house = await session.get(House, offer.house_id)
            if house is None:
                return False
            await require_staff(session, actor, house.company_id)
            return True
        if kind == "resident_grant":
            grant = await session.get(ResidentGrant, subject_id)
            if grant is None:
                return False
            if grant.user_id == actor.id:
                return True
            apartment = await session.get(Apartment, grant.apartment_id)
            house = await session.get(House, apartment.house_id) if apartment else None
            if house is None:
                return False
            await require_staff(session, actor, house.company_id)
            return True
        if kind == "staff_assignment":
            assignment = await session.get(StaffAssignment, subject_id)
            if assignment is None:
                return False
            if assignment.user_id == actor.id:
                return True
            if (
                assignment.user_id is None
                and actor.phone_verified_at is not None
                and assignment.phone_number == actor.phone_number
            ):
                return True
            await require_staff(session, actor, assignment.company_id)
            return True
        request_kind = REQUEST_KINDS.get(kind)
        if request_kind is None:
            return False
        request = await load_request(
            session, kind=request_kind, request_id=subject_id
        )
        await require_request_party(
            session, actor, kind=request_kind, request=request, writing=False
        )
        return True
    except (IssueError, AccessRuleError):
        return False


async def set_mute(
    session: AsyncSession,
    actor: User,
    *,
    subject_kind: str,
    subject_id: UUID,
    is_muted: bool,
) -> bool:
    if subject_kind not in {"issue_card", "resident_request"}:
        raise NotificationError(400, "Для этой заявки настройка недоступна")
    if subject_kind == "issue_card":
        initial = await session.get(IssueCard, subject_id)
        if initial is None:
            raise NotificationError(404, "Карточка или заявка не найдена")
        await session.scalar(
            select(House)
            .where(House.id == initial.house_id)
            .with_for_update()
        )
        card = await resolve_card(session, subject_id)
        subject_id = card.id
    if not await can_view_subject(session, actor, subject_kind, subject_id):
        raise NotificationError(404, "Карточка или заявка не найдена")
    column = BotMute.issue_id if subject_kind == "issue_card" else BotMute.resident_request_id
    row = await session.scalar(
        select(BotMute)
        .where(BotMute.user_id == actor.id, column == subject_id)
        .with_for_update()
    )
    if row is None:
        row = BotMute(
            id=uuid4(),
            user_id=actor.id,
            issue_id=subject_id if subject_kind == "issue_card" else None,
            resident_request_id=(
                subject_id if subject_kind == "resident_request" else None
            ),
            is_muted=is_muted,
            updated_at=_now(),
        )
        session.add(row)
    else:
        row.is_muted = is_muted
        row.updated_at = _now()
    await session.commit()
    return is_muted


async def is_muted(
    session: AsyncSession, user_id: UUID, kind: str, subject_id: UUID
) -> bool:
    if kind not in {"issue_card", "resident_request"}:
        return False
    if kind == "issue_card":
        try:
            subject_id = (await resolve_card(session, subject_id)).id
        except IssueError:
            return False
    column = BotMute.issue_id if kind == "issue_card" else BotMute.resident_request_id
    value = await session.scalar(
        select(BotMute.is_muted).where(
            BotMute.user_id == user_id, column == subject_id
        )
    )
    return bool(value)


async def list_notifications(
    session: AsyncSession, actor: User
) -> list[tuple[Notification, OutboxEvent]]:
    rows = (
        await session.execute(
            select(Notification, OutboxEvent)
            .join(OutboxEvent, OutboxEvent.id == Notification.outbox_event_id)
            .where(Notification.recipient_user_id == actor.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(100)
        )
    ).all()
    return [
        (notification, event)
        for notification, event in rows
        if await can_view_subject(
            session, actor, notification.subject_kind, notification.subject_id
        )
    ]


async def mark_read(
    session: AsyncSession, actor: User, notification_id: UUID
) -> None:
    notification = await session.scalar(
        select(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_user_id == actor.id,
        )
        .with_for_update()
    )
    if notification is None or not await can_view_subject(
        session, actor, notification.subject_kind, notification.subject_id
    ):
        raise NotificationError(404, "Уведомление не найдено")
    if notification.read_at is None:
        notification.read_at = _now()
        await session.commit()


async def _recipient_ids(
    session: AsyncSession, event: OutboxEvent
) -> set[UUID]:
    kind = event.subject_kind
    subject_id = event.subject_id
    recipient_ids: set[UUID] = set()
    company_id: UUID | None = None
    if kind == "issue_card":
        card = await resolve_card(session, subject_id)
        house = await session.get(House, card.house_id)
        if house is None:
            return set()
        company_id = house.company_id
        now = _now()
        resident_ids = (
            await session.scalars(
                select(ResidentGrant.user_id)
                .join(Apartment, Apartment.id == ResidentGrant.apartment_id)
                .where(
                    Apartment.house_id == house.id,
                    ResidentGrant.valid_from <= now,
                    or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
                    ResidentGrant.revoked_at.is_(None),
                )
            )
        ).all()
        recipient_ids.update(resident_ids)
    elif kind == "resident_request":
        request = await session.get(ResidentRequest, subject_id)
        if request is None:
            return set()
        house = await session.get(House, request.house_id)
        if house is None:
            return set()
        company_id = house.company_id
        recipient_ids.add(request.applicant_user_id)
    elif kind == "company_registration_request":
        request = await session.get(CompanyRegistrationRequest, subject_id)
        if request is None:
            return set()
        recipient_ids.add(request.applicant_user_id)
        recipient_ids.update(
            (await session.scalars(select(User.id).where(User.kind == "support"))).all()
        )
    elif kind == "house_addition_request":
        request = await session.get(HouseAdditionRequest, subject_id)
        if request is None:
            return set()
        recipient_ids.add(request.applicant_user_id)
        company_id = request.company_id
        recipient_ids.update(
            (await session.scalars(select(User.id).where(User.kind == "support"))).all()
        )
    elif kind == "resident_offer":
        offer = await session.get(ResidentOffer, subject_id)
        if offer is None:
            return set()
        house = await session.get(House, offer.house_id)
        if house is None:
            return set()
        company_id = house.company_id
        target_id = await session.scalar(
            select(User.id).where(
                User.phone_number == offer.phone_number,
                User.phone_verified_at.is_not(None),
            )
        )
        if target_id is not None:
            recipient_ids.add(target_id)
    elif kind == "resident_grant":
        grant = await session.get(ResidentGrant, subject_id)
        if grant is None:
            return set()
        recipient_ids.add(grant.user_id)
        apartment = await session.get(Apartment, grant.apartment_id)
        house = await session.get(House, apartment.house_id) if apartment else None
        company_id = house.company_id if house else None
    elif kind == "staff_assignment":
        assignment = await session.get(StaffAssignment, subject_id)
        if assignment is None:
            return set()
        company_id = assignment.company_id
        if assignment.user_id is not None:
            recipient_ids.add(assignment.user_id)
    if company_id is not None:
        staff_filter = []
        if kind == "issue_card":
            staff_filter.append(StaffAssignment.can_manage_issues.is_(True))
        elif kind == "resident_request":
            staff_filter.append(StaffAssignment.can_manage_residents.is_(True))
        elif kind == "house_addition_request":
            staff_filter.append(StaffAssignment.can_manage_staff.is_(True))
        staff_ids = (
            await session.scalars(
                select(StaffAssignment.user_id).where(
                    StaffAssignment.company_id == company_id,
                    StaffAssignment.user_id.is_not(None),
                    StaffAssignment.revoked_at.is_(None),
                    *staff_filter,
                )
            )
        ).all()
        recipient_ids.update(staff_ids)
    actor_id = (event.payload or {}).get("actor_user_id")
    if isinstance(actor_id, str):
        try:
            recipient_ids.discard(UUID(actor_id))
        except ValueError:
            pass
    visible_ids: set[UUID] = set()
    for user_id in recipient_ids:
        user = await session.get(User, user_id)
        if user is not None and await can_view_subject(
            session, user, kind, subject_id
        ):
            visible_ids.add(user_id)
    return visible_ids


async def expand_outbox_once(session: AsyncSession, *, limit: int = 20) -> int:
    """Fan out committed events; concurrent workers skip rows locked by peers."""
    now = _now()
    events = (
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.processed_at.is_(None),
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for event in events:
        try:
            recipients = await _recipient_ids(session, event)
        except (IssueError, AccessRuleError):
            recipients = set()
        for user_id in recipients:
            await session.execute(
                insert(Notification)
                .values(
                    id=uuid4(),
                    outbox_event_id=event.id,
                    recipient_user_id=user_id,
                    subject_kind=event.subject_kind,
                    subject_id=event.subject_id,
                    created_at=now,
                    bot_state="pending",
                    bot_attempts=0,
                )
                .on_conflict_do_nothing(
                    constraint="uq_notifications_event_recipient"
                )
            )
        event.processed_at = now
    if events:
        await session.commit()
    return len(events)


async def deliver_bot_notifications_once(
    session: AsyncSession, bot: Bot, *, limit: int = 10
) -> int:
    """Send generic notices only after rechecking rights immediately before send."""
    now = _now()
    rows = (
        await session.scalars(
            select(Notification)
            .where(
                Notification.bot_state.in_(["pending", "failed"]),
                or_(
                    Notification.next_bot_attempt_at.is_(None),
                    Notification.next_bot_attempt_at <= now,
                ),
            )
            .order_by(Notification.created_at, Notification.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for notification in rows:
        user = await session.get(User, notification.recipient_user_id)
        if user is None or not await can_view_subject(
            session, user, notification.subject_kind, notification.subject_id
        ):
            notification.bot_state = "blocked"
            continue
        if await is_muted(
            session, user.id, notification.subject_kind, notification.subject_id
        ):
            notification.bot_state = "muted"
            continue
        if user.max_user_id is None:
            notification.bot_state = "blocked"
            continue
        try:
            max_user_id = int(user.max_user_id)
            await asyncio.wait_for(
                bot.send_message(
                    user_id=max_user_id,
                    text="Есть обновление обращения. Откройте мини-приложение MAX, чтобы посмотреть детали.",
                ),
                timeout=10,
            )
            notification.bot_state = "sent"
            notification.bot_sent_at = _now()
            notification.last_error = None
        except Exception as error:
            notification.bot_state = "failed"
            notification.bot_attempts += 1
            wait_seconds = min(24 * 3600, 30 * (2 ** min(notification.bot_attempts, 10)))
            notification.next_bot_attempt_at = _now() + timedelta(seconds=wait_seconds)
            notification.last_error = type(error).__name__
    if rows:
        await session.commit()
    return len(rows)
