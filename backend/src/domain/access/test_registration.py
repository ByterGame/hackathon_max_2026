"""Explicitly enabled test-only self-approval of an applicant's company and houses."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import test_mode_enabled
from src.db.models import (
    Company,
    CompanyRegistrationRequest,
    House,
    HouseAdditionRequest,
    ResidentOffer,
    StaffAssignment,
    SupportInvitation,
    User,
)
from src.domain.access.common import (
    audit,
    commit_or_conflict,
    lock_phone_role,
    require_row,
    utcnow,
)
from src.domain.access.company import address_key
from src.domain.access.rules import (
    AccessRuleError,
    normalize_phone,
    require_open_request,
    require_verified_phone,
)


async def activate_test_registration(
    session: AsyncSession,
    actor: User,
    *,
    request_id: UUID,
) -> tuple[Company, int]:
    """Create a test УК and all requested houses in one transaction.

    The flag is checked in the domain as well as the HTTP route so a caller
    cannot accidentally bypass support by invoking this function directly.
    """
    if not test_mode_enabled():
        raise AccessRuleError(404, "not_found", "Действие недоступно")
    request = await require_row(
        session, CompanyRegistrationRequest, request_id, for_update=True
    )
    if request.applicant_user_id != actor.id:
        raise AccessRuleError(403, "forbidden", "Можно открыть только свою УК")
    require_open_request(request.status)
    if actor.kind != "unassigned":
        raise AccessRuleError(
            403, "role_conflict", "Тестовое создание УК доступно только новому аккаунту"
        )
    phone = require_verified_phone(actor)
    if normalize_phone(request.phone_number) != phone:
        raise AccessRuleError(
            400,
            "own_phone_required",
            "Для тестового создания УК укажите свой подтверждённый номер первым сотрудником",
        )
    name = (request.proposed_company_name or "").strip()
    if not name:
        raise AccessRuleError(400, "company_name_required", "Укажите название УК")

    # Match the normal approval flow's phone-role exclusion under the same lock.
    await lock_phone_role(session, phone)
    locked_actor = await require_row(session, User, actor.id, for_update=True)
    if locked_actor.kind != "unassigned" or require_verified_phone(locked_actor) != phone:
        raise AccessRuleError(409, "role_conflict", "Роль или номер аккаунта изменились")
    competing_support = await session.scalar(
        select(SupportInvitation.id).where(
            SupportInvitation.phone_number == phone,
            SupportInvitation.accepted_at.is_(None),
            SupportInvitation.revoked_at.is_(None),
        )
    )
    competing_resident = await session.scalar(
        select(ResidentOffer.id).where(
            ResidentOffer.phone_number == phone,
            ResidentOffer.status == "pending",
        )
    )
    if competing_support is not None or competing_resident is not None:
        raise AccessRuleError(
            409, "role_conflict", "На этот номер уже выдано приглашение другой роли"
        )

    house_requests = list(
        (
            await session.scalars(
                select(HouseAdditionRequest)
                .where(
                    HouseAdditionRequest.registration_request_id == request.id,
                    HouseAdditionRequest.status.in_(("open", "reviewing", "needs_info")),
                )
                .order_by(HouseAdditionRequest.created_at, HouseAdditionRequest.id)
                .with_for_update()
            )
        ).all()
    )
    keys: set[str] = set()
    for house_request in house_requests:
        key = address_key(house_request.entered_address)
        if not key or key in keys:
            raise AccessRuleError(409, "duplicate_house", "Проверьте адреса домов в заявке")
        keys.add(key)
        if house_request.entrance_count is None or house_request.entrance_count <= 0:
            raise AccessRuleError(400, "invalid_entrances", "Укажите количество подъездов")
        if house_request.apartment_count is None or house_request.apartment_count <= 0:
            raise AccessRuleError(400, "invalid_apartments", "Укажите количество квартир")
        if await session.scalar(select(House.id).where(House.address_key == key)):
            raise AccessRuleError(409, "house_exists", "Дом уже подключён")

    now = utcnow()
    company = Company(
        id=uuid4(),
        registration_request_id=request.id,
        display_name=name,
        created_at=now,
    )
    session.add(company)
    await session.flush([company])
    session.add(
        StaffAssignment(
            id=uuid4(),
            company_id=company.id,
            phone_number=phone,
            user_id=locked_actor.id,
            can_manage_staff=True,
            can_manage_residents=True,
            can_manage_issues=True,
            granted_by=actor.id,
            created_at=now,
            bound_at=now,
            version=1,
        )
    )
    locked_actor.kind = "employee"
    locked_actor.updated_at = now
    locked_actor.version += 1

    for house_request in house_requests:
        house = House(
            id=uuid4(),
            company_id=company.id,
            address_display=house_request.entered_address,
            address_key=address_key(house_request.entered_address),
            entrance_count=house_request.entrance_count,
            apartment_count=house_request.apartment_count,
            created_at=now,
        )
        session.add(house)
        await session.flush([house])
        house_request.company_id = company.id
        house_request.resolved_house_id = house.id
        house_request.status = "closed"
        house_request.outcome = "approved"
        house_request.decision_note = "Автоматически одобрено в тестовом режиме"
        house_request.decided_by = actor.id
        house_request.decided_at = now
        house_request.updated_at = now
        house_request.version += 1
        audit(
            session,
            entity_kind="house_addition_request",
            entity_id=house_request.id,
            action="closed",
            actor_id=actor.id,
            after={"outcome": "approved", "house_id": str(house.id), "test_mode": True},
        )

    request.status = "closed"
    request.outcome = "approved"
    request.decision_note = "Автоматически одобрено в тестовом режиме"
    request.decided_by = actor.id
    request.decided_at = now
    request.updated_at = now
    request.version += 1
    audit(
        session,
        entity_kind="company_registration_request",
        entity_id=request.id,
        action="closed",
        actor_id=actor.id,
        after={"outcome": "approved", "company_id": str(company.id), "test_mode": True},
    )
    await commit_or_conflict(session)
    return company, len(house_requests)
