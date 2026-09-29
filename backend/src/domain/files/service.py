"""Attachment ownership and current-parent visibility checks."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AuditEvent,
    Draft,
    File,
    IssueMessage,
    IssueReport,
    User,
)
from src.domain.access.requests import load_request, require_request_party
from src.domain.access.rules import AccessRuleError
from src.domain.issues.service import IssueError, get_visible_card

from .storage import FileError, path_for_key, remove_failed_upload, store_stream


PARENT_FIELDS = {
    "issue_report": "issue_report_id",
    "issue_message": "issue_message_id",
    "company_registration": "company_registration_request_id",
    "house_addition": "house_addition_request_id",
}


def _not_found() -> FileError:
    return FileError(404, "file_not_found", "Файл не найден")


def _draft_not_found() -> FileError:
    return FileError(404, "draft_not_found", "Черновик не найден")


def _now() -> datetime:
    return datetime.now(UTC)


async def _draft(
    session: AsyncSession, actor: User, draft_id: UUID, *, for_upload: bool = False
) -> Draft:
    # Upload and submit must serialize on the same draft row. Otherwise a
    # concurrent upload can publish another staged file after submission.
    if for_upload:
        draft = await session.scalar(
            select(Draft)
            .where(Draft.id == draft_id, Draft.owner_user_id == actor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        draft = await session.get(Draft, draft_id)
    if draft is None or (
        draft.owner_user_id != actor.id
        and (getattr(actor, "kind", None) != "admin" or for_upload)
    ):
        raise _draft_not_found()
    if draft.expires_at is not None and draft.expires_at <= _now():
        raise FileError(409, "draft_expired", "Срок черновика истёк")
    if for_upload and draft.submitted_at is not None:
        raise FileError(409, "draft_submitted", "Нельзя добавлять файлы в отправленный черновик")
    if draft.flow_kind != "issue_card":
        raise FileError(400, "unsupported_draft", "Для этого черновика вложения пока не поддерживаются")
    return draft


async def _parent(
    session: AsyncSession,
    actor: User,
    kind: str,
    parent_id: UUID,
    *,
    writing: bool,
) -> tuple[object, UUID | None]:
    if kind not in PARENT_FIELDS:
        raise FileError(400, "invalid_parent_kind", "Неизвестный тип заявки для файла")
    if kind in {"issue_report", "issue_message"}:
        model = IssueReport if kind == "issue_report" else IssueMessage
        parent = await session.get(model, parent_id)
        if parent is None:
            raise _not_found()
        try:
            card = await get_visible_card(session, actor, parent.card_id)
        except IssueError as error:
            raise _not_found() from error
        if writing:
            if parent.author_user_id != actor.id:
                raise FileError(403, "not_file_author", "Добавлять файл может только автор сообщения")
            if card.status == "closed":
                raise FileError(409, "issue_closed", "В закрытую заявку нельзя добавить файл")
        return parent, card.id
    request_kind = "company_registration" if kind == "company_registration" else "house_addition"
    try:
        parent = await load_request(session, kind=request_kind, request_id=parent_id)
        await require_request_party(
            session, actor, kind=request_kind, request=parent, writing=writing
        )
    except AccessRuleError as error:
        if writing and error.status_code != 404:
            raise FileError(error.status_code, error.code, error.message) from error
        raise _not_found() from error
    if writing and parent.status in {"closed", "cancelled"}:
        raise FileError(409, "request_finished", "В завершённую заявку нельзя добавить файл")
    return parent, None


def file_data(file: File) -> dict[str, object]:
    return {
        "id": str(file.id),
        "draft_id": str(file.draft_id) if file.draft_id is not None else None,
        "parent_kind": next(
            (kind for kind, field in PARENT_FIELDS.items() if getattr(file, field) is not None),
            None,
        ),
        "parent_id": next(
            (str(getattr(file, field)) for field in PARENT_FIELDS.values() if getattr(file, field) is not None),
            None,
        ),
        "original_name": file.original_name,
        "mime_type": file.mime_type,
        "size_bytes": file.size_bytes,
        "sha256": file.sha256,
        "state": file.state,
        "created_at": file.created_at.isoformat() if file.created_at else None,
        "ready_at": file.ready_at.isoformat() if file.ready_at else None,
    }


async def upload(
    session: AsyncSession,
    actor: User,
    *,
    root: Path,
    chunks: AsyncIterator[bytes],
    filename: str,
    content_type: str,
    draft_id: UUID | None,
    parent_kind: str | None,
    parent_id: UUID | None,
) -> File:
    if draft_id is not None and parent_kind is None and parent_id is None:
        await _draft(session, actor, draft_id, for_upload=True)
        parent_field = None
        parent_card_id = None
    elif draft_id is None and parent_kind is not None and parent_id is not None:
        _, parent_card_id = await _parent(
            session, actor, parent_kind, parent_id, writing=True
        )
        parent_field = PARENT_FIELDS[parent_kind]
    else:
        raise FileError(400, "invalid_destination", "Укажите черновик или одну готовую заявку")

    stored, name = await store_stream(
        root, chunks, filename=filename, content_type=content_type
    )
    file = File(
        id=uuid4(),
        storage_key=stored.key,
        uploader_user_id=actor.id,
        draft_id=draft_id,
        original_name=name,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        state="staged" if draft_id is not None else "ready",
        ready_at=None if draft_id is not None else _now(),
        **({parent_field: parent_id} if parent_field else {}),
    )
    try:
        session.add(file)
        session.add(
            AuditEvent(
                id=uuid4(),
                entity_kind="file",
                entity_id=file.id,
                action="uploaded",
                actor_user_id=actor.id,
                after_data={
                    "state": file.state,
                    "draft_id": str(draft_id) if draft_id else None,
                    "parent_kind": parent_kind,
                    "parent_id": str(parent_id) if parent_id else None,
                    "issue_card_id": str(parent_card_id) if parent_card_id else None,
                    "sha256": stored.sha256,
                },
            )
        )
        await session.flush()
    except Exception:
        remove_failed_upload(root, stored.key)
        raise
    return file


async def attach(
    session: AsyncSession,
    actor: User,
    *,
    file_id: UUID,
    parent_kind: str,
    parent_id: UUID,
) -> File:
    file = await session.scalar(
        select(File).where(File.id == file_id).with_for_update().execution_options(populate_existing=True)
    )
    if file is None or file.uploader_user_id != actor.id:
        raise _not_found()
    if file.state != "staged" or file.draft_id is None:
        raise FileError(409, "file_already_attached", "Файл уже привязан к заявке")
    draft = await _draft(session, actor, file.draft_id)
    if parent_kind != "issue_report":
        raise FileError(400, "invalid_destination", "Файл этого черновика можно привязать к исходному описанию")
    parent, card_id = await _parent(session, actor, parent_kind, parent_id, writing=True)
    card = await get_visible_card(session, actor, parent.card_id)
    if str(draft.payload.get("house_id")) != str(card.house_id):
        raise FileError(409, "draft_house_mismatch", "Черновик относится к другому дому")
    file.draft_id = None
    file.issue_report_id = parent_id
    file.state = "ready"
    file.ready_at = _now()
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind="file",
            entity_id=file.id,
            action="attached",
            actor_user_id=actor.id,
            after_data={"parent_kind": parent_kind, "parent_id": str(parent_id), "issue_card_id": str(card_id)},
        )
    )
    await session.flush()
    return file


async def list_files(
    session: AsyncSession,
    actor: User,
    *,
    parent_kind: str,
    parent_id: UUID,
) -> list[File]:
    if parent_kind == "draft":
        await _draft(session, actor, parent_id)
        predicate = File.draft_id == parent_id
        state = "staged"
    else:
        await _parent(session, actor, parent_kind, parent_id, writing=False)
        predicate = getattr(File, PARENT_FIELDS[parent_kind]) == parent_id
        state = "ready"
    return list(
        (
            await session.scalars(
                select(File)
                .where(predicate, File.state == state)
                .order_by(File.created_at, File.id)
            )
        ).all()
    )


async def get_file(
    session: AsyncSession,
    actor: User,
    *,
    file_id: UUID,
    root: Path,
) -> tuple[File, Path]:
    file = await session.get(File, file_id)
    if file is None:
        raise _not_found()
    if file.state == "staged" and file.draft_id is not None:
        if file.uploader_user_id != actor.id and getattr(actor, "kind", None) != "admin":
            raise _not_found()
        await _draft(session, actor, file.draft_id)
    elif file.state == "ready":
        parent = next(
            ((kind, getattr(file, field)) for kind, field in PARENT_FIELDS.items() if getattr(file, field) is not None),
            None,
        )
        if parent is None:
            raise _not_found()
        await _parent(session, actor, parent[0], parent[1], writing=False)
    else:
        raise _not_found()
    path = path_for_key(root, file.storage_key)
    if not path.is_file() or path.is_symlink():
        raise _not_found()
    return file, path
