"""Administrator-only, paginated views over application records."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, Text, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import (
    Apartment,
    AuditEvent,
    Company,
    CompanyRegistrationRequest,
    File,
    House,
    HouseAdditionRequest,
    IssueCard,
    IssueMessage,
    IssueReport,
    IssueTarget,
    ResidentGrant,
    ResidentOffer,
    ResidentRequest,
    StaffAssignment,
    SupportInvitation,
    User,
)
from src.domain.access.rules import AccessRuleError
from src.domain.issues.service import merged_card_ids

ENTITY_MODELS: dict[str, type[Any]] = {
    "users": User,
    "companies": Company,
    "houses": House,
    "apartments": Apartment,
    "issues": IssueCard,
    "resident_requests": ResidentRequest,
    "company_requests": CompanyRegistrationRequest,
    "house_requests": HouseAdditionRequest,
    "staff": StaffAssignment,
    "resident_grants": ResidentGrant,
    "offers": ResidentOffer,
    "support_invites": SupportInvitation,
    "files": File,
    "audit": AuditEvent,
}
REQUEST_MODELS: dict[str, type[Any]] = {
    "resident": ResidentRequest,
    "company_registration": CompanyRegistrationRequest,
    "house_addition": HouseAdditionRequest,
}
REQUEST_ENTITY_KIND = {
    "resident_requests": "resident",
    "company_requests": "company_registration",
    "house_requests": "house_addition",
}


async def require_admin(session: AsyncSession, actor: User) -> User:
    """Re-read the role so a revoked administrator cannot use a stale bot object."""
    current = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .execution_options(populate_existing=True)
    )
    if current is None or current.kind != "admin":
        raise AccessRuleError(403, "admin_required", "Требуется доступ администратора")
    return current


def _primitive(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def _row_data(row: Any) -> dict[str, Any]:
    return {
        column.key: _primitive(getattr(row, column.key))
        for column in row.__table__.columns
        if column.key != "storage_key"
    }


def _request_title(kind: str, row: Any) -> str:
    if kind == "resident":
        return row.submitted_full_name
    if kind == "company_registration":
        return row.proposed_company_name or row.phone_number
    return row.entered_address


def _invite_status(row: SupportInvitation) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if row.accepted_at is not None:
        return "accepted"
    return "pending"


def _summary(
    entity: str, row: Any, *, request_kind: str | None = None
) -> dict[str, Any]:
    kind = request_kind or REQUEST_ENTITY_KIND.get(entity)
    data = _row_data(row)
    if kind is not None:
        title = _request_title(kind, row)
        subtitle = kind
        status = row.status
        data["kind"] = kind
    elif entity == "users":
        title = row.full_name or row.phone_number or row.max_user_id or str(row.id)
        subtitle = row.phone_number
        status = row.kind
    elif entity == "companies":
        title, subtitle = row.display_name, row.inn
        status = "archived" if row.archived_at else "active"
    elif entity == "houses":
        title, subtitle = row.address_display, str(row.company_id)
        status = "archived" if row.archived_at else "active"
    elif entity == "issues":
        title, subtitle = row.title, str(row.house_id)
        status = "merged" if row.merged_into_id is not None else row.status
    elif entity == "apartments":
        title = f"Подъезд {row.entrance_number}, квартира {row.apartment_number}"
        subtitle, status = str(row.house_id), None
    elif entity == "staff":
        title, subtitle = row.phone_number, str(row.company_id)
        status = "revoked" if row.revoked_at else "active"
    elif entity == "resident_grants":
        title, subtitle = str(row.user_id), str(row.apartment_id)
        status = "revoked" if row.revoked_at else "active"
    elif entity == "offers":
        title, subtitle, status = row.phone_number, str(row.house_id), row.status
    elif entity == "support_invites":
        title, subtitle, status = (
            row.phone_number,
            str(row.invited_by),
            _invite_status(row),
        )
    elif entity == "files":
        title, subtitle, status = row.original_name, row.mime_type, row.state
    else:
        title, subtitle, status = row.action, row.entity_kind, None
    result = {
        "id": str(row.id),
        "title": title,
        "subtitle": subtitle,
        "status": status,
        "created_at": _primitive(getattr(row, "created_at", None)),
        "updated_at": _primitive(getattr(row, "updated_at", None)),
        "data": data,
    }
    if kind is not None:
        result["kind"] = kind
        result["request_id"] = str(row.id)
    return result


def _search_clause(entity: str, model: type[Any], text: str) -> Any:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    term = f"%{escaped}%"
    fields: dict[str, tuple[str, ...]] = {
        "users": ("full_name", "phone_number", "max_user_id"),
        "companies": ("display_name", "legal_name", "inn", "ogrn"),
        "houses": ("address_display", "address_key"),
        "issues": ("title", "current_note"),
        "resident_requests": ("submitted_full_name",),
        "company_requests": ("proposed_company_name", "phone_number", "free_text"),
        "house_requests": ("entered_address", "free_text"),
        "staff": ("phone_number",),
        "offers": ("phone_number",),
        "support_invites": ("phone_number",),
        "files": ("original_name", "mime_type"),
        "audit": ("entity_kind", "action"),
    }
    clauses = [
        getattr(model, field).ilike(term, escape="\\")
        for field in fields.get(entity, ())
    ]
    clauses.append(cast(model.id, Text).ilike(term, escape="\\"))
    return or_(*clauses)


def _order_column(model: type[Any]) -> Any:
    created_at = getattr(model, "created_at", None)
    return model.id if created_at is None else created_at


async def _list_one(
    session: AsyncSession,
    entity: str,
    *,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AccessRuleError(400, "invalid_entity", "Неизвестный раздел админки")
    filters = []
    if q:
        filters.append(_search_clause(entity, model, q))
    count_query = select(func.count()).select_from(model).where(*filters)
    total = int(await session.scalar(count_query) or 0)
    order = _order_column(model)
    rows = (
        await session.scalars(
            select(model)
            .where(*filters)
            .order_by(order.desc(), model.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_summary(entity, row) for row in rows], total


async def _list_all_requests(
    session: AsyncSession,
    *,
    q: str | None,
    limit: int,
    offset: int,
    kind: str | None,
) -> tuple[list[dict[str, Any]], int]:
    if kind is not None and kind not in REQUEST_MODELS:
        raise AccessRuleError(400, "invalid_request_kind", "Неизвестный вид заявки")
    names = [kind] if kind else list(REQUEST_MODELS)
    arms = []
    for name in names:
        model = REQUEST_MODELS[name]
        entity = next(
            key for key, value in REQUEST_ENTITY_KIND.items() if value == name
        )
        statement: Select[Any] = select(
            model.id.label("id"),
            model.created_at.label("created_at"),
            literal(name).label("kind"),
        )
        if q:
            statement = statement.where(_search_clause(entity, model, q))
        arms.append(statement)
    combined = union_all(*arms).subquery() if len(arms) > 1 else arms[0].subquery()
    total = int(await session.scalar(select(func.count()).select_from(combined)) or 0)
    keys = (
        await session.execute(
            select(combined.c.id, combined.c.kind)
            .order_by(combined.c.created_at.desc(), combined.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items: list[dict[str, Any]] = []
    for row_id, row_kind in keys:
        row = await session.get(REQUEST_MODELS[row_kind], row_id)
        if row is not None:
            items.append(_summary("access_requests", row, request_kind=row_kind))
    return items, total


async def admin_list(
    session: AsyncSession,
    actor: User,
    entity: str,
    *,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    kind: str | None = None,
) -> dict[str, Any]:
    await require_admin(session, actor)
    if limit < 1 or limit > 100 or offset < 0 or offset > 10_000:
        raise AccessRuleError(400, "invalid_page", "Недопустимые параметры страницы")
    query = q.strip() if q else None
    if query and len(query) > 100:
        raise AccessRuleError(400, "query_too_long", "Поисковый запрос слишком длинный")
    if entity == "access_requests":
        items, total = await _list_all_requests(
            session, q=query, limit=limit, offset=offset, kind=kind
        )
    else:
        if kind is not None:
            raise AccessRuleError(
                400, "invalid_kind_filter", "Фильтр вида здесь не поддерживается"
            )
        items, total = await _list_one(
            session, entity, q=query, limit=limit, offset=offset
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def admin_get(
    session: AsyncSession, actor: User, entity: str, row_id: UUID
) -> dict[str, Any]:
    await require_admin(session, actor)
    if entity == "access_requests":
        row = None
        request_kind = None
        for name, model in REQUEST_MODELS.items():
            row = await session.get(model, row_id)
            if row is not None:
                request_kind = name
                break
        if row is None:
            raise AccessRuleError(404, "not_found", "Заявка не найдена")
        result = _row_data(row) | {"kind": request_kind, "request_id": str(row_id)}
    else:
        model = ENTITY_MODELS.get(entity)
        if model is None:
            raise AccessRuleError(400, "invalid_entity", "Неизвестный раздел админки")
        row = await session.get(model, row_id)
        if row is None:
            raise AccessRuleError(404, "not_found", "Запись не найдена")
        result = _row_data(row)
        if entity in REQUEST_ENTITY_KIND:
            result["kind"] = REQUEST_ENTITY_KIND[entity]
            result["request_id"] = str(row_id)
    if entity == "issues":
        history_ids = await merged_card_ids(session, row_id)
        targets = (
            await session.execute(
                select(IssueTarget, Apartment)
                .outerjoin(Apartment, Apartment.id == IssueTarget.apartment_id)
                .where(IssueTarget.card_id == row_id)
            )
        ).all()
        result["targets"] = [
            _row_data(target)
            | {
                "apartment_entrance_number": (
                    apartment.entrance_number if apartment else None
                ),
                "apartment_number": apartment.apartment_number if apartment else None,
            }
            for target, apartment in targets
        ]
        result["reports"] = [
            _row_data(item)
            for item in (
                await session.scalars(
                    select(IssueReport)
                    .where(IssueReport.card_id == row_id)
                    .order_by(IssueReport.created_at)
                )
            ).all()
        ]
        result["messages"] = [
            _row_data(item)
            for item in (
                await session.scalars(
                    select(IssueMessage)
                    .where(IssueMessage.card_id == row_id)
                    .order_by(IssueMessage.created_at)
                )
            ).all()
        ]
        result["history"] = [
            _row_data(item)
            for item in (
                await session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.entity_kind == "issue_card",
                        AuditEvent.entity_id.in_(history_ids),
                    )
                    .order_by(AuditEvent.created_at)
                )
            ).all()
        ]
    return result


async def admin_overview(session: AsyncSession, actor: User) -> dict[str, Any]:
    await require_admin(session, actor)
    counts = {
        name: int(await session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in ENTITY_MODELS.items()
        if name != "audit"
    }
    counts["access_requests"] = (
        counts["resident_requests"]
        + counts["company_requests"]
        + counts["house_requests"]
    )
    counts["audit"] = int(
        await session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    )
    return {"counts": counts}
