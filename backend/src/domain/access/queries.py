"""Чтение домов, заявок и назначений с проверкой текущих прав."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Apartment,
    CompanyRegistrationRequest,
    House,
    HouseAdditionRequest,
    ResidentGrant,
    ResidentOffer,
    ResidentRequest,
    StaffAssignment,
    User,
)
from src.domain.access.common import require_staff
from src.domain.access.requests import (
    load_request,
    model_for_kind,
    require_request_party,
)
from src.domain.access.rules import require_verified_phone


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def request_data(kind: str, row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(row.id),
        "kind": kind,
        "status": row.status,
        "applicant_user_id": str(row.applicant_user_id),
        "outcome": row.outcome,
        "decision_note": row.decision_note,
        "decided_by": str(row.decided_by) if row.decided_by else None,
        "decided_at": _iso(row.decided_at),
        "cancel_requested_by": (
            str(row.cancel_requested_by) if row.cancel_requested_by else None
        ),
        "discussion": list(row.discussion or []),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if kind == "resident":
        data.update(
            house_id=str(row.house_id),
            submitted_full_name=row.submitted_full_name,
            submitted_entrance_number=row.submitted_entrance_number,
            submitted_apartment_number=row.submitted_apartment_number,
            resolved_apartment_id=(
                str(row.resolved_apartment_id) if row.resolved_apartment_id else None
            ),
        )
    elif kind == "company_registration":
        data.update(
            phone_number=row.phone_number,
            proposed_company_name=row.proposed_company_name,
            free_text=row.free_text,
        )
    else:
        data.update(
            registration_request_id=(
                str(row.registration_request_id)
                if row.registration_request_id
                else None
            ),
            company_id=str(row.company_id) if row.company_id else None,
            entered_address=row.entered_address,
            resolved_house_id=(
                str(row.resolved_house_id) if row.resolved_house_id else None
            ),
            free_text=row.free_text,
        )
    return data


async def get_request(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request_id: UUID,
) -> dict[str, Any]:
    row = await load_request(session, kind=kind, request_id=request_id)
    await require_request_party(session, actor, kind=kind, request=row, writing=False)
    data = request_data(kind, row)
    if kind == "resident":
        house = await session.get(House, row.house_id)
        if house is not None:
            data["address_display"] = house.address_display
    return data


async def list_requests(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    house_id: UUID | None = None,
) -> list[dict[str, Any]]:
    model = model_for_kind(kind)
    statement = select(model)
    if kind == "resident":
        company_ids = select(StaffAssignment.company_id).where(
            StaffAssignment.user_id == actor.id,
            StaffAssignment.revoked_at.is_(None),
        )
        statement = select(model, House.address_display).join(
            House, House.id == ResidentRequest.house_id
        ).where(
            or_(
                ResidentRequest.applicant_user_id == actor.id,
                House.company_id.in_(company_ids),
            )
        )
        if house_id is not None:
            statement = statement.where(ResidentRequest.house_id == house_id)
    elif kind == "company_registration":
        if actor.kind != "support":
            statement = statement.where(
                CompanyRegistrationRequest.applicant_user_id == actor.id
            )
    else:
        if actor.kind != "support":
            statement = statement.where(
                HouseAdditionRequest.applicant_user_id == actor.id
            )
    statement = statement.order_by(model.created_at.desc(), model.id.desc()).limit(100)
    if kind == "resident":
        rows = (await session.execute(statement)).all()
        return [
            request_data(kind, row) | {"address_display": address}
            for row, address in rows
        ]
    rows = list((await session.scalars(statement)).all())
    return [request_data(kind, row) for row in rows]


async def search_houses(session: AsyncSession, text: str) -> list[dict[str, Any]]:
    query = text.strip()
    if len(query) < 2:
        return []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = list(
        (
            await session.scalars(
                select(House)
                .where(
                    House.archived_at.is_(None),
                    or_(
                        House.address_display.ilike(f"%{escaped}%", escape="\\"),
                        House.address_key.ilike(f"%{escaped}%", escape="\\"),
                    ),
                )
                .order_by(House.address_display)
                .limit(20)
            )
        ).all()
    )
    return [
        {
            "id": house.id,
            "address_display": house.address_display,
            "entrance_count": house.entrance_count,
        }
        for house in rows
    ]


async def list_offers(session: AsyncSession, actor: User) -> list[dict[str, Any]]:
    phone = require_verified_phone(actor)
    rows = (
        await session.execute(
            select(ResidentOffer, Apartment, House)
            .join(Apartment, Apartment.id == ResidentOffer.apartment_id)
            .join(House, House.id == ResidentOffer.house_id)
            .where(ResidentOffer.phone_number == phone)
            .order_by(ResidentOffer.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": str(offer.id),
            "house_id": str(offer.house_id),
            "address_display": house.address_display,
            "apartment_id": str(offer.apartment_id),
            "entrance_number": apartment.entrance_number,
            "apartment_number": apartment.apartment_number,
            "status": offer.status,
            "proposed_access_until": _iso(offer.proposed_access_until),
            "created_at": _iso(offer.created_at),
        }
        for offer, apartment, house in rows
    ]


async def list_company_offers(
    session: AsyncSession,
    actor: User,
    *,
    company_id: UUID,
    house_id: UUID | None = None,
) -> list[dict[str, Any]]:
    await require_staff(session, actor, company_id)
    statement = (
        select(ResidentOffer, Apartment, House)
        .join(Apartment, Apartment.id == ResidentOffer.apartment_id)
        .join(House, House.id == ResidentOffer.house_id)
        .where(House.company_id == company_id, House.archived_at.is_(None))
        .order_by(ResidentOffer.created_at.desc())
        .limit(100)
    )
    if house_id is not None:
        statement = statement.where(House.id == house_id)
    rows = (await session.execute(statement)).all()
    return [
        {
            "id": offer.id,
            "house_id": house.id,
            "address_display": house.address_display,
            "entrance_number": apartment.entrance_number,
            "apartment_number": apartment.apartment_number,
            "phone_number": offer.phone_number,
            "status": offer.status,
            "proposed_access_until": offer.proposed_access_until,
            "created_at": offer.created_at,
        }
        for offer, apartment, house in rows
    ]


async def list_grants(session: AsyncSession, actor: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ResidentGrant, Apartment, House)
            .join(Apartment, Apartment.id == ResidentGrant.apartment_id)
            .join(House, House.id == Apartment.house_id)
            .where(ResidentGrant.user_id == actor.id)
            .order_by(ResidentGrant.created_at.desc())
            .limit(100)
        )
    ).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "id": str(grant.id),
            "apartment_id": str(grant.apartment_id),
            "house_id": str(house.id),
            "address_display": house.address_display,
            "entrance_number": apartment.entrance_number,
            "apartment_number": apartment.apartment_number,
            "valid_from": _iso(grant.valid_from),
            "valid_to": _iso(grant.valid_to),
            "revoked_at": _iso(grant.revoked_at),
            "status": (
                "revoked"
                if grant.revoked_at is not None
                else (
                    "expired"
                    if grant.valid_to is not None and grant.valid_to <= now
                    else "active"
                )
            ),
        }
        for grant, apartment, house in rows
    ]


async def list_company_houses(
    session: AsyncSession,
    actor: User,
    *,
    company_id: UUID,
) -> list[dict[str, Any]]:
    await require_staff(session, actor, company_id)
    houses = list(
        (
            await session.scalars(
                select(House)
                .where(House.company_id == company_id, House.archived_at.is_(None))
                .order_by(House.address_display)
                .limit(100)
            )
        ).all()
    )
    return [
        {
            "id": house.id,
            "address_display": house.address_display,
            "entrance_count": house.entrance_count,
        }
        for house in houses
    ]


async def list_staff(
    session: AsyncSession,
    actor: User,
    *,
    company_id: UUID,
) -> list[dict[str, Any]]:
    await require_staff(session, actor, company_id)
    rows = list(
        (
            await session.scalars(
                select(StaffAssignment)
                .where(StaffAssignment.company_id == company_id)
                .order_by(StaffAssignment.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    return [
        {
            "id": str(assignment.id),
            "phone_number": assignment.phone_number,
            "user_id": str(assignment.user_id) if assignment.user_id else None,
            "can_manage_staff": assignment.can_manage_staff,
            "can_manage_residents": assignment.can_manage_residents,
            "can_manage_issues": assignment.can_manage_issues,
            "created_at": _iso(assignment.created_at),
            "revoked_at": _iso(assignment.revoked_at),
        }
        for assignment in rows
    ]


async def list_residents(
    session: AsyncSession,
    actor: User,
    *,
    company_id: UUID,
    house_id: UUID | None = None,
) -> list[dict[str, Any]]:
    await require_staff(session, actor, company_id)
    statement = (
        select(ResidentGrant, Apartment, House, User, ResidentRequest, ResidentOffer)
        .join(Apartment, Apartment.id == ResidentGrant.apartment_id)
        .join(House, House.id == Apartment.house_id)
        .join(User, User.id == ResidentGrant.user_id)
        .outerjoin(
            ResidentRequest, ResidentRequest.id == ResidentGrant.source_request_id
        )
        .outerjoin(ResidentOffer, ResidentOffer.id == ResidentGrant.source_offer_id)
        .where(House.company_id == company_id)
        .order_by(ResidentGrant.created_at.desc())
        .limit(100)
    )
    if house_id is not None:
        statement = statement.where(House.id == house_id)
    rows = (await session.execute(statement)).all()
    now = datetime.now(timezone.utc)
    items = []
    for grant, apartment, house, user, request, offer in rows:
        if grant.revoked_at is not None:
            status = "revoked"
        elif grant.valid_to is not None and grant.valid_to <= now:
            status = "expired"
        else:
            status = "active"
        items.append(
            {
                "grant_id": str(grant.id),
                "user_id": str(user.id),
                "full_name": request.submitted_full_name if request else user.full_name,
                "phone_number": user.phone_number,
                "house_id": str(house.id),
                "address_display": house.address_display,
                "entrance_number": apartment.entrance_number,
                "apartment_number": apartment.apartment_number,
                "status": status,
                "valid_from": _iso(grant.valid_from),
                "valid_to": _iso(grant.valid_to),
                "revoked_at": _iso(grant.revoked_at),
                "source": "resident_request" if request else "resident_offer",
                "decided_by": str(request.decided_by if request else offer.offered_by),
                "decided_at": _iso(
                    request.decided_at if request else offer.accepted_at
                ),
                "decision_note": request.decision_note if request else None,
            }
        )
    return items
