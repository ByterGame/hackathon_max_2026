"""Заявки жителей, предложения УК и действующие привязки к квартирам."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Apartment,
    House,
    ResidentGrant,
    ResidentOffer,
    ResidentRequest,
    User,
)
from src.domain.access.common import (
    audit,
    commit_or_conflict,
    require_row,
    require_staff,
    utcnow,
)
from src.domain.access.rules import (
    AccessRuleError,
    normalize_phone,
    require_future_expiry,
    require_note,
    require_open_request,
    require_user_kind,
    require_verified_phone,
)


async def create_resident_request(
    session: AsyncSession,
    actor: User,
    *,
    house_id: UUID,
    full_name: str,
    entrance_number: int,
    apartment_number: int,
) -> ResidentRequest:
    require_verified_phone(actor)
    require_user_kind(actor, {"unassigned", "resident"})
    if entrance_number <= 0 or apartment_number <= 0:
        raise AccessRuleError(
            400,
            "invalid_apartment",
            "Номера подъезда и квартиры должны быть положительными",
        )
    house = await require_row(session, House, house_id)
    if house.archived_at is not None:
        raise AccessRuleError(409, "house_unavailable", "Дом не подключён")
    if house.entrance_count is not None and entrance_number > house.entrance_count:
        raise AccessRuleError(400, "invalid_entrance", "Такого подъезда нет в доме")
    name = full_name.strip()
    if not name:
        raise AccessRuleError(400, "name_required", "Укажите ФИО")
    existing = await session.scalar(
        select(ResidentRequest)
        .where(
            ResidentRequest.applicant_user_id == actor.id,
            ResidentRequest.house_id == house_id,
            ResidentRequest.submitted_entrance_number == entrance_number,
            ResidentRequest.submitted_apartment_number == apartment_number,
            ResidentRequest.status.in_(["open", "reviewing", "needs_info"]),
        )
        .with_for_update()
    )
    if existing is not None:
        return existing
    now = utcnow()
    request = ResidentRequest(
        id=uuid4(),
        applicant_user_id=actor.id,
        house_id=house_id,
        submitted_full_name=name,
        submitted_entrance_number=entrance_number,
        submitted_apartment_number=apartment_number,
        status="open",
        discussion=[],
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(request)
    audit(
        session,
        entity_kind="resident_request",
        entity_id=request.id,
        action="created",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return request


async def update_resident_request(
    session: AsyncSession,
    actor: User,
    *,
    request_id: UUID,
    full_name: str,
    entrance_number: int,
    apartment_number: int,
) -> ResidentRequest:
    request = await require_row(session, ResidentRequest, request_id, for_update=True)
    if request.applicant_user_id != actor.id:
        raise AccessRuleError(403, "forbidden", "Можно исправить только свою заявку")
    require_open_request(request.status)
    if entrance_number <= 0 or apartment_number <= 0:
        raise AccessRuleError(
            400,
            "invalid_apartment",
            "Номера подъезда и квартиры должны быть положительными",
        )
    house = await require_row(session, House, request.house_id)
    if house.entrance_count is not None and entrance_number > house.entrance_count:
        raise AccessRuleError(400, "invalid_entrance", "Такого подъезда нет в доме")
    name = full_name.strip()
    if not name:
        raise AccessRuleError(400, "name_required", "Укажите ФИО")
    before = {
        "full_name": request.submitted_full_name,
        "entrance_number": request.submitted_entrance_number,
        "apartment_number": request.submitted_apartment_number,
    }
    request.submitted_full_name = name
    request.submitted_entrance_number = entrance_number
    request.submitted_apartment_number = apartment_number
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind="resident_request",
        entity_id=request.id,
        action="edited_by_applicant",
        actor_id=actor.id,
        before=before,
        after={
            "full_name": name,
            "entrance_number": entrance_number,
            "apartment_number": apartment_number,
        },
    )
    await commit_or_conflict(session)
    return request


async def _require_no_active_grant(
    session: AsyncSession, user_id: UUID, apartment_id: UUID
) -> None:
    now = utcnow()
    grant = await session.scalar(
        select(ResidentGrant)
        .where(
            ResidentGrant.user_id == user_id,
            ResidentGrant.apartment_id == apartment_id,
            ResidentGrant.revoked_at.is_(None),
            ResidentGrant.valid_from <= now,
            or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
        )
        .with_for_update()
    )
    if grant is not None:
        raise AccessRuleError(
            409, "already_has_access", "Доступ к квартире уже действует"
        )


async def decide_resident_request(
    session: AsyncSession,
    actor: User,
    *,
    request_id: UUID,
    outcome: str,
    decision_note: str,
    valid_to: datetime | None,
) -> tuple[ResidentRequest, ResidentGrant | None]:
    if outcome not in {"granted", "denied"}:
        raise AccessRuleError(400, "invalid_outcome", "Неизвестный результат решения")
    request = await require_row(session, ResidentRequest, request_id, for_update=True)
    require_open_request(request.status)
    house = await require_row(session, House, request.house_id)
    await require_staff(session, actor, house.company_id, "can_manage_residents")
    note = require_note(decision_note)
    grant = None
    if outcome == "granted":
        require_future_expiry(valid_to)
        applicant = await require_row(
            session, User, request.applicant_user_id, for_update=True
        )
        require_verified_phone(applicant)
        require_user_kind(applicant, {"unassigned", "resident"})
        apartment = await session.scalar(
            select(Apartment).where(
                Apartment.house_id == house.id,
                Apartment.entrance_number == request.submitted_entrance_number,
                Apartment.apartment_number == request.submitted_apartment_number,
            )
        )
        if apartment is None:
            apartment = Apartment(
                id=uuid4(),
                house_id=house.id,
                entrance_number=request.submitted_entrance_number,
                apartment_number=request.submitted_apartment_number,
            )
            session.add(apartment)
            await session.flush()
        await _require_no_active_grant(session, applicant.id, apartment.id)
        grant = ResidentGrant(
            id=uuid4(),
            user_id=applicant.id,
            apartment_id=apartment.id,
            valid_from=utcnow(),
            valid_to=valid_to,
            source_request_id=request.id,
            granted_by=actor.id,
            created_at=utcnow(),
        )
        session.add(grant)
        applicant.kind = "resident"
        request.resolved_apartment_id = apartment.id
    request.status = "closed"
    request.outcome = outcome
    request.decision_note = note
    request.decided_by = actor.id
    request.decided_at = utcnow()
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind="resident_request",
        entity_id=request.id,
        action="closed",
        actor_id=actor.id,
        after={"outcome": outcome, "grant_id": str(grant.id) if grant else None},
    )
    await commit_or_conflict(session)
    return request, grant


async def create_resident_offer(
    session: AsyncSession,
    actor: User,
    *,
    house_id: UUID,
    entrance_number: int,
    apartment_number: int,
    phone_number: str,
    valid_to: datetime | None,
) -> ResidentOffer:
    require_future_expiry(valid_to)
    house = await require_row(session, House, house_id)
    await require_staff(session, actor, house.company_id, "can_manage_residents")
    if entrance_number <= 0 or apartment_number <= 0:
        raise AccessRuleError(
            400,
            "invalid_apartment",
            "Номера подъезда и квартиры должны быть положительными",
        )
    if house.entrance_count is not None and entrance_number > house.entrance_count:
        raise AccessRuleError(400, "invalid_entrance", "Такого подъезда нет в доме")
    apartment = await session.scalar(
        select(Apartment).where(
            Apartment.house_id == house.id,
            Apartment.entrance_number == entrance_number,
            Apartment.apartment_number == apartment_number,
        )
    )
    if apartment is None:
        apartment = Apartment(
            id=uuid4(),
            house_id=house.id,
            entrance_number=entrance_number,
            apartment_number=apartment_number,
        )
        session.add(apartment)
        await session.flush()
    phone = normalize_phone(phone_number)
    target = await session.scalar(select(User).where(User.phone_number == phone))
    if target is not None and target.kind not in {"unassigned", "resident"}:
        raise AccessRuleError(
            409, "role_conflict", "Указанный номер принадлежит сотруднику или поддержке"
        )
    existing = await session.scalar(
        select(ResidentOffer).where(
            ResidentOffer.apartment_id == apartment.id,
            ResidentOffer.phone_number == phone,
            ResidentOffer.status == "pending",
        )
    )
    if existing is not None:
        raise AccessRuleError(
            409, "offer_exists", "Такое предложение уже ожидает ответа"
        )
    offer = ResidentOffer(
        id=uuid4(),
        house_id=house.id,
        company_id_at_offer=house.company_id,
        apartment_id=apartment.id,
        phone_number=phone,
        offered_by=actor.id,
        status="pending",
        proposed_access_until=valid_to,
        created_at=utcnow(),
    )
    session.add(offer)
    audit(
        session,
        entity_kind="resident_offer",
        entity_id=offer.id,
        action="created",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return offer


async def respond_resident_offer(
    session: AsyncSession,
    actor: User,
    *,
    offer_id: UUID,
    accept: bool,
) -> tuple[ResidentOffer, ResidentGrant | None]:
    locked_actor = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_actor is None:
        raise AccessRuleError(404, "user_not_found", "Аккаунт не найден")
    phone = require_verified_phone(locked_actor)
    offer = await require_row(session, ResidentOffer, offer_id, for_update=True)
    if offer.phone_number != phone:
        raise AccessRuleError(403, "forbidden", "Предложение адресовано другому номеру")
    if offer.status != "pending":
        raise AccessRuleError(409, "offer_finished", "Предложение уже обработано")
    house = await require_row(session, House, offer.house_id)
    if house.company_id != offer.company_id_at_offer:
        raise AccessRuleError(
            409, "company_changed", "Предложение прежней УК недействительно"
        )
    grant = None
    if accept:
        require_user_kind(locked_actor, {"unassigned", "resident"})
        require_future_expiry(offer.proposed_access_until)
        await _require_no_active_grant(session, actor.id, offer.apartment_id)
        grant = ResidentGrant(
            id=uuid4(),
            user_id=actor.id,
            apartment_id=offer.apartment_id,
            valid_from=utcnow(),
            valid_to=offer.proposed_access_until,
            source_offer_id=offer.id,
            granted_by=offer.offered_by,
            created_at=utcnow(),
        )
        session.add(grant)
        locked_actor.kind = "resident"
        offer.status = "accepted"
        offer.accepted_by = actor.id
        offer.accepted_at = utcnow()
    else:
        offer.status = "declined"
    audit(
        session,
        entity_kind="resident_offer",
        entity_id=offer.id,
        action="accepted" if accept else "declined",
        actor_id=actor.id,
        after={"grant_id": str(grant.id) if grant else None},
    )
    await commit_or_conflict(session)
    return offer, grant


async def change_resident_grant(
    session: AsyncSession,
    actor: User,
    *,
    grant_id: UUID,
    action: str,
    valid_to: datetime | None,
    reason: str | None,
) -> ResidentGrant:
    grant = await require_row(session, ResidentGrant, grant_id, for_update=True)
    apartment = await require_row(session, Apartment, grant.apartment_id)
    house = await require_row(session, House, apartment.house_id)
    await require_staff(session, actor, house.company_id, "can_manage_residents")
    if grant.revoked_at is not None:
        raise AccessRuleError(409, "grant_revoked", "Отозванный доступ нельзя продлить")
    before = {"valid_to": grant.valid_to.isoformat() if grant.valid_to else None}
    if action == "extend":
        require_future_expiry(valid_to)
        if valid_to is None:
            raise AccessRuleError(
                400, "expiry_required", "Укажите новую дату окончания"
            )
        if grant.valid_to is None:
            raise AccessRuleError(
                409, "grant_permanent", "Бессрочный доступ не требует продления"
            )
        if valid_to <= grant.valid_to:
            raise AccessRuleError(
                400, "not_an_extension", "Новый срок должен быть позднее текущего"
            )
        grant.valid_to = valid_to
    elif action == "revoke":
        grant.revoked_at = utcnow()
        grant.revoked_by = actor.id
        grant.revoke_reason = reason.strip() if reason else None
        if grant.valid_to is None or grant.valid_to > grant.revoked_at:
            grant.valid_to = grant.revoked_at
    else:
        raise AccessRuleError(400, "invalid_action", "Неизвестное действие с доступом")
    audit(
        session,
        entity_kind="resident_grant",
        entity_id=grant.id,
        action=action,
        actor_id=actor.id,
        before=before,
        after={"valid_to": grant.valid_to.isoformat() if grant.valid_to else None},
    )
    await commit_or_conflict(session)
    return grant
