"""Text access to the same PostgreSQL drafts used by the mini-app."""

import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.issues_text import _category, _scope
from src.db.models import File, IssueReport, User
from src.domain.access.resident import create_resident_request
from src.domain.access.rules import AccessRuleError
from src.domain.drafts.service import (
    DraftError,
    get_draft,
    list_drafts,
    mark_submitted,
    save_draft,
)
from src.domain.files.service import attach
from src.domain.files.storage import FileError
from src.domain.issues.service import IssueError, create_card
from src.gen.access.api.create_resident_request import Request as ResidentRequestBody
from src.gen.issues.api.create_card import Request as IssueCardBody

DRAFT_HELP = (
    "Черновики доступны и в мини-приложении. Формат команд:\n"
    "/draft list [issue_card|resident_request] — список; /draft show UUID — открыть.\n"
    "/draft save issue_card | UUID_дома | код_категории | all/e:1,2/a:12 | название | описание\n"
    "/draft save resident_request | UUID_дома | квартира | ФИО\n"
    "/filehelp — добавить фото, PDF или видео в черновик проблемы.\n"
    "Для обновления замените тип на «UUID_черновика revision» и повторите все поля.\n"
    "/draft send UUID revision — подать обращение и отметить черновик отправленным.\n"
    "/draft submit UUID revision — только отметить после отдельной подачи обращения."
)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Нужен корректный UUID черновика или дома") from error


def _positive(value: str, label: str = "Номер квартиры") -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise ValueError(f"{label} должен быть целым числом") from error
    if result < 1:
        raise ValueError(f"{label} должен быть положительным")
    return result


def _revision(value: str) -> int:
    try:
        revision = int(value)
    except ValueError as error:
        raise ValueError("Укажите целую версию черновика из /draft show") from error
    if revision < 1:
        raise ValueError("Версия черновика должна быть положительной")
    return revision


async def _save_text(session: AsyncSession, actor: User, raw: str) -> str:
    parts = [part.strip() for part in raw.split("|")]
    head = parts[0].split()
    draft_id = None
    revision = None
    if len(head) == 1 and head[0] in {"issue_card", "resident_request"}:
        flow_kind = head[0]
    elif len(head) == 2:
        draft_id, revision = _uuid(head[0]), _revision(head[1])
        flow_kind = (await get_draft(session, actor, draft_id)).flow_kind
    else:
        raise ValueError(DRAFT_HELP)

    fields = parts[1:]
    if flow_kind == "resident_request":
        if len(fields) not in {3, 4} or not all(fields):
            raise ValueError(
                "Формат: /draft save resident_request | UUID_дома | квартира | ФИО"
            )
        legacy = len(fields) == 4
        payload = {
            "house_id": str(_uuid(fields[0])),
            "apartment_number": _positive(fields[2] if legacy else fields[1]),
            "full_name": fields[3] if legacy else fields[2],
        }
        if legacy:
            payload["entrance_number"] = _positive(fields[1], "Номер подъезда")
    else:
        if len(fields) != 5 or not all(fields):
            raise ValueError(
                "Формат: /draft save issue_card | UUID_дома | код_категории | all/e:1,2/a:12 | название | описание"
            )
        category = await _category(session, fields[1])
        scope_all, entrances, apartments = _scope(fields[2])
        payload = {
            "house_id": str(_uuid(fields[0])),
            "category_id": str(category.id),
            "scope_all_house": scope_all,
            "target_entrances": entrances,
            "target_apartments": [
                {"apartment_number": apartment}
                | ({"entrance_number": entrance} if entrance is not None else {})
                for entrance, apartment in apartments
            ],
            "title": fields[3],
            "description": fields[4],
            "summary_description": fields[4],
        }
    draft = await save_draft(
        session,
        actor,
        flow_kind=flow_kind,
        payload=payload,
        draft_id=draft_id,
        revision=revision,
    )
    return f"Черновик сохранён: {draft.id}; версия {draft.revision}. Отправить: /draft send {draft.id} {draft.revision}"


async def _send_text(
    session: AsyncSession, actor: User, draft_id: UUID, revision: int
) -> str:
    draft = await get_draft(session, actor, draft_id)
    if draft.revision != revision:
        raise DraftError(
            409, "stale_revision", "Черновик изменён в другом окне; обновите его"
        )
    try:
        if draft.flow_kind == "issue_card":
            body = IssueCardBody.model_validate(draft.payload)
        elif draft.flow_kind == "resident_request":
            body = ResidentRequestBody.model_validate(draft.payload)
        else:
            raise DraftError(400, "invalid_flow_kind", "Неизвестный тип черновика")
    except ValidationError as error:
        raise ValueError(
            "Черновик заполнен не полностью; откройте /draft show и исправьте поля"
        ) from error

    try:
        await mark_submitted(session, actor, draft_id=draft_id, revision=revision)
        if draft.flow_kind == "issue_card":
            result = await create_card(
                session,
                actor,
                house_id=body.house_id,
                category_id=body.category_id,
                title=body.title,
                description=body.description,
                summary_description=draft.payload.get("summary_description") or body.description,
                scope_all_house=body.scope_all_house,
                target_entrances=body.target_entrances or [],
                target_apartments=[
                    (item.entrance_number, item.apartment_number)
                    for item in body.target_apartments or []
                ],
            )
            report_id = await session.scalar(
                select(IssueReport.id).where(
                    IssueReport.card_id == result.id,
                    IssueReport.author_user_id == actor.id,
                )
            )
            if report_id is None:
                raise RuntimeError("Создана карточка без исходного описания")
            staged_ids = (
                await session.scalars(
                    select(File.id).where(
                        File.draft_id == draft_id, File.state == "staged"
                    )
                )
            ).all()
            for file_id in staged_ids:
                await attach(
                    session,
                    actor,
                    file_id=file_id,
                    parent_kind="issue_report",
                    parent_id=report_id,
                )
        else:
            result = await create_resident_request(
                session,
                actor,
                house_id=body.house_id,
                full_name=body.full_name,
                apartment_number=body.apartment_number,
                **(
                    {"entrance_number": body.entrance_number}
                    if body.entrance_number is not None
                    else {}
                ),
            )
    except (IssueError, AccessRuleError, FileError, ValueError):
        await session.rollback()
        raise
    return (
        f"Обращение отправлено: {result.id}. Черновик {draft_id} помечен отправленным."
    )


async def handle_draft_text(
    session: AsyncSession, actor: User, text: str
) -> str | None:
    """Return a reply for /draft, or None for other command families."""
    command, _, argument = text.strip().partition(" ")
    if command.lower() not in {"/draft", "/drafthelp"}:
        return None
    if command.lower() == "/drafthelp" or not argument.strip():
        return DRAFT_HELP
    action, _, rest = argument.strip().partition(" ")
    action = action.lower()
    try:
        if action == "list":
            kind = rest.strip() or None
            drafts = await list_drafts(session, actor, flow_kind=kind)
            if not drafts:
                return "Черновиков пока нет."
            return "Черновики:\n" + "\n".join(
                f"{item.id} · {item.flow_kind} · версия {item.revision} · "
                f"{'отправлен' if item.submitted_at else 'не отправлен'}"
                for item in drafts[:20]
            )
        if action == "show":
            draft = await get_draft(session, actor, _uuid(rest.strip()))
            payload = json.dumps(draft.payload, ensure_ascii=False, indent=2)
            return (
                f"Черновик {draft.id} ({draft.flow_kind}); версия {draft.revision}; "
                f"{'отправлен' if draft.submitted_at else 'не отправлен'}\n{payload}"
            )[:3500]
        if action == "save":
            return await _save_text(session, actor, rest.strip())
        if action in {"send", "submit"}:
            fields = rest.split()
            if len(fields) != 2:
                raise ValueError(
                    "Формат: /draft send UUID revision или /draft submit UUID revision"
                )
            draft_id, revision = _uuid(fields[0]), _revision(fields[1])
            if action == "send":
                return await _send_text(session, actor, draft_id, revision)
            draft = await mark_submitted(
                session, actor, draft_id=draft_id, revision=revision
            )
            return f"Черновик {draft.id} помечен отправленным. Само обращение эта команда не создала."
        return DRAFT_HELP
    except (DraftError, IssueError, AccessRuleError, FileError, ValueError) as error:
        return f"Не получилось выполнить команду черновика: {error}"
