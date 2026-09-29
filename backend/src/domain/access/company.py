"""Обращения о регистрации УК и подключении домов."""

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    require_staff,
    require_support,
    utcnow,
)
from src.domain.access.rules import (
    AccessRuleError,
    normalize_phone,
    require_note,
    require_open_request,
    require_verified_phone,
)


async def create_company_registration(
    session: AsyncSession,
    actor: User,
    *,
    phone_number: str,
    free_text: str,
    proposed_company_name: str | None,
) -> CompanyRegistrationRequest:
    if actor.kind in {"support", "admin"}:
        raise AccessRuleError(
            403, "role_conflict", "Оператор или администратор не подаёт обращение от имени УК"
        )
    require_verified_phone(actor)
    now = utcnow()
    request = CompanyRegistrationRequest(
        id=uuid4(),
        applicant_user_id=actor.id,
        phone_number=normalize_phone(phone_number),
        proposed_company_name=(
            proposed_company_name.strip() or None if proposed_company_name else None
        ),
        free_text=free_text.strip(),
        status="open",
        discussion=[],
        created_at=now,
        updated_at=now,
        version=1,
    )
    if not request.free_text:
        raise AccessRuleError(400, "text_required", "Опишите, какая УК обращается")
    session.add(request)
    audit(
        session,
        entity_kind="company_registration_request",
        entity_id=request.id,
        action="created",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return request


async def decide_company_registration(
    session: AsyncSession,
    actor: User,
    *,
    request_id: UUID,
    outcome: str,
    decision_note: str,
    display_name: str | None,
) -> tuple[CompanyRegistrationRequest, Company | None]:
    require_support(actor)
    if outcome not in {"approved", "rejected"}:
        raise AccessRuleError(400, "invalid_outcome", "Неизвестный результат решения")
    request = await require_row(
        session, CompanyRegistrationRequest, request_id, for_update=True
    )
    require_open_request(request.status)
    note = require_note(decision_note)
    company = None
    if outcome == "approved":
        name = (display_name or request.proposed_company_name or "").strip()
        if not name:
            raise AccessRuleError(
                400, "company_name_required", "Укажите название одобренной УК"
            )
        phone = normalize_phone(request.phone_number)
        await lock_phone_role(session, phone)
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
                409,
                "role_conflict",
                "На этот номер уже выдано приглашение другой роли",
            )
        matching_user = await session.scalar(
            select(User)
            .where(
                User.phone_number == phone,
                User.phone_verified_at.is_not(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if matching_user is not None and matching_user.kind not in {
            "unassigned",
            "employee",
        }:
            raise AccessRuleError(
                409, "role_conflict", "Указанный номер занят аккаунтом другой роли"
            )
        company = Company(
            id=uuid4(),
            registration_request_id=request.id,
            display_name=name,
            created_at=utcnow(),
        )
        session.add(company)
        assignment = StaffAssignment(
            id=uuid4(),
            company_id=company.id,
            phone_number=phone,
            user_id=matching_user.id if matching_user is not None else None,
            can_manage_staff=True,
            can_manage_residents=True,
            can_manage_issues=True,
            granted_by=actor.id,
            created_at=utcnow(),
            bound_at=utcnow() if matching_user is not None else None,
            version=1,
        )
        session.add(assignment)
        if matching_user is not None:
            matching_user.kind = "employee"
    else:
        pending_houses = list(
            (
                await session.scalars(
                    select(HouseAdditionRequest)
                    .where(
                        HouseAdditionRequest.registration_request_id == request.id,
                        HouseAdditionRequest.status.not_in(("closed", "cancelled")),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for pending in pending_houses:
            pending.status = "cancelled"
            pending.updated_at = utcnow()
            pending.version += 1
            audit(
                session,
                entity_kind="house_addition_request",
                entity_id=pending.id,
                action="cancelled_with_registration",
                actor_id=actor.id,
            )
    request.status = "closed"
    request.outcome = outcome
    request.decision_note = note
    request.decided_by = actor.id
    request.decided_at = utcnow()
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind="company_registration_request",
        entity_id=request.id,
        action="closed",
        actor_id=actor.id,
        after={"outcome": outcome, "company_id": str(company.id) if company else None},
    )
    await commit_or_conflict(session)
    return request, company


async def create_house_request(
    session: AsyncSession,
    actor: User,
    *,
    registration_request_id: UUID | None,
    company_id: UUID | None,
    entered_address: str,
    free_text: str | None,
) -> HouseAdditionRequest:
    if (registration_request_id is None) == (company_id is None):
        raise AccessRuleError(
            400, "company_reference_required", "Укажите регистрацию либо действующую УК"
        )
    if registration_request_id is not None:
        registration = await require_row(
            session, CompanyRegistrationRequest, registration_request_id, for_update=True
        )
        if registration.applicant_user_id != actor.id:
            raise AccessRuleError(
                403, "forbidden", "Можно дополнить только своё обращение"
            )
        require_open_request(registration.status)
    else:
        await require_row(session, Company, company_id)
        # До согласования отдельного права на запрос домов используем самое
        # узкое административное право УК — управление сотрудниками.
        await require_staff(session, actor, company_id, "can_manage_staff")
    address = entered_address.strip()
    if not address:
        raise AccessRuleError(400, "address_required", "Укажите адрес дома")
    now = utcnow()
    request = HouseAdditionRequest(
        id=uuid4(),
        applicant_user_id=actor.id,
        registration_request_id=registration_request_id,
        company_id=company_id,
        entered_address=address,
        free_text=free_text.strip() if free_text else None,
        status="open",
        discussion=[],
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(request)
    audit(
        session,
        entity_kind="house_addition_request",
        entity_id=request.id,
        action="created",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return request


def address_key(value: str) -> str:
    """Временная нормализация ввода; адресный реестр пока не подключён."""
    return re.sub(r"\s+", " ", value.strip()).casefold()


async def decide_house_request(
    session: AsyncSession,
    actor: User,
    *,
    request_id: UUID,
    outcome: str,
    decision_note: str,
    proposed_address_key: str | None,
    entrance_count: int | None,
) -> tuple[HouseAdditionRequest, House | None]:
    require_support(actor)
    if outcome not in {"approved", "rejected"}:
        raise AccessRuleError(400, "invalid_outcome", "Неизвестный результат решения")
    request = await require_row(
        session, HouseAdditionRequest, request_id, for_update=True
    )
    require_open_request(request.status)
    note = require_note(decision_note)
    house = None
    if outcome == "approved":
        company_id = request.company_id
        if company_id is None and request.registration_request_id is not None:
            company = await session.scalar(
                select(Company).where(
                    Company.registration_request_id == request.registration_request_id
                )
            )
            company_id = company.id if company is not None else None
        if company_id is None:
            raise AccessRuleError(
                409, "registration_pending", "Сначала одобрите регистрацию УК"
            )
        key = address_key(proposed_address_key or request.entered_address)
        if not key:
            raise AccessRuleError(
                400, "address_required", "Укажите нормализованный адрес"
            )
        existing = await session.scalar(select(House).where(House.address_key == key))
        if existing is not None:
            raise AccessRuleError(
                409,
                "house_exists",
                "Дом уже подключён; передача УК здесь не выполняется",
            )
        if entrance_count is not None and entrance_count <= 0:
            raise AccessRuleError(
                400,
                "invalid_entrances",
                "Количество подъездов должно быть положительным",
            )
        house = House(
            id=uuid4(),
            company_id=company_id,
            address_display=request.entered_address,
            address_key=key,
            entrance_count=entrance_count,
            created_at=utcnow(),
        )
        session.add(house)
        # The request already exists, so its UPDATE can otherwise be flushed
        # before the new house INSERT despite the foreign key between them.
        await session.flush([house])
        request.company_id = company_id
        request.resolved_house_id = house.id
    request.status = "closed"
    request.outcome = outcome
    request.decision_note = note
    request.decided_by = actor.id
    request.decided_at = utcnow()
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind="house_addition_request",
        entity_id=request.id,
        action="closed",
        actor_id=actor.id,
        after={"outcome": outcome, "house_id": str(house.id) if house else None},
    )
    await commit_or_conflict(session)
    return request, house
