"""Owner-only, revision-checked PostgreSQL drafts.

These functions deliberately do not commit. HTTP handlers commit once, and the
bot can combine a draft transition with a subject operation in one transaction.
"""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Draft, User


FLOW_FIELDS = {
    "issue_card": frozenset({
        "house_id", "category_id", "title", "description", "summary_description", "scope",
        "apartment_id",
    }),
    "resident_request": frozenset({
        "house_id", "full_name", "name_from_profile", "entrance_number", "apartment_number",
    }),
}
MAX_PAYLOAD_BYTES = 32 * 1024


class DraftError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _validate_payload(flow_kind: str, payload: dict[str, object]) -> None:
    fields = FLOW_FIELDS.get(flow_kind)
    if fields is None:
        raise DraftError(400, "invalid_flow_kind", "Неизвестный тип черновика")
    if not isinstance(payload, dict):
        raise DraftError(400, "invalid_payload", "Данные черновика должны быть объектом")
    if unknown := set(payload) - fields:
        raise DraftError(400, "invalid_payload", f"Недопустимые поля черновика: {', '.join(sorted(unknown))}")
    if (
        flow_kind == "resident_request"
        and "name_from_profile" in payload
        and not isinstance(payload["name_from_profile"], bool)
    ):
        raise DraftError(400, "invalid_payload", "Источник ФИО должен быть логическим значением")
    try:
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise DraftError(400, "invalid_payload", "Данные черновика должны быть JSON") from error
    if size > MAX_PAYLOAD_BYTES:
        raise DraftError(413, "payload_too_large", "Черновик слишком большой")


async def list_drafts(
    session: AsyncSession, actor: User, *, flow_kind: str | None = None
) -> list[Draft]:
    if flow_kind is not None and flow_kind not in FLOW_FIELDS:
        raise DraftError(400, "invalid_flow_kind", "Неизвестный тип черновика")
    query = select(Draft).where(Draft.owner_user_id == actor.id)
    if flow_kind is not None:
        query = query.where(Draft.flow_kind == flow_kind)
    return list((await session.scalars(query.order_by(Draft.updated_at.desc(), Draft.id).limit(50))).all())


async def get_draft(session: AsyncSession, actor: User, draft_id: UUID) -> Draft:
    draft = await session.scalar(
        select(Draft).where(Draft.id == draft_id, Draft.owner_user_id == actor.id)
    )
    if draft is None:
        raise DraftError(404, "draft_not_found", "Черновик не найден")
    return draft


async def save_draft(
    session: AsyncSession,
    actor: User,
    *,
    flow_kind: str,
    payload: dict[str, object],
    draft_id: UUID | None = None,
    revision: int | None = None,
) -> Draft:
    _validate_payload(flow_kind, payload)
    now = datetime.now(UTC)
    if draft_id is None:
        if revision is not None:
            raise DraftError(400, "invalid_revision", "При создании revision не передаётся")
        draft = Draft(
            id=uuid4(), owner_user_id=actor.id, flow_kind=flow_kind,
            payload=payload, revision=1, created_at=now, updated_at=now,
        )
        session.add(draft)
        await session.flush()
        return draft
    if revision is None or revision < 1:
        raise DraftError(400, "invalid_revision", "Для обновления укажите revision")
    draft = await session.scalar(
        update(Draft)
        .where(
            Draft.id == draft_id,
            Draft.owner_user_id == actor.id,
            Draft.flow_kind == flow_kind,
            Draft.revision == revision,
            Draft.submitted_at.is_(None),
        )
        .values(payload=payload, revision=Draft.revision + 1, updated_at=now)
        .returning(Draft)
        .execution_options(populate_existing=True)
    )
    if draft is not None:
        return draft
    existing = await get_draft(session, actor, draft_id)
    if existing.flow_kind != flow_kind:
        raise DraftError(409, "flow_kind_mismatch", "Тип черновика нельзя изменить")
    if existing.submitted_at is not None:
        raise DraftError(409, "draft_submitted", "Черновик уже отправлен")
    raise DraftError(409, "stale_revision", "Черновик изменён в другом окне; обновите его")


async def mark_submitted(
    session: AsyncSession, actor: User, *, draft_id: UUID, revision: int
) -> Draft:
    if revision < 1:
        raise DraftError(400, "invalid_revision", "Укажите revision черновика")
    now = datetime.now(UTC)
    draft = await session.scalar(
        update(Draft)
        .where(
            Draft.id == draft_id,
            Draft.owner_user_id == actor.id,
            Draft.revision == revision,
            Draft.submitted_at.is_(None),
        )
        .values(submitted_at=now, updated_at=now, revision=Draft.revision + 1)
        .returning(Draft)
        .execution_options(populate_existing=True)
    )
    if draft is not None:
        return draft
    existing = await get_draft(session, actor, draft_id)
    if existing.submitted_at is not None:
        raise DraftError(409, "draft_submitted", "Черновик уже отправлен")
    raise DraftError(409, "stale_revision", "Черновик изменён в другом окне; обновите его")
