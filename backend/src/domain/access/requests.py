"""Общее управление обсуждением, рабочим статусом и отменой заявок."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    CompanyRegistrationRequest,
    House,
    HouseAdditionRequest,
    ResidentRequest,
    User,
)
from src.domain.access.common import (
    audit,
    commit_or_conflict,
    require_row,
    require_staff,
    require_support,
    utcnow,
)
from src.domain.access.rules import (
    AccessRuleError,
    ensure_other_party,
    require_open_request,
)

REQUEST_MODELS: dict[str, type[Any]] = {
    "company_registration": CompanyRegistrationRequest,
    "house_addition": HouseAdditionRequest,
    "resident": ResidentRequest,
}


def model_for_kind(kind: str) -> type[Any]:
    model = REQUEST_MODELS.get(kind)
    if model is None:
        raise AccessRuleError(400, "invalid_request_kind", "Неизвестный вид заявки")
    return model


async def load_request(
    session: AsyncSession,
    *,
    kind: str,
    request_id: UUID,
    for_update: bool = False,
) -> Any:
    return await require_row(
        session,
        model_for_kind(kind),
        request_id,
        for_update=for_update,
    )


async def require_request_party(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request: Any,
    writing: bool,
) -> None:
    if actor.kind == "admin":
        return
    if kind in {"company_registration", "house_addition"}:
        if request.applicant_user_id == actor.id or actor.kind == "support":
            return
        raise AccessRuleError(
            403, "forbidden", "Заявка доступна только заявителю и поддержке"
        )
    if request.applicant_user_id == actor.id:
        return
    house = await require_row(session, House, request.house_id)
    await require_staff(
        session,
        actor,
        house.company_id,
        "can_manage_residents" if writing else None,
    )


async def add_discussion_message(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request_id: UUID,
    text: str,
) -> tuple[Any, UUID]:
    request = await load_request(
        session, kind=kind, request_id=request_id, for_update=True
    )
    await require_request_party(
        session, actor, kind=kind, request=request, writing=True
    )
    if request.status == "cancelled":
        raise AccessRuleError(
            409, "request_cancelled", "Отменённую заявку нельзя обсуждать"
        )
    body = text.strip()
    if not body:
        raise AccessRuleError(400, "text_required", "Введите текст сообщения")
    message_id = uuid4()
    message = {
        "id": str(message_id),
        "author_user_id": str(actor.id),
        "author_kind": actor.kind,
        "text": body,
        "created_at": utcnow().isoformat(),
    }
    request.discussion = [*(request.discussion or []), message]
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind=f"{kind}_request",
        entity_id=request.id,
        action="message_added",
        actor_id=actor.id,
        after={"message_id": str(message_id)},
    )
    await commit_or_conflict(session)
    return request, message_id


async def change_request_status(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request_id: UUID,
    status: str,
) -> Any:
    if status not in {"open", "reviewing", "needs_info"}:
        raise AccessRuleError(400, "invalid_status", "Рабочий статус не поддерживается")
    request = await load_request(
        session, kind=kind, request_id=request_id, for_update=True
    )
    require_open_request(request.status)
    if kind == "resident":
        house = await require_row(session, House, request.house_id)
        await require_staff(session, actor, house.company_id, "can_manage_residents")
    else:
        require_support(actor)
    before = request.status
    request.status = status
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind=f"{kind}_request",
        entity_id=request.id,
        action="status_changed",
        actor_id=actor.id,
        before={"status": before},
        after={"status": status},
    )
    await commit_or_conflict(session)
    return request


async def request_cancellation(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request_id: UUID,
) -> Any:
    request = await load_request(
        session, kind=kind, request_id=request_id, for_update=True
    )
    require_open_request(request.status)
    await require_request_party(
        session, actor, kind=kind, request=request, writing=True
    )
    if request.cancel_requested_by is not None:
        raise AccessRuleError(
            409, "cancellation_pending", "Запрос на отмену уже ожидает ответа"
        )
    if (
        kind == "resident"
        and request.status == "open"
        and request.applicant_user_id == actor.id
    ):
        request.status = "cancelled"
        action = "cancelled_by_applicant"
    else:
        request.cancel_requested_by = actor.id
        request.cancel_requested_at = utcnow()
        action = "cancellation_requested"
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind=f"{kind}_request",
        entity_id=request.id,
        action=action,
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return request


async def resolve_cancellation(
    session: AsyncSession,
    actor: User,
    *,
    kind: str,
    request_id: UUID,
    accept: bool,
) -> Any:
    request = await load_request(
        session, kind=kind, request_id=request_id, for_update=True
    )
    require_open_request(request.status)
    await require_request_party(
        session, actor, kind=kind, request=request, writing=True
    )
    requester_id = request.cancel_requested_by
    if requester_id is None:
        raise AccessRuleError(409, "no_cancellation_request", "Отмена не запрошена")
    ensure_other_party(requester_id, actor.id)
    # Другая сторона — не просто другой сотрудник той же УК или оператор поддержки.
    requester_is_applicant = requester_id == request.applicant_user_id
    resolver_is_applicant = actor.id == request.applicant_user_id
    if requester_is_applicant == resolver_is_applicant:
        raise AccessRuleError(
            403, "other_party_required", "Отмену подтверждает другая сторона"
        )
    if accept:
        request.status = "cancelled"
        if kind == "company_registration":
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
    request.cancel_requested_by = None
    request.cancel_requested_at = None
    request.updated_at = utcnow()
    request.version += 1
    audit(
        session,
        entity_kind=f"{kind}_request",
        entity_id=request.id,
        action="cancellation_accepted" if accept else "cancellation_rejected",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return request
