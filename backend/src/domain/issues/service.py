"""Database-backed issue scenarios shared by the mini-app and the bot."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Apartment,
    AuditEvent,
    BotMute,
    House,
    IssueCard,
    IssueCategory,
    IssueMessage,
    IssueReport,
    IssueSupport,
    IssueTarget,
    OutboxEvent,
    ResidentGrant,
    StaffAssignment,
    User,
)

from .rules import ACTIVE_STATUSES, ALL_STATUSES, CLOSE_RESULTS, can_merge_issues, can_view_issue, primary_issue_id


class IssueError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


def _record_issue_event(
    session: AsyncSession,
    actor: User,
    card_id: UUID,
    action: str,
    *,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    """Keep issue history and a notification event in the same transaction."""
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind="issue_card",
            entity_id=card_id,
            action=action,
            actor_user_id=actor.id,
            before_data=before,
            after_data=after,
        )
    )
    session.add(
        OutboxEvent(
            id=uuid4(),
            event_kind=f"issues.card.{action}",
            subject_kind="issue_card",
            subject_id=card_id,
            payload={"actor_user_id": str(actor.id)},
        )
    )


async def _staff_assignment(
    session: AsyncSession, actor: User, house: House, *, for_update: bool = False
) -> StaffAssignment | None:
    if actor.kind != "employee":
        return None
    query = select(StaffAssignment).where(
        StaffAssignment.user_id == actor.id,
        StaffAssignment.company_id == house.company_id,
        StaffAssignment.revoked_at.is_(None),
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def _resident_locations(
    session: AsyncSession, actor: User, house: House
) -> tuple[set[UUID], set[int]]:
    if actor.kind != "resident":
        return set(), set()
    now = _now()
    rows = (
        await session.execute(
            select(Apartment.id, Apartment.entrance_number)
            .join(ResidentGrant, ResidentGrant.apartment_id == Apartment.id)
            .where(
                ResidentGrant.user_id == actor.id,
                Apartment.house_id == house.id,
                ResidentGrant.valid_from <= now,
                or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
                ResidentGrant.revoked_at.is_(None),
            )
        )
    ).all()
    return {row.id for row in rows}, {row.entrance_number for row in rows}


async def _house(session: AsyncSession, house_id: UUID) -> House:
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    return house


async def _lock_issue_house(session: AsyncSession, card_id: UUID) -> IssueCard:
    """Serialize every issue mutation for one house, including merges.

    We read only the immutable house ID before taking the house lock, then
    resolve merge redirects again. A concurrent merge cannot move reports or
    supports between our resolution and the write.
    """
    original = await session.get(IssueCard, card_id)
    if original is None:
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    house = await session.scalar(
        select(House)
        .where(House.id == original.house_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    return await resolve_card(session, card_id)


async def _lock_visible_card(
    session: AsyncSession, actor: User, card_id: UUID
) -> IssueCard:
    card = await _lock_issue_house(session, card_id)
    if not await _can_view(session, actor, card):
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    return card


async def resolve_card(session: AsyncSession, card_id: UUID) -> IssueCard:
    """Follow merge redirects; old URLs always resolve to the current card."""
    seen: set[UUID] = set()
    current_id = card_id
    while current_id not in seen:
        seen.add(current_id)
        card = await session.get(IssueCard, current_id, populate_existing=True)
        if card is None:
            raise IssueError("issue_not_found", "Проблема не найдена", 404)
        if card.merged_into_id is None:
            return card
        current_id = card.merged_into_id
    raise IssueError("merge_cycle", "Нарушена цепочка объединения", 500)


async def merged_card_ids(session: AsyncSession, primary_id: UUID) -> set[UUID]:
    """Include every source card whose history now belongs to the primary."""
    result = {primary_id}
    frontier = {primary_id}
    while frontier:
        children = set(
            (
                await session.scalars(
                    select(IssueCard.id).where(IssueCard.merged_into_id.in_(frontier))
                )
            ).all()
        ) - result
        result.update(children)
        frontier = children
    return result


async def _can_view(session: AsyncSession, actor: User, card: IssueCard) -> bool:
    house = await _house(session, card.house_id)
    if await _staff_assignment(session, actor, house) is not None:
        return True
    apartments, entrances = await _resident_locations(session, actor, house)
    if not apartments:
        return False
    targets = (
        await session.scalars(select(IssueTarget).where(IssueTarget.card_id == card.id))
    ).all()
    return can_view_issue(
        has_house_access=True,
        is_author=card.author_user_id == actor.id,
        scope_all_house=card.scope_all_house,
        granted_apartment_ids=apartments,
        granted_entrance_numbers=entrances,
        target_apartment_ids={target.apartment_id for target in targets if target.apartment_id},
        target_entrance_numbers={
            target.entrance_number for target in targets if target.entrance_number is not None
        },
    )


async def get_visible_card(
    session: AsyncSession, actor: User, card_id: UUID
) -> IssueCard:
    card = await resolve_card(session, card_id)
    if not await _can_view(session, actor, card):
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    return card


async def list_visible_cards(
    session: AsyncSession, actor: User, house_id: UUID, *, include_closed: bool = False
) -> list[IssueCard]:
    house = await _house(session, house_id)
    staff = await _staff_assignment(session, actor, house)
    if staff is None:
        apartments, _ = await _resident_locations(session, actor, house)
        if not apartments:
            raise IssueError("house_access_denied", "Нет доступа к дому", 403)
    query = select(IssueCard).where(
        IssueCard.house_id == house_id,
        IssueCard.merged_into_id.is_(None),
    )
    if not include_closed:
        query = query.where(IssueCard.status != "closed")
    cards = (
        await session.scalars(
            query.order_by(IssueCard.created_at.desc(), IssueCard.id.desc())
        )
    ).all()
    if staff is not None:
        return list(cards)
    return [card for card in cards if await _can_view(session, actor, card)]


async def _get_or_create_apartment(
    session: AsyncSession, house: House, entrance_number: int, apartment_number: int
) -> UUID:
    if entrance_number <= 0 or apartment_number <= 0:
        raise IssueError("invalid_target", "Номера подъезда и квартиры должны быть положительными")
    if house.entrance_count is not None and entrance_number > house.entrance_count:
        raise IssueError("invalid_target", "В доме нет такого подъезда")
    proposed_id = uuid4()
    statement = (
        insert(Apartment)
        .values(
            id=proposed_id,
            house_id=house.id,
            entrance_number=entrance_number,
            apartment_number=apartment_number,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Apartment.house_id,
                Apartment.entrance_number,
                Apartment.apartment_number,
            ]
        )
        .returning(Apartment.id)
    )
    inserted_id = await session.scalar(statement)
    if inserted_id is not None:
        return inserted_id
    existing_id = await session.scalar(
        select(Apartment.id).where(
            Apartment.house_id == house.id,
            Apartment.entrance_number == entrance_number,
            Apartment.apartment_number == apartment_number,
        )
    )
    if existing_id is None:
        raise IssueError("apartment_conflict", "Не удалось определить квартиру", 409)
    return existing_id


async def create_card(
    session: AsyncSession,
    actor: User,
    *,
    house_id: UUID,
    category_id: UUID,
    title: str,
    description: str,
    scope_all_house: bool,
    target_entrances: list[int],
    target_apartments: list[tuple[int, int]],
) -> IssueCard:
    house = await _house(session, house_id)
    apartments, _ = await _resident_locations(session, actor, house)
    if not apartments:
        raise IssueError("house_access_denied", "Нужен действующий доступ к дому", 403)
    category = await session.get(IssueCategory, category_id)
    if category is None or not category.is_active:
        raise IssueError("invalid_category", "Категория не найдена")
    title = title.strip()
    description = description.strip()
    if not title or not description:
        raise IssueError("empty_issue", "Нужны краткая формулировка и описание")
    if scope_all_house and (target_entrances or target_apartments):
        raise IssueError("invalid_scope", "Для всего дома отдельные цели не указываются")
    if not scope_all_house and not (target_entrances or target_apartments):
        raise IssueError("invalid_scope", "Укажите квартиру, подъезд или весь дом")
    now = _now()
    card = IssueCard(
        id=uuid4(),
        house_id=house.id,
        author_user_id=actor.id,
        category_id=category.id,
        title=title,
        status="open",
        scope_all_house=scope_all_house,
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(card)
    session.add(
        IssueReport(
            id=uuid4(),
            card_id=card.id,
            origin_card_id=card.id,
            author_user_id=actor.id,
            raw_description=description,
            created_at=now,
        )
    )
    session.add(IssueSupport(card_id=card.id, user_id=actor.id, supported_at=now))
    for entrance_number in set(target_entrances):
        if entrance_number <= 0 or (
            house.entrance_count is not None and entrance_number > house.entrance_count
        ):
            raise IssueError("invalid_target", "В доме нет такого подъезда")
        session.add(
            IssueTarget(
                id=uuid4(),
                card_id=card.id,
                house_id=house.id,
                entrance_number=entrance_number,
            )
        )
    for entrance_number, apartment_number in set(target_apartments):
        apartment_id = await _get_or_create_apartment(
            session, house, entrance_number, apartment_number
        )
        session.add(
            IssueTarget(
                id=uuid4(),
                card_id=card.id,
                house_id=house.id,
                apartment_id=apartment_id,
            )
        )
    _record_issue_event(session, actor, card.id, "created")
    await session.flush()
    return card


async def edit_card(
    session: AsyncSession,
    actor: User,
    card_id: UUID,
    *,
    expected_version: int,
    category_id: UUID,
    title: str,
    scope_all_house: bool,
    target_entrances: list[int],
    target_apartments: list[tuple[int, int]],
) -> IssueCard:
    """Edit only the shared summary; immutable resident reports remain untouched."""
    card = await _lock_issue_house(session, card_id)
    house = await _house(session, card.house_id)
    staff = await _staff_assignment(session, actor, house, for_update=True)
    if staff is None or not staff.can_manage_issues:
        raise IssueError("issue_permission_denied", "Нет права менять карточку", 403)
    if card.version != expected_version:
        raise IssueError("stale_card", "Карточка изменилась; обновите страницу", 409)
    category = await session.get(IssueCategory, category_id)
    if category is None or not category.is_active:
        raise IssueError("invalid_category", "Категория не найдена")
    cleaned_title = title.strip()
    if not cleaned_title:
        raise IssueError("empty_title", "Нужно название проблемы")
    if scope_all_house and (target_entrances or target_apartments):
        raise IssueError("invalid_scope", "Для всего дома отдельные цели не указываются")
    if not scope_all_house and not (target_entrances or target_apartments):
        raise IssueError("invalid_scope", "Укажите квартиру, подъезд или весь дом")
    old_summary = {
        "title": card.title,
        "category_id": str(card.category_id),
        "scope_all_house": card.scope_all_house,
    }
    previous_targets = (
        await session.scalars(select(IssueTarget).where(IssueTarget.card_id == card.id))
    ).all()
    old_summary["targets"] = [
        {
            "entrance_number": item.entrance_number,
            "apartment_id": str(item.apartment_id) if item.apartment_id else None,
        }
        for item in previous_targets
    ]
    new_targets: list[dict[str, object]] = []
    await session.execute(delete(IssueTarget).where(IssueTarget.card_id == card.id))
    for entrance_number in set(target_entrances):
        if entrance_number <= 0 or (
            house.entrance_count is not None and entrance_number > house.entrance_count
        ):
            raise IssueError("invalid_target", "В доме нет такого подъезда")
        session.add(
            IssueTarget(
                id=uuid4(),
                card_id=card.id,
                house_id=house.id,
                entrance_number=entrance_number,
            )
        )
        new_targets.append({"entrance_number": entrance_number, "apartment_id": None})
    for entrance_number, apartment_number in set(target_apartments):
        apartment_id = await _get_or_create_apartment(
            session, house, entrance_number, apartment_number
        )
        session.add(
            IssueTarget(
                id=uuid4(),
                card_id=card.id,
                house_id=house.id,
                apartment_id=apartment_id,
            )
        )
        new_targets.append({"entrance_number": None, "apartment_id": str(apartment_id)})
    card.title = cleaned_title
    card.category_id = category.id
    card.scope_all_house = scope_all_house
    card.updated_at = _now()
    card.version += 1
    _record_issue_event(
        session,
        actor,
        card.id,
        "edited",
        before=old_summary,
        after={
            "title": card.title,
            "category_id": str(card.category_id),
            "scope_all_house": card.scope_all_house,
            "targets": new_targets,
        },
    )
    await session.flush()
    return card


async def support_card(
    session: AsyncSession, actor: User, card_id: UUID, description: str | None = None
) -> tuple[IssueCard, UUID | None]:
    card = await _lock_visible_card(session, actor, card_id)
    if card.status == "closed":
        raise IssueError("issue_closed", "Закрытую проблему нельзя поддержать", 409)
    house = await _house(session, card.house_id)
    apartments, _ = await _resident_locations(session, actor, house)
    if not apartments:
        raise IssueError("resident_required", "Поддержать проблему может только житель", 403)
    inserted = await session.scalar(
        insert(IssueSupport)
        .values(card_id=card.id, user_id=actor.id, supported_at=_now())
        .on_conflict_do_nothing(index_elements=[IssueSupport.card_id, IssueSupport.user_id])
        .returning(IssueSupport.user_id)
    )
    if inserted is not None:
        _record_issue_event(session, actor, card.id, "supported")
    report_id = None
    if description and description.strip():
        report_id = uuid4()
        session.add(
            IssueReport(
                id=report_id,
                card_id=card.id,
                origin_card_id=card.id,
                author_user_id=actor.id,
                raw_description=description.strip(),
                created_at=_now(),
            )
        )
        _record_issue_event(
            session, actor, card.id, "report_added", after={"report_id": str(report_id)}
        )
    await session.flush()
    return card, report_id


async def add_comment(
    session: AsyncSession, actor: User, card_id: UUID, text: str
) -> IssueMessage:
    card = await _lock_visible_card(session, actor, card_id)
    body = text.strip()
    if not body:
        raise IssueError("empty_comment", "Комментарий не может быть пустым")
    house = await _house(session, card.house_id)
    staff = await _staff_assignment(session, actor, house, for_update=True)
    if staff is not None:
        if not staff.can_manage_issues:
            raise IssueError("issue_permission_denied", "Нет права отвечать от УК", 403)
        kind = "official_uk"
    else:
        kind = "resident_comment"
    message = IssueMessage(
        id=uuid4(),
        card_id=card.id,
        origin_card_id=card.id,
        author_user_id=actor.id,
        kind=kind,
        body=body,
        created_at=_now(),
    )
    session.add(message)
    _record_issue_event(
        session, actor, card.id, "comment_added", after={"message_id": str(message.id)}
    )
    await session.flush()
    return message


async def set_status(
    session: AsyncSession,
    actor: User,
    card_id: UUID,
    *,
    status: str,
    note: str | None,
    close_result: str | None = None,
) -> IssueCard:
    card = await _lock_issue_house(session, card_id)
    house = await _house(session, card.house_id)
    staff = await _staff_assignment(session, actor, house, for_update=True)
    if staff is None or not staff.can_manage_issues:
        raise IssueError("issue_permission_denied", "Нет права менять статус", 403)
    if status not in ALL_STATUSES:
        raise IssueError("invalid_status", "Неизвестный статус")
    if card.status == "closed" and status != "closed":
        raise IssueError("reopen_by_author_only", "Переоткрыть может только автор", 403)
    old_status = card.status
    old_note = card.current_note
    if status == "closed":
        if close_result not in CLOSE_RESULTS or not note or not note.strip():
            raise IssueError("close_requires_result", "Укажите результат и пояснение")
        card.close_result = close_result
        card.closed_at = _now()
    else:
        if close_result is not None:
            raise IssueError("invalid_close_result", "Результат нужен только при закрытии")
        card.close_result = None
        card.closed_at = None
    card.status = status
    card.current_note = note.strip() if note else None
    card.updated_at = _now()
    card.version += 1
    _record_issue_event(
        session,
        actor,
        card.id,
        "status_changed",
        before={"status": old_status, "note": old_note},
        after={"status": status, "note": card.current_note, "close_result": card.close_result},
    )
    await session.flush()
    return card


async def reopen_card(
    session: AsyncSession, actor: User, card_id: UUID, comment: str
) -> IssueCard:
    card = await _lock_visible_card(session, actor, card_id)
    if card.author_user_id != actor.id:
        raise IssueError("reopen_by_author_only", "Переоткрыть может только автор", 403)
    if card.status != "closed":
        raise IssueError("issue_not_closed", "Проблема ещё не закрыта")
    if not comment.strip():
        raise IssueError("reopen_comment_required", "Нужно пояснить причину")
    card.status = "open"
    card.close_result = None
    card.closed_at = None
    card.current_note = None
    card.updated_at = _now()
    card.version += 1
    session.add(
        IssueMessage(
            id=uuid4(),
            card_id=card.id,
            origin_card_id=card.id,
            author_user_id=actor.id,
            kind="resident_comment",
            body=comment.strip(),
            created_at=_now(),
        )
    )
    _record_issue_event(
        session,
        actor,
        card.id,
        "reopened",
        before={"status": "closed"},
        after={"status": "open"},
    )
    await session.flush()
    return card


async def merge_cards(
    session: AsyncSession,
    actor: User,
    left_id: UUID,
    right_id: UUID,
    *,
    final_title: str,
    final_status: str,
    final_note: str | None,
) -> IssueCard:
    if left_id == right_id:
        raise IssueError("same_issue", "Нельзя объединить карточку саму с собой")
    first = await session.get(IssueCard, left_id)
    second = await session.get(IssueCard, right_id)
    if first is None or second is None:
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    if first.house_id != second.house_id:
        raise IssueError("cannot_merge", "Объединить можно только две проблемы одного дома")
    house = await session.scalar(
        select(House)
        .where(House.id == first.house_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    ids = sorted((left_id, right_id))
    cards = (
        await session.scalars(
            select(IssueCard)
            .where(IssueCard.id.in_(ids))
            .order_by(IssueCard.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(cards) != 2:
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    left, right = cards
    if not can_merge_issues(
        left_id=left.id,
        right_id=right.id,
        left_house_id=left.house_id,
        right_house_id=right.house_id,
        left_status=left.status,
        right_status=right.status,
        left_merged=left.merged_into_id is not None,
        right_merged=right.merged_into_id is not None,
    ):
        raise IssueError("cannot_merge", "Объединить можно только две открытые проблемы одного дома")
    staff = await _staff_assignment(session, actor, house, for_update=True)
    if staff is None or not staff.can_manage_issues:
        raise IssueError("issue_permission_denied", "Нет права объединять проблемы", 403)
    if final_status not in ACTIVE_STATUSES or not final_title.strip():
        raise IssueError("invalid_merge_result", "Укажите итоговые статус и название")
    primary_id = primary_issue_id(left.id, left.created_at, right.id, right.created_at)
    primary, source = (left, right) if left.id == primary_id else (right, left)
    before_merge = {
        "primary": {
            "id": str(primary.id),
            "title": primary.title,
            "status": primary.status,
            "note": primary.current_note,
            "scope_all_house": primary.scope_all_house,
        },
        "source": {
            "id": str(source.id),
            "title": source.title,
            "status": source.status,
            "note": source.current_note,
            "scope_all_house": source.scope_all_house,
        },
    }
    targets = (
        await session.scalars(select(IssueTarget).where(IssueTarget.card_id.in_(ids)))
    ).all()
    for label, merged_card in (("primary", primary), ("source", source)):
        before_merge[label]["targets"] = [
            {
                "entrance_number": target.entrance_number,
                "apartment_id": str(target.apartment_id) if target.apartment_id else None,
            }
            for target in targets
            if target.card_id == merged_card.id
        ]
    if primary.scope_all_house or source.scope_all_house:
        primary.scope_all_house = True
        await session.execute(delete(IssueTarget).where(IssueTarget.card_id == primary.id))
    else:
        existing = {(row.entrance_number, row.apartment_id) for row in targets if row.card_id == primary.id}
        for row in targets:
            key = (row.entrance_number, row.apartment_id)
            if row.card_id == source.id and key not in existing:
                session.add(
                    IssueTarget(
                        id=uuid4(),
                        card_id=primary.id,
                        house_id=primary.house_id,
                        entrance_number=row.entrance_number,
                        apartment_id=row.apartment_id,
                    )
                )
                existing.add(key)
    source_supporters = (
        await session.scalars(select(IssueSupport.user_id).where(IssueSupport.card_id == source.id))
    ).all()
    for user_id in source_supporters:
        await session.execute(
            insert(IssueSupport)
            .values(card_id=primary.id, user_id=user_id, supported_at=_now())
            .on_conflict_do_nothing(index_elements=[IssueSupport.card_id, IssueSupport.user_id])
        )
    await session.execute(delete(IssueSupport).where(IssueSupport.card_id == source.id))
    await session.execute(delete(IssueTarget).where(IssueTarget.card_id == source.id))
    primary_mutes = {
        row.user_id: row
        for row in (
            await session.scalars(
                select(BotMute)
                .where(BotMute.issue_id == primary.id)
                .with_for_update()
            )
        ).all()
    }
    source_mutes = (
        await session.scalars(
            select(BotMute)
            .where(BotMute.issue_id == source.id)
            .with_for_update()
        )
    ).all()
    for source_mute in source_mutes:
        existing_mute = primary_mutes.get(source_mute.user_id)
        if existing_mute is None:
            source_mute.issue_id = primary.id
            primary_mutes[source_mute.user_id] = source_mute
        else:
            existing_mute.is_muted = existing_mute.is_muted or source_mute.is_muted
            await session.delete(source_mute)
    await session.execute(
        update(IssueReport).where(IssueReport.card_id == source.id).values(card_id=primary.id)
    )
    await session.execute(
        update(IssueMessage).where(IssueMessage.card_id == source.id).values(card_id=primary.id)
    )
    primary.title = final_title.strip()
    primary.status = final_status
    primary.current_note = final_note.strip() if final_note else None
    primary.updated_at = _now()
    primary.version += 1
    source.merged_into_id = primary.id
    source.updated_at = _now()
    source.version += 1
    _record_issue_event(
        session,
        actor,
        primary.id,
        "merged",
        before=before_merge,
        after={
            "source_card_id": str(source.id),
            "title": primary.title,
            "status": final_status,
            "note": primary.current_note,
            "scope_all_house": primary.scope_all_house,
            "targets": (
                []
                if primary.scope_all_house
                else [
                    {
                        "entrance_number": entrance_number,
                        "apartment_id": str(apartment_id) if apartment_id else None,
                    }
                    for entrance_number, apartment_id in existing
                ]
            ),
        },
    )
    await session.flush()
    return primary
