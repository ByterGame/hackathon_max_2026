"""Separate administrator interface for the MAX bot.

The bot never edits ORM rows directly: every mutation goes through the shared
allowlisted administrator actions used by the mini-app.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from maxapi.enums import UploadType
from maxapi.types.input_media import InputMediaBuffer
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers import admin_system
from src.bot.handlers.media_text import _read_private_file
from src.bot.ui import (
    Button,
    UiReply,
    clear_dialog,
    get_dialog,
    set_dialog,
    update_dialog,
)
from src.db.models import BotDialog, User
from src.domain.access.rules import AccessRuleError
from src.domain.admin.actions import ACTION_MODELS, admin_action
from src.domain.admin.read import admin_get, admin_list, admin_overview, require_admin
from src.domain.admin.service import (
    accept_support_invitation,
    pending_support_invitations,
)
from src.domain.files.service import get_file
from src.domain.files.storage import FileError, storage_root

PAGE_SIZE = 7
ENTITIES = {
    "access_requests": "Заявки на доступ",
    "issues": "Проблемы",
    "users": "Пользователи",
    "companies": "УК",
    "houses": "Дома",
    "staff": "Сотрудники УК",
    "resident_grants": "Доступы жильцов",
    "offers": "Приглашения жильцов",
    "support_invites": "Приглашения поддержки",
    "apartments": "Квартиры",
    "files": "Вложения",
    "audit": "История действий",
    "resident_requests": "Заявки жильцов",
    "company_requests": "Регистрации УК",
    "house_requests": "Подключения домов",
}

ACTION_TITLES = {
    "invite_support": "Пригласить оператора",
    "revoke_support": "Отозвать доступ поддержки",
    "revoke_support_invite": "Отозвать приглашение",
    "assign_staff": "Назначить сотрудника УК",
    "revoke_staff": "Отозвать назначение сотрудника",
    "offer_resident": "Предложить доступ жильцу",
    "revoke_grant": "Отозвать доступ жильца",
    "edit_company": "Изменить УК",
    "edit_house": "Изменить дом",
    "edit_user_name": "Изменить ФИО",
    "edit_issue": "Изменить карточку проблемы",
    "set_issue_status": "Изменить статус проблемы",
    "request_status": "Изменить статус заявки",
    "add_access_message": "Написать в обсуждение",
    "request_decision": "Принять решение по заявке",
}


@dataclass(frozen=True)
class FormField:
    name: str
    prompt: str
    value_type: str = "text"
    optional: bool = False


FORMS: dict[str, tuple[FormField, ...]] = {
    "invite_support": (
        FormField("phone_number", "Номер оператора, например +79991234567:"),
    ),
    "revoke_support": (
        FormField("target_user_id", "UUID пользователя поддержки:", "uuid"),
    ),
    "revoke_support_invite": (FormField("invitation_id", "UUID приглашения:", "uuid"),),
    "assign_staff": (
        FormField("company_id", "UUID управляющей компании:", "uuid"),
        FormField("phone_number", "Номер сотрудника:", "phone"),
        FormField("can_manage_staff", "Может добавлять сотрудников? да/нет", "bool"),
        FormField("can_manage_residents", "Может добавлять жильцов? да/нет", "bool"),
        FormField(
            "can_manage_issues", "Может менять статусы и отвечать? да/нет", "bool"
        ),
    ),
    "revoke_staff": (FormField("assignment_id", "UUID назначения:", "uuid"),),
    "offer_resident": (
        FormField("house_id", "UUID дома:", "uuid"),
        FormField("entrance_number", "Номер подъезда:", "int"),
        FormField("apartment_number", "Номер квартиры:", "int"),
        FormField("phone_number", "Номер жильца:", "phone"),
        FormField(
            "valid_to",
            "Срок доступа ISO с часовым поясом или «.» без срока:",
            "datetime",
            True,
        ),
    ),
    "revoke_grant": (
        FormField("grant_id", "UUID доступа:", "uuid"),
        FormField("reason", "Причина отзыва или «.» без пояснения:", optional=True),
    ),
    "edit_company": (
        FormField("company_id", "UUID УК:", "uuid"),
        FormField("display_name", "Новое отображаемое название УК:"),
        FormField(
            "legal_name",
            "Новое юридическое название; «.» без изменения, «очистить» удалить:",
            optional=True,
        ),
        FormField(
            "inn", "Новый ИНН; «.» без изменения, «очистить» удалить:", optional=True
        ),
        FormField(
            "ogrn", "Новый ОГРН; «.» без изменения, «очистить» удалить:", optional=True
        ),
    ),
    "edit_house": (
        FormField("house_id", "UUID дома:", "uuid"),
        FormField("address_display", "Новый адрес дома:"),
        FormField(
            "entrance_count",
            "Количество подъездов; «.» без изменения, «очистить» удалить:",
            "int",
            True,
        ),
    ),
    "edit_user_name": (
        FormField("user_id", "UUID пользователя:", "uuid"),
        FormField("full_name", "Новое ФИО или «.» чтобы очистить:", optional=True),
    ),
    "edit_issue": (
        FormField("card_id", "UUID проблемы:", "uuid"),
        FormField("expected_version", "Текущая версия карточки:", "int"),
        FormField("category_id", "UUID категории (или «.» оставить прежнюю):", "uuid"),
        FormField("title", "Название проблемы (или «.» оставить прежнее):"),
        FormField(
            "scope_all_house",
            "Затронут весь дом? да/нет (или «.» оставить прежнее):",
            "bool",
        ),
        FormField(
            "target_entrances",
            "Номера подъездов как JSON-массив, например [1,2] (или «.» оставить прежние):",
            "array",
        ),
        FormField(
            "target_apartments",
            'Квартиры как JSON-массив объектов, например [{"entrance_number":1,"apartment_number":2}] (или «.» оставить прежние):',
            "array",
        ),
    ),
    "set_issue_status": (
        FormField("card_id", "UUID проблемы:", "uuid"),
        FormField(
            "status", "Статус: open, reviewing, needs_info, in_progress, closed:"
        ),
        FormField("note", "Пояснение или «.» без него:", optional=True),
        FormField(
            "close_result",
            "При закрытии: solved или invalid. Иначе «.»:",
            optional=True,
        ),
    ),
    "request_status": (
        FormField(
            "kind", "Вид заявки: resident, company_registration, house_addition:"
        ),
        FormField("request_id", "UUID заявки:", "uuid"),
        FormField("status", "Статус: open, reviewing, needs_info:"),
    ),
    "add_access_message": (
        FormField(
            "kind", "Вид заявки: resident, company_registration, house_addition:"
        ),
        FormField("request_id", "UUID заявки:", "uuid"),
        FormField("text", "Текст сообщения для обсуждения:"),
    ),
    "request_decision": (
        FormField(
            "kind", "Вид заявки: resident, company_registration, house_addition:"
        ),
        FormField("request_id", "UUID заявки:", "uuid"),
        FormField(
            "outcome",
            "Решение: granted/denied для жильца, approved/rejected для УК и дома:",
        ),
        FormField("decision_note", "Обязательное пояснение решения:"),
        FormField(
            "display_name",
            "Для УК: отображаемое название или «.» без изменения:",
            optional=True,
        ),
        FormField(
            "proposed_address_key",
            "Для дома: адресный ключ или «.» использовать адрес заявки:",
            optional=True,
        ),
        FormField(
            "entrance_count",
            "Для дома: число подъездов или «.» не указывать:",
            "int",
            True,
        ),
        FormField(
            "valid_to",
            "Для жильца: срок ISO с часовым поясом или «.» без срока:",
            "datetime",
            True,
        ),
    ),
}

ENTITY_ACTIONS = {
    "users": ("edit_user_name", "revoke_support"),
    "companies": ("edit_company", "assign_staff"),
    "houses": ("edit_house", "offer_resident"),
    "issues": ("set_issue_status", "edit_issue"),
    "staff": ("revoke_staff",),
    "resident_grants": ("revoke_grant",),
    "support_invites": ("revoke_support_invite",),
    "access_requests": ("request_status", "add_access_message", "request_decision"),
    "resident_requests": ("request_status", "add_access_message", "request_decision"),
    "company_requests": ("request_status", "add_access_message", "request_decision"),
    "house_requests": ("request_status", "add_access_message", "request_decision"),
}

ID_FIELD = {
    "users": "user_id",
    "companies": "company_id",
    "houses": "house_id",
    "issues": "card_id",
    "staff": "assignment_id",
    "resident_grants": "grant_id",
    "support_invites": "invitation_id",
    "access_requests": "request_id",
    "resident_requests": "request_id",
    "company_requests": "request_id",
    "house_requests": "request_id",
}


def menu() -> UiReply:
    return UiReply(
        "Кабинет администратора. Это отдельная роль: операторы поддержки не получают эти права.",
        [
            [
                Button("Заявки", "adm:list:access_requests:0"),
                Button("Проблемы", "adm:list:issues:0"),
            ],
            [
                Button("Люди", "adm:list:users:0"),
                Button("УК", "adm:list:companies:0"),
                Button("Дома", "adm:list:houses:0"),
            ],
            [Button("Пригласить оператора", "adm:new:invite_support")],
            [
                Button("Добавить сотрудника", "adm:new:assign_staff"),
                Button("Добавить жильца", "adm:new:offer_resident"),
            ],
            [
                Button("Остальные разделы", "adm:sections"),
                Button("Сводка", "adm:overview"),
            ],
            [Button("Системный редактор", "adm:sys:home:0")],
        ],
    )


def _sections() -> UiReply:
    names = (
        "staff",
        "resident_grants",
        "offers",
        "support_invites",
        "apartments",
        "files",
        "audit",
        "resident_requests",
        "company_requests",
        "house_requests",
    )
    return UiReply(
        "Остальные данные:",
        [[Button(ENTITIES[name], f"adm:list:{name}:0")] for name in names],
    )


def _int(raw: str, *, maximum: int = 10000) -> int:
    try:
        number = int(raw)
    except ValueError as error:
        raise ValueError("Некорректный номер страницы") from error
    if number < 0 or number > maximum:
        raise ValueError("Некорректный номер страницы")
    return number


def _uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as error:
        raise ValueError("Некорректный UUID") from error


def _short(raw: object, size: int = 45) -> str:
    value = str(raw or "—")
    return value if len(value) <= size else value[: size - 1].rstrip() + "…"


async def _list(
    session: AsyncSession,
    actor: User,
    entity: str,
    offset: int,
    *,
    q: str | None = None,
) -> UiReply:
    if entity not in ENTITIES:
        raise ValueError("Неизвестный раздел")
    data = await admin_list(session, actor, entity, q=q, limit=PAGE_SIZE, offset=offset)
    total = data["total"]
    items = data["items"]
    title = ENTITIES[entity]
    if q:
        title += f" · поиск: {_short(q, 50)}"
    lines = [f"{title} · {total} записей"]
    buttons: list[list[Button]] = []
    for i, row in enumerate(items, offset + 1):
        lines.append(
            f"{i}. {_short(row['title'], 80)} · {_short(row.get('status'), 20)}"
        )
        buttons.append(
            [
                Button(
                    f"Открыть {i}: {_short(row['title'], 25)}",
                    f"adm:get:{entity}:{row['id']}:0",
                )
            ]
        )
    if not items:
        lines.append("На этой странице записей нет.")
    kind = "searchpage" if q else "list"
    navigation = []
    if offset > 0:
        navigation.append(
            Button("Назад", f"adm:{kind}:{entity}:{max(0, offset - PAGE_SIZE)}")
        )
    if offset + len(items) < total:
        navigation.append(Button("Дальше", f"adm:{kind}:{entity}:{offset + PAGE_SIZE}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(
        [
            Button("Поиск", f"adm:search:{entity}"),
            Button("Без поиска", f"adm:list:{entity}:0"),
        ]
    )
    return UiReply("\n".join(lines), buttons)


def _detail_pages(data: dict[str, object]) -> list[str]:
    # MAX has a 4000-character message limit. Split verbose discussions and
    # issue history on lines so the complete record remains readable.
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    pages: list[str] = []
    current = ""
    for line in content.splitlines(keepends=True):
        while len(line) > 2800:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:2800])
            line = line[2800:]
        if len(current) + len(line) > 2800:
            pages.append(current)
            current = ""
        current += line
    if current or not pages:
        pages.append(current)
    return pages


async def _detail(
    session: AsyncSession, actor: User, entity: str, record_id: UUID, page: int
) -> UiReply:
    if entity not in ENTITIES:
        raise ValueError("Неизвестный раздел")
    data = await admin_get(session, actor, entity, record_id)
    pages = _detail_pages(data)
    if page >= len(pages):
        raise ValueError("Такой страницы записи нет")
    buttons: list[list[Button]] = []
    navigation = []
    if page:
        navigation.append(Button("Назад", f"adm:get:{entity}:{record_id}:{page - 1}"))
    if page + 1 < len(pages):
        navigation.append(Button("Дальше", f"adm:get:{entity}:{record_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    if page == 0:
        if entity == "files" and data.get("state") == "ready":
            buttons.append([Button("Получить вложение", f"adm:file:{record_id}")])
        if entity == "issues" and data.get("merged_into_id"):
            buttons.append(
                [
                    Button(
                        "Открыть основную карточку",
                        f"adm:get:issues:{data['merged_into_id']}:0",
                    )
                ]
            )
        for action in (
            ()
            if entity == "issues" and data.get("merged_into_id")
            else ENTITY_ACTIONS.get(entity, ())
        ):
            if action == "revoke_support" and data.get("kind") != "support":
                continue
            if action == "set_issue_status" and data.get("status") == "closed":
                continue
            buttons.append(
                [
                    Button(
                        ACTION_TITLES[action], f"adm:form:{action}:{entity}:{record_id}"
                    )
                ]
            )
    buttons.append([Button("К списку", f"adm:list:{entity}:0")])
    return UiReply(
        f"{ENTITIES[entity]} · {record_id} · {page + 1}/{len(pages)}\n{pages[page]}",
        buttons,
    )


async def _attachment(session: AsyncSession, actor: User, file_id: UUID) -> UiReply:
    """Send any ready file to the administrator after rechecking access."""
    await require_admin(session, actor)
    file, path = await get_file(session, actor, file_id=file_id, root=storage_root())
    if file.state != "ready":
        raise FileError(409, "file_not_ready", "Вложение ещё не готово")
    data = await asyncio.to_thread(_read_private_file, path, file.size_bytes)
    media = InputMediaBuffer(
        buffer=data,
        filename=Path(file.original_name).stem,
        type=UploadType.FILE,
    )
    return UiReply(
        f"Вложение: {file.original_name}",
        [[Button("К записи", f"adm:get:files:{file.id}:0")]],
        media=media,
    )


def _prefill(
    action: str, entity: str, row: dict[str, object], record_id: UUID
) -> dict[str, object]:
    field = ID_FIELD.get(entity)
    payload: dict[str, object] = {field: str(record_id)} if field else {}
    if action == "revoke_support":
        payload = {"target_user_id": str(record_id)}
    if action == "assign_staff":
        payload = {"company_id": str(record_id)}
    if action == "offer_resident":
        payload = {"house_id": str(record_id)}
    if action.startswith("request_") or action == "add_access_message":
        payload["kind"] = row["kind"]
    return payload


def _parse_field(field: FormField, text: str) -> object:
    value = text.strip()
    if field.optional and value == ".":
        return None
    if not value and not field.optional:
        raise ValueError("Поле обязательно. Отправьте значение или /cancel.")
    if field.value_type == "uuid":
        return str(_uuid(value))
    if field.value_type == "int":
        try:
            result = int(value)
        except ValueError as error:
            raise ValueError("Нужно положительное целое число") from error
        if result < 1:
            raise ValueError("Нужно положительное целое число")
        return result
    if field.value_type == "bool":
        lowered = value.casefold()
        if lowered in {"да", "yes", "true", "1"}:
            return True
        if lowered in {"нет", "no", "false", "0"}:
            return False
        raise ValueError("Ответьте «да» или «нет»")
    if field.value_type == "datetime":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Нужна дата ISO с часовым поясом") from error
        if parsed.tzinfo is None:
            raise ValueError("Укажите часовой пояс, например +03:00")
        return parsed.isoformat()
    if field.value_type == "array":
        try:
            result = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Нужен корректный JSON-массив") from error
        if not isinstance(result, list):
            raise ValueError("Нужен JSON-массив")
        return result
    return value


async def _next_field(session: AsyncSession, actor: User, dialog: BotDialog) -> UiReply:
    action = str(dialog.data["action"])
    payload = dict(dialog.data.get("payload", {}))
    fields = FORMS[action]
    defaults = dict(dialog.data.get("defaults", {}))
    for index, field in enumerate(fields):
        if field.name not in payload:
            await update_dialog(dialog, step=str(index))
            current = (
                f"\nСейчас: {json.dumps(defaults[field.name], ensure_ascii=False)}"
                if field.name in defaults
                else ""
            )
            return UiReply(
                f"{ACTION_TITLES[action]}\n{field.prompt}{current}\n/cancel — отмена"
            )
    await update_dialog(dialog, step="confirm")
    preview = {
        name: value
        for name, value in payload.items()
        if name not in dialog.data.get("skipped", [])
    }
    if action == "edit_issue" and preview.get("scope_all_house") is True:
        preview["target_entrances"] = []
        preview["target_apartments"] = []
    safe_payload = json.dumps(preview, ensure_ascii=False, indent=2, default=str)
    return UiReply(
        f"Подтвердите действие «{ACTION_TITLES[action]}»:\n{safe_payload[:2800]}",
        [[Button("Подтвердить", "adm:commit"), Button("Отмена", "adm:cancel")]],
    )


async def _start_form(
    session: AsyncSession,
    actor: User,
    action: str,
    *,
    entity: str | None = None,
    record_id: UUID | None = None,
) -> UiReply:
    if action not in FORMS or action not in ACTION_MODELS:
        raise ValueError(
            "Для этого действия нет формы. Используйте /admin do ACTION JSON."
        )
    payload: dict[str, object] = {}
    defaults: dict[str, object] = {}
    if entity and record_id:
        if action not in ENTITY_ACTIONS.get(entity, ()):
            raise ValueError("Действие недоступно для этого раздела")
        row = await admin_get(session, actor, entity, record_id)
        if entity == "issues" and row.get("merged_into_id"):
            raise AccessRuleError(
                409,
                "issue_not_editable",
                "Откройте основную активную карточку проблемы",
            )
        if (
            entity == "issues"
            and action == "set_issue_status"
            and row.get("status") == "closed"
        ):
            raise AccessRuleError(
                409,
                "issue_closed",
                "Закрытую проблему может переоткрыть только её автор",
            )
        payload = _prefill(action, entity, row, record_id)
        if action == "edit_issue":
            payload["expected_version"] = row["version"]
            targets = row.get("targets") or []
            defaults = {
                "category_id": row["category_id"],
                "title": row["title"],
                "scope_all_house": row["scope_all_house"],
                "target_entrances": [
                    item["entrance_number"]
                    for item in targets
                    if item.get("entrance_number") is not None
                ],
                "target_apartments": [
                    {
                        "entrance_number": item["apartment_entrance_number"],
                        "apartment_number": item["apartment_number"],
                    }
                    for item in targets
                    if item.get("apartment_number") is not None
                ],
            }
    dialog = await set_dialog(
        session,
        actor.id,
        flow_kind="admin_action",
        step="start",
        data={"action": action, "payload": payload, "defaults": defaults},
    )
    return await _next_field(session, actor, dialog)


async def _commit(session: AsyncSession, actor: User) -> UiReply:
    dialog = await get_dialog(session, actor.id)
    if dialog is None or dialog.flow_kind != "admin_action" or dialog.step != "confirm":
        raise ValueError("Форма уже закрыта. Начните действие заново")
    action = str(dialog.data["action"])
    payload = dict(dialog.data["payload"])
    for field in dialog.data.get("skipped", []):
        payload.pop(str(field), None)
    if action == "edit_issue" and payload.get("scope_all_house") is True:
        payload["target_entrances"] = []
        payload["target_apartments"] = []
    result = await admin_action(session, actor, action, payload)
    await clear_dialog(session, actor.id)
    return UiReply(
        f"Готово: {ACTION_TITLES[action]}. Запись {result['id']}",
        [[Button("Открыть запись", f"adm:get:{result['entity']}:{result['id']}:0")]],
    )


async def invitation_reply(session: AsyncSession, actor: User) -> UiReply | None:
    invitations = await pending_support_invitations(session, actor)
    if not invitations:
        return None
    return UiReply(
        "Вас пригласили в поддержку. Доступ появится только после вашего подтверждения.",
        [
            [Button("Принять приглашение", f"adm:accept:{item.id}")]
            for item in invitations
        ],
    )


async def handle_action(
    session: AsyncSession, actor: User, payload: str
) -> UiReply | None:
    if not payload.startswith("adm:"):
        return None
    parts = payload.split(":")
    if len(parts) == 3 and parts[1] == "accept":
        await accept_support_invitation(session, actor, invitation_id=_uuid(parts[2]))
        return UiReply("Приглашение принято. Теперь вам доступен кабинет поддержки.")
    if payload == "adm:invites":
        return await invitation_reply(session, actor) or UiReply(
            "Приглашений поддержки пока нет."
        )
    await require_admin(session, actor)
    if payload.startswith("adm:sys:"):
        return await admin_system.handle_action(session, actor, payload)
    if payload == "adm:home":
        await clear_dialog(session, actor.id)
        return menu()
    if payload == "adm:sections":
        await clear_dialog(session, actor.id)
        return _sections()
    if payload == "adm:overview":
        await clear_dialog(session, actor.id)
        counts = (await admin_overview(session, actor))["counts"]
        lines = ["Сводка:"] + [
            f"{ENTITIES.get(key, key)}: {value}" for key, value in counts.items()
        ]
        return UiReply("\n".join(lines)[:3500], [[Button("Разделы", "adm:sections")]])
    if len(parts) == 3 and parts[1] == "file":
        return await _attachment(session, actor, _uuid(parts[2]))
    if len(parts) == 4 and parts[1] in {"list", "searchpage"}:
        entity, offset = parts[2], _int(parts[3])
        if parts[1] == "list":
            await clear_dialog(session, actor.id)
            return await _list(session, actor, entity, offset)
        dialog = await get_dialog(session, actor.id)
        if (
            dialog is None
            or dialog.flow_kind != "admin_search"
            or dialog.data.get("entity") != entity
        ):
            raise ValueError("Поиск устарел. Запустите его снова")
        return await _list(
            session, actor, entity, offset, q=str(dialog.data.get("q", ""))
        )
    if len(parts) == 3 and parts[1] == "search":
        entity = parts[2]
        if entity not in ENTITIES:
            raise ValueError("Неизвестный раздел")
        await set_dialog(
            session,
            actor.id,
            flow_kind="admin_search",
            step="query",
            data={"entity": entity},
        )
        return UiReply(
            f"Что искать в разделе «{ENTITIES[entity]}»? Можно ввести UUID, имя, номер или адрес."
        )
    if len(parts) == 5 and parts[1] == "get":
        return await _detail(
            session, actor, parts[2], _uuid(parts[3]), _int(parts[4], maximum=1000)
        )
    if len(parts) == 3 and parts[1] == "new":
        return await _start_form(session, actor, parts[2])
    if len(parts) == 5 and parts[1] == "form":
        return await _start_form(
            session, actor, parts[2], entity=parts[3], record_id=_uuid(parts[4])
        )
    if payload == "adm:commit":
        return await _commit(session, actor)
    if payload == "adm:cancel":
        await clear_dialog(session, actor.id)
        return menu()
    return UiReply(
        "Кнопка устарела. Откройте кабинет администратора заново.",
        [[Button("Кабинет администратора", "adm:home")]],
    )


async def handle_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply | None:
    if dialog.flow_kind.startswith("admin_system_"):
        await require_admin(session, actor)
        return await admin_system.handle_text(session, actor, dialog, text)
    if dialog.flow_kind not in {"admin_search", "admin_action"}:
        return None
    await require_admin(session, actor)
    if dialog.flow_kind == "admin_search":
        if dialog.step != "query":
            return UiReply("Поиск завершён. Нажмите кнопку раздела или /admin.")
        q = text.strip()
        if not q or len(q) > 100:
            raise ValueError("Поисковый запрос должен содержать от 1 до 100 символов")
        entity = str(dialog.data["entity"])
        await update_dialog(dialog, step="results", data={"entity": entity, "q": q})
        return await _list(session, actor, entity, 0, q=q)
    if dialog.step == "confirm":
        if text.strip().casefold() in {"да", "подтверждаю"}:
            return await _commit(session, actor)
        return UiReply(
            "Нажмите «Подтвердить» или отправьте «да». Для отмены — /cancel.",
            [[Button("Подтвердить", "adm:commit")]],
        )
    action = str(dialog.data["action"])
    index = _int(dialog.step, maximum=100)
    field = FORMS[action][index]
    defaults = dict(dialog.data.get("defaults", {}))
    if (
        text.strip().casefold() == "очистить"
        and action in {"edit_company", "edit_house"}
        and field.optional
    ):
        value = "" if action == "edit_company" else None
    elif text.strip() == "." and field.name in defaults:
        value = defaults[field.name]
    else:
        value = _parse_field(field, text)
    if (
        action == "set_issue_status"
        and field.name == "status"
        and value not in {"open", "reviewing", "needs_info", "in_progress", "closed"}
    ):
        raise ValueError(
            "Допустимые статусы: open, reviewing, needs_info, in_progress, closed"
        )
    if (
        action == "set_issue_status"
        and field.name == "close_result"
        and value not in {None, "solved", "invalid"}
    ):
        raise ValueError("Результат закрытия: solved или invalid")
    payload = dict(dialog.data.get("payload", {}))
    skipped = list(dialog.data.get("skipped", []))
    if value is not None:
        payload[field.name] = value
    else:
        # Skipped optional fields must not be prompted again.
        payload[field.name] = None
        if action in {"edit_company", "edit_house"}:
            skipped.append(field.name)
    if (
        action in {"edit_company", "edit_house"}
        and field.optional
        and text.strip().casefold() == "очистить"
    ):
        payload[field.name] = value
        if field.name in skipped:
            skipped.remove(field.name)
    await update_dialog(
        dialog,
        data={
            "action": action,
            "payload": payload,
            "skipped": skipped,
            "defaults": defaults,
        },
    )
    return await _next_field(session, actor, dialog)


async def handle_command(
    session: AsyncSession, actor: User, command: str
) -> UiReply | None:
    if not (command == "/admin" or command.startswith("/admin ")):
        return None
    await require_admin(session, actor)
    if command == "/admin system" or command.startswith("/admin system "):
        return await admin_system.handle_command(session, actor, command)
    words = command.split(maxsplit=3)
    if len(words) == 1 or words[1] in {"help", "menu"}:
        await clear_dialog(session, actor.id)
        return menu()
    if words[1] == "overview":
        return await handle_action(session, actor, "adm:overview")
    if words[1] == "list" and len(words) >= 3:
        await clear_dialog(session, actor.id)
        entity = words[2]
        offset = _int(words[3]) if len(words) == 4 else 0
        return await _list(session, actor, entity, offset)
    if words[1] == "get" and len(words) == 4:
        await clear_dialog(session, actor.id)
        return await _detail(session, actor, words[2], _uuid(words[3]), 0)
    if words[1] == "file" and len(words) == 3:
        return await _attachment(session, actor, _uuid(words[2]))
    if words[1] == "search" and len(words) == 4:
        # /admin search ENTITY QUERY, where QUERY may contain spaces.
        entity, q = words[2], words[3].strip()
        if not q or len(q) > 100:
            raise ValueError("Поисковый запрос должен содержать от 1 до 100 символов")
        await set_dialog(
            session,
            actor.id,
            flow_kind="admin_search",
            step="results",
            data={"entity": entity, "q": q},
        )
        return await _list(session, actor, entity, 0, q=q)
    if words[1] == "new" and len(words) == 3:
        return await _start_form(session, actor, words[2])
    if words[1] == "do" and len(words) == 4:
        action = words[2]
        try:
            payload = json.loads(words[3])
        except json.JSONDecodeError as error:
            raise ValueError("После действия нужен корректный объект JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Данные действия должны быть объектом JSON")
        result = await admin_action(session, actor, action, payload)
        await clear_dialog(session, actor.id)
        return UiReply(
            f"Готово: {action}. Запись {result['id']}",
            [
                [
                    Button(
                        "Открыть запись", f"adm:get:{result['entity']}:{result['id']}:0"
                    )
                ]
            ],
        )
    return UiReply(
        "Команды: /admin, /admin overview, /admin list РАЗДЕЛ [СМЕЩЕНИЕ], "
        "/admin search РАЗДЕЛ ЗАПРОС, /admin get РАЗДЕЛ UUID, "
        "/admin file UUID, /admin new ДЕЙСТВИЕ, /admin do ДЕЙСТВИЕ JSON. "
        "Разделы: " + ", ".join(ENTITIES) + ". Действия: " + ", ".join(ACTION_MODELS),
    )
