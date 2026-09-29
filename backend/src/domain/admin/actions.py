"""Allowlisted administrator actions, shared by the bot and mini-app."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Apartment, AuditEvent, Company, House, IssueTarget, User
from src.domain.access.common import commit_or_conflict, require_row, utcnow
from src.domain.access.company import (
    address_key,
    decide_company_registration,
    decide_house_request,
)
from src.domain.access.requests import add_discussion_message, change_request_status
from src.domain.access.resident import (
    change_resident_grant,
    create_resident_offer,
    decide_resident_request,
)
from src.domain.access.rules import AccessRuleError
from src.domain.access.staff import assign_staff, revoke_staff
from src.domain.admin.service import (
    invite_support,
    require_admin,
    revoke_support,
    revoke_support_invitation,
)
from src.domain.issues.service import (
    IssueError,
    edit_card,
    require_unmerged_card,
    set_status,
)


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InviteSupport(ActionPayload):
    phone_number: str = Field(min_length=1, max_length=30)


class RevokeSupport(ActionPayload):
    target_user_id: UUID


class RevokeSupportInvite(ActionPayload):
    invitation_id: UUID


class AssignStaff(ActionPayload):
    company_id: UUID
    phone_number: str = Field(min_length=1, max_length=30)
    can_manage_staff: bool
    can_manage_residents: bool
    can_manage_issues: bool


class RevokeStaff(ActionPayload):
    assignment_id: UUID


class OfferResident(ActionPayload):
    house_id: UUID
    entrance_number: int = Field(gt=0)
    apartment_number: int = Field(gt=0)
    phone_number: str = Field(min_length=1, max_length=30)
    valid_to: datetime | None = None

    @field_validator("valid_to")
    @classmethod
    def aware_valid_to(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Срок должен содержать часовой пояс")
        return value


class RevokeGrant(ActionPayload):
    grant_id: UUID
    reason: str | None = Field(default=None, max_length=2000)


class EditCompany(ActionPayload):
    company_id: UUID
    display_name: str = Field(min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=500)
    inn: str | None = Field(default=None, max_length=12)
    ogrn: str | None = Field(default=None, max_length=15)

    @field_validator("inn")
    @classmethod
    def valid_inn(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        if cleaned and (
            not cleaned.isascii()
            or not cleaned.isdigit()
            or len(cleaned) not in {10, 12}
        ):
            raise ValueError("ИНН должен содержать 10 или 12 цифр")
        return cleaned or None

    @field_validator("ogrn")
    @classmethod
    def valid_ogrn(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        if cleaned and (
            not cleaned.isascii()
            or not cleaned.isdigit()
            or len(cleaned) not in {13, 15}
        ):
            raise ValueError("ОГРН должен содержать 13 или 15 цифр")
        return cleaned or None


class EditHouse(ActionPayload):
    house_id: UUID
    address_display: str = Field(min_length=1, max_length=500)
    entrance_count: int | None = Field(default=None, gt=0)


class EditUserName(ActionPayload):
    user_id: UUID
    full_name: str | None = Field(default=None, max_length=255)


class TargetApartment(ActionPayload):
    entrance_number: int = Field(gt=0)
    apartment_number: int = Field(gt=0)


class EditIssue(ActionPayload):
    card_id: UUID
    expected_version: int = Field(gt=0)
    category_id: UUID
    title: str = Field(min_length=1, max_length=500)
    scope_all_house: bool
    target_entrances: list[int] = Field(default_factory=list, max_length=100)
    target_apartments: list[TargetApartment] = Field(
        default_factory=list, max_length=100
    )


class SetIssueStatus(ActionPayload):
    card_id: UUID
    status: str
    note: str | None = Field(default=None, max_length=4000)
    close_result: str | None = None


class RequestStatus(ActionPayload):
    kind: str
    request_id: UUID
    status: str


class AddAccessMessage(ActionPayload):
    kind: str
    request_id: UUID
    text: str = Field(min_length=1, max_length=4000)


class RequestDecision(ActionPayload):
    kind: str
    request_id: UUID
    outcome: str
    decision_note: str = Field(min_length=1, max_length=4000)
    display_name: str | None = Field(default=None, max_length=255)
    proposed_address_key: str | None = Field(default=None, max_length=500)
    entrance_count: int | None = Field(default=None, gt=0)
    valid_to: datetime | None = None

    @field_validator("valid_to")
    @classmethod
    def aware_valid_to(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Срок должен содержать часовой пояс")
        return value


ACTION_MODELS: dict[str, type[ActionPayload]] = {
    "invite_support": InviteSupport,
    "revoke_support": RevokeSupport,
    "revoke_support_invite": RevokeSupportInvite,
    "assign_staff": AssignStaff,
    "revoke_staff": RevokeStaff,
    "offer_resident": OfferResident,
    "revoke_grant": RevokeGrant,
    "edit_company": EditCompany,
    "edit_house": EditHouse,
    "edit_user_name": EditUserName,
    "edit_issue": EditIssue,
    "set_issue_status": SetIssueStatus,
    "request_status": RequestStatus,
    "add_access_message": AddAccessMessage,
    "request_decision": RequestDecision,
}


def _result(entity: str, row_id: UUID, action: str) -> dict[str, str]:
    return {"id": str(row_id), "entity": entity, "action": action}


def _audit_edit(
    session: AsyncSession,
    actor: User,
    *,
    entity_kind: str,
    entity_id: UUID,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind=entity_kind,
            entity_id=entity_id,
            action="admin_edited",
            actor_user_id=actor.id,
            before_data=before,
            after_data=after,
        )
    )


async def _edit_company(
    session: AsyncSession, actor: User, body: EditCompany
) -> Company:
    row = await require_row(session, Company, body.company_id, for_update=True)
    name = body.display_name.strip()
    if not name:
        raise AccessRuleError(400, "name_required", "Укажите название УК")
    before = {
        "display_name": row.display_name,
        "legal_name": row.legal_name,
        "inn": row.inn,
        "ogrn": row.ogrn,
    }
    row.display_name = name
    for field in ("legal_name", "inn", "ogrn"):
        if field in body.model_fields_set:
            value = getattr(body, field)
            setattr(row, field, value.strip() or None if value is not None else None)
    _audit_edit(
        session,
        actor,
        entity_kind="company",
        entity_id=row.id,
        before=before,
        after={
            "display_name": row.display_name,
            "legal_name": row.legal_name,
            "inn": row.inn,
            "ogrn": row.ogrn,
        },
    )
    await commit_or_conflict(session)
    return row


async def _edit_house(session: AsyncSession, actor: User, body: EditHouse) -> House:
    row = await require_row(session, House, body.house_id, for_update=True)
    address = body.address_display.strip()
    key = address_key(address)
    if not key:
        raise AccessRuleError(400, "address_required", "Укажите адрес дома")
    existing = await session.scalar(
        select(House.id).where(House.address_key == key, House.id != row.id)
    )
    if existing is not None:
        raise AccessRuleError(409, "house_exists", "Дом с таким адресом уже есть")
    if body.entrance_count is not None and "entrance_count" in body.model_fields_set:
        apartment_max = await session.scalar(
            select(func.max(Apartment.entrance_number)).where(
                Apartment.house_id == row.id
            )
        )
        target_max = await session.scalar(
            select(func.max(IssueTarget.entrance_number)).where(
                IssueTarget.house_id == row.id
            )
        )
        if body.entrance_count < max(apartment_max or 0, target_max or 0):
            raise AccessRuleError(
                409,
                "entrance_count_conflict",
                "Количество подъездов меньше уже зарегистрированного номера",
            )
    before = {
        "address_display": row.address_display,
        "address_key": row.address_key,
        "entrance_count": row.entrance_count,
    }
    row.address_display = address
    row.address_key = key
    if "entrance_count" in body.model_fields_set:
        row.entrance_count = body.entrance_count
    _audit_edit(
        session,
        actor,
        entity_kind="house",
        entity_id=row.id,
        before=before,
        after={
            "address_display": row.address_display,
            "address_key": row.address_key,
            "entrance_count": row.entrance_count,
        },
    )
    await commit_or_conflict(session)
    return row


async def _edit_user_name(
    session: AsyncSession, actor: User, body: EditUserName
) -> User:
    row = await require_row(session, User, body.user_id, for_update=True)
    before = {"full_name": row.full_name}
    row.full_name = body.full_name.strip() or None if body.full_name else None
    row.version += 1
    row.updated_at = utcnow()
    _audit_edit(
        session,
        actor,
        entity_kind="user",
        entity_id=row.id,
        before=before,
        after={"full_name": row.full_name},
    )
    await commit_or_conflict(session)
    return row


async def admin_action(
    session: AsyncSession,
    actor: User,
    action: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    """Run one named domain operation; never accept table names or arbitrary columns."""
    current = await require_admin(session, actor)
    model = ACTION_MODELS.get(action)
    if model is None:
        raise AccessRuleError(
            400, "invalid_action", "Неизвестное действие администратора"
        )
    try:
        body = model.model_validate(payload)
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(item) for item in first["loc"]) or "payload"
        raise AccessRuleError(
            400, "invalid_payload", f"Некорректное поле: {field}"
        ) from error

    if isinstance(body, InviteSupport):
        row = await invite_support(session, current, phone_number=body.phone_number)
        return _result("support_invites", row.id, action)
    if isinstance(body, RevokeSupport):
        row = await revoke_support(session, current, target_user_id=body.target_user_id)
        return _result("users", row.id, action)
    if isinstance(body, RevokeSupportInvite):
        row = await revoke_support_invitation(
            session, current, invitation_id=body.invitation_id
        )
        return _result("support_invites", row.id, action)
    if isinstance(body, AssignStaff):
        row = await assign_staff(
            session,
            current,
            company_id=body.company_id,
            phone_number=body.phone_number,
            can_manage_staff=body.can_manage_staff,
            can_manage_residents=body.can_manage_residents,
            can_manage_issues=body.can_manage_issues,
        )
        return _result("staff", row.id, action)
    if isinstance(body, RevokeStaff):
        row = await revoke_staff(session, current, assignment_id=body.assignment_id)
        return _result("staff", row.id, action)
    if isinstance(body, OfferResident):
        row = await create_resident_offer(
            session,
            current,
            house_id=body.house_id,
            entrance_number=body.entrance_number,
            apartment_number=body.apartment_number,
            phone_number=body.phone_number,
            valid_to=body.valid_to,
        )
        return _result("offers", row.id, action)
    if isinstance(body, RevokeGrant):
        row = await change_resident_grant(
            session,
            current,
            grant_id=body.grant_id,
            action="revoke",
            valid_to=None,
            reason=body.reason,
        )
        return _result("resident_grants", row.id, action)
    if isinstance(body, EditCompany):
        row = await _edit_company(session, current, body)
        return _result("companies", row.id, action)
    if isinstance(body, EditHouse):
        row = await _edit_house(session, current, body)
        return _result("houses", row.id, action)
    if isinstance(body, EditUserName):
        row = await _edit_user_name(session, current, body)
        return _result("users", row.id, action)
    if isinstance(body, RequestStatus):
        row = await change_request_status(
            session,
            current,
            kind=body.kind,
            request_id=body.request_id,
            status=body.status,
        )
        return _result("access_requests", row.id, action)
    if isinstance(body, AddAccessMessage):
        row, _ = await add_discussion_message(
            session, current, kind=body.kind, request_id=body.request_id, text=body.text
        )
        return _result("access_requests", row.id, action)
    if isinstance(body, RequestDecision):
        if body.kind == "company_registration":
            row, _ = await decide_company_registration(
                session,
                current,
                request_id=body.request_id,
                outcome=body.outcome,
                decision_note=body.decision_note,
                display_name=body.display_name,
            )
        elif body.kind == "house_addition":
            row, _ = await decide_house_request(
                session,
                current,
                request_id=body.request_id,
                outcome=body.outcome,
                decision_note=body.decision_note,
                proposed_address_key=body.proposed_address_key,
                entrance_count=body.entrance_count,
            )
        elif body.kind == "resident":
            row, _ = await decide_resident_request(
                session,
                current,
                request_id=body.request_id,
                outcome=body.outcome,
                decision_note=body.decision_note,
                valid_to=body.valid_to,
            )
        else:
            raise AccessRuleError(400, "invalid_request_kind", "Неизвестный вид заявки")
        return _result("access_requests", row.id, action)
    try:
        if isinstance(body, EditIssue):
            await require_unmerged_card(session, body.card_id)
            row = await edit_card(
                session,
                current,
                body.card_id,
                expected_version=body.expected_version,
                category_id=body.category_id,
                title=body.title,
                scope_all_house=body.scope_all_house,
                target_entrances=body.target_entrances,
                target_apartments=[
                    (item.entrance_number, item.apartment_number)
                    for item in body.target_apartments
                ],
            )
        elif isinstance(body, SetIssueStatus):
            await require_unmerged_card(session, body.card_id)
            row = await set_status(
                session,
                current,
                body.card_id,
                status=body.status,
                note=body.note,
                close_result=body.close_result,
            )
        else:
            raise AccessRuleError(
                400, "invalid_action", "Неизвестное действие администратора"
            )
    except IssueError as error:
        raise AccessRuleError(error.status_code, error.code, error.message) from error
    await commit_or_conflict(session)
    return _result("issues", row.id, action)
