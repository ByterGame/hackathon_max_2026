"""Guided MAX interface for the administrator's row-level system editor."""

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.ui import Button, UiReply, clear_dialog, get_dialog, set_dialog, update_dialog
from src.db.models import BotDialog, User
from src.domain.admin.system import (
    system_delete,
    system_get,
    system_list,
    system_operations,
    system_patch,
    system_schema,
)

PAGE_SIZE = 6
SECTION_SIZE = 8


def _integer(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("Некорректный номер страницы") from error
    if value < 0 or value > 10000:
        raise ValueError("Некорректный номер страницы")
    return value


def _short(value: object, limit: int = 45) -> str:
    text = str(value if value is not None else "—")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title(data: dict[str, object], fallback: str) -> str:
    for field in (
        "full_name", "display_name", "address_display", "title",
        "original_name", "action", "phone_number", "max_user_id",
    ):
        if data.get(field):
            return _short(data[field], 55)
    return _short(fallback, 55)


def _pages(value: object) -> list[str]:
    body = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    pages: list[str] = []
    current = ""
    for line in body.splitlines(keepends=True):
        while len(line) > 2600:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:2600])
            line = line[2600:]
        if len(current) + len(line) > 2600:
            pages.append(current)
            current = ""
        current += line
    if current or not pages:
        pages.append(current)
    return pages


async def _entity_info(session: AsyncSession, actor: User, entity: str) -> dict[str, object]:
    schema = await system_schema(session, actor)
    for item in schema["entities"]:
        if item["key"] == entity:
            return item
    raise ValueError("Неизвестная таблица системного редактора")


async def home(session: AsyncSession, actor: User, page: int = 0) -> UiReply:
    schema = await system_schema(session, actor)
    entities = schema["entities"]
    selected = entities[page * SECTION_SIZE : (page + 1) * SECTION_SIZE]
    if not selected and page:
        raise ValueError("Такой страницы таблиц нет")
    buttons = [
        [Button(str(item["label"]), f"adm:sys:list:{item['key']}:0")]
        for item in selected
    ]
    navigation: list[Button] = []
    if page:
        navigation.append(Button("Назад", f"adm:sys:home:{page - 1}"))
    if (page + 1) * SECTION_SIZE < len(entities):
        navigation.append(Button("Дальше", f"adm:sys:home:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([Button("Журнал действий администратора", "adm:sys:operations:0")])
    buttons.append([Button("Обычная админка", "adm:home")])
    return UiReply(
        f"Системный редактор · таблицы {page * SECTION_SIZE + 1}–"
        f"{page * SECTION_SIZE + len(selected)} из {len(entities)}. "
        "Изменения записываются с причиной; удаление может быть необратимым.",
        buttons,
    )


async def _list(
    session: AsyncSession,
    actor: User,
    entity: str,
    offset: int,
    *,
    q: str | None = None,
) -> UiReply:
    info = await _entity_info(session, actor, entity)
    result = await system_list(
        session, actor, entity, q=q, limit=PAGE_SIZE, offset=offset
    )
    items = result["items"]
    lines = [f"{info['label']} · {result['total']} записей"]
    buttons: list[list[Button]] = []
    for number, item in enumerate(items, start=offset + 1):
        label = _title(item["data"], item["id"])
        lines.append(f"{number}. {label}")
        buttons.append(
            [Button(f"Открыть {number}: {_short(label, 24)}", f"adm:sys:open:{entity}:{item['id']}:0")]
        )
    if not items:
        lines.append("Записей на этой странице нет.")
    navigation: list[Button] = []
    action = "searchpage" if q else "list"
    if offset:
        navigation.append(
            Button("Назад", f"adm:sys:{action}:{entity}:{max(0, offset - PAGE_SIZE)}")
        )
    if offset + len(items) < result["total"]:
        navigation.append(
            Button("Дальше", f"adm:sys:{action}:{entity}:{offset + PAGE_SIZE}")
        )
    if navigation:
        buttons.append(navigation)
    buttons.append([Button("Поиск", f"adm:sys:search:{entity}")])
    buttons.append([Button("Таблицы", "adm:sys:home:0")])
    if q:
        lines.append(f"Поиск: {_short(q, 70)}")
    return UiReply("\n".join(lines), buttons)


async def _detail(
    session: AsyncSession, actor: User, entity: str, row_id: str, page: int
) -> UiReply:
    info = await _entity_info(session, actor, entity)
    detail = await system_get(session, actor, entity, row_id)
    pages = _pages(detail)
    if page >= len(pages):
        raise ValueError("Такой страницы записи нет")
    buttons: list[list[Button]] = []
    navigation: list[Button] = []
    if page:
        navigation.append(Button("Назад", f"adm:sys:open:{entity}:{row_id}:{page - 1}"))
    if page + 1 < len(pages):
        navigation.append(Button("Дальше", f"adm:sys:open:{entity}:{row_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    if page == 0:
        if any(field.get("editable") for field in info["fields"]):
            buttons.append([Button("Изменить поле", f"adm:sys:patch:{entity}:{row_id}")])
        modes = detail.get("delete_modes") or []
        if "soft" in modes:
            buttons.append([Button("Архивировать/отключить", f"adm:sys:remove:soft:{entity}:{row_id}")])
        if "hard" in modes:
            buttons.append([Button("Удалить безвозвратно", f"adm:sys:remove:hard:{entity}:{row_id}")])
    buttons.append([Button("К списку", f"adm:sys:list:{entity}:0")])
    return UiReply(
        f"{info['label']} · {_short(row_id, 90)} · {page + 1}/{len(pages)}\n"
        f"{pages[page]}",
        buttons,
    )


async def _operations(
    session: AsyncSession, actor: User, offset: int, *, q: str | None = None
) -> UiReply:
    result = await system_operations(
        session, actor, q=q, limit=PAGE_SIZE, offset=offset
    )
    items = result["items"]
    lines = [f"Журнал действий администратора · {result['total']} записей"]
    buttons: list[list[Button]] = []
    page_action = "operations-page" if q else "operations"
    for number, item in enumerate(items, start=offset + 1):
        label = f"{item['operation']} · {item['entity_key']}"
        lines.append(
            f"{number}. {_short(label, 80)} · {_short(item['row_key'], 40)}"
        )
        buttons.append(
            [Button(f"Открыть {number}", f"adm:sys:open:system.admin_operations:{item['id']}:0")]
        )
    if not items:
        lines.append("Записей на этой странице нет.")
    navigation: list[Button] = []
    if offset:
        navigation.append(
            Button("Назад", f"adm:sys:{page_action}:{max(0, offset - PAGE_SIZE)}")
        )
    if offset + len(items) < result["total"]:
        navigation.append(
            Button("Дальше", f"adm:sys:{page_action}:{offset + PAGE_SIZE}")
        )
    if navigation:
        buttons.append(navigation)
    buttons.append([Button("Поиск по журналу", "adm:sys:search-operations")])
    buttons.append([Button("Таблицы", "adm:sys:home:0")])
    if q:
        lines.append(f"Поиск: {_short(q, 70)}")
    return UiReply("\n".join(lines), buttons)


def _parse_value(raw: str, field: dict[str, object]) -> object:
    value = raw.strip()
    if value == "/null":
        if field.get("nullable"):
            return None
        raise ValueError("Это поле нельзя очистить")
    value_type = field.get("type")
    if value_type == "text":
        if not value and not field.get("nullable"):
            raise ValueError("Введите непустой текст")
        return raw
    if value_type == "int":
        try:
            return int(value)
        except ValueError as error:
            raise ValueError("Введите целое число") from error
    if value_type == "bool":
        if value.casefold() in {"да", "true", "1", "yes"}:
            return True
        if value.casefold() in {"нет", "false", "0", "no"}:
            return False
        raise ValueError("Введите «да» или «нет»")
    if value_type == "uuid":
        try:
            return str(UUID(value))
        except ValueError as error:
            raise ValueError("Введите UUID") from error
    if value_type == "datetime":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Введите дату и время ISO с часовым поясом") from error
        if parsed.tzinfo is None:
            raise ValueError("Укажите часовой пояс, например +03:00")
        return parsed.isoformat()
    if value_type == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Введите корректный JSON") from error
    raise ValueError("Тип поля не поддерживается в боте")


async def _start_patch(
    session: AsyncSession, actor: User, entity: str, row_id: str
) -> UiReply:
    info = await _entity_info(session, actor, entity)
    detail = await system_get(session, actor, entity, row_id)
    fields = [field for field in info["fields"] if field.get("editable")]
    if not fields:
        raise ValueError("В этой таблице нет редактируемых полей")
    await set_dialog(
        session, actor.id,
        flow_kind="admin_system_patch", step="field",
        data={"entity": entity, "row_id": row_id, "etag": detail["item"]["etag"]},
    )
    names = ", ".join(str(field["name"]) for field in fields)
    return UiReply(
        f"Какое поле изменить?\n{names[:2500]}\n"
        "Отправьте точное имя поля. /cancel — выход."
    )


async def _start_delete(
    session: AsyncSession, actor: User, entity: str, row_id: str, mode: str
) -> UiReply:
    detail = await system_get(session, actor, entity, row_id)
    if mode not in (detail.get("delete_modes") or []):
        raise ValueError("Эту запись нельзя удалить через редактор")
    dependencies = detail.get("dependencies") or []
    summary = "; ".join(
        f"{item['entity']}: {item['count']}" for item in dependencies if item.get("count")
    ) or "связанных записей не найдено"
    await set_dialog(
        session, actor.id,
        flow_kind="admin_system_delete", step="reason",
        data={
            "entity": entity, "row_id": row_id,
            "etag": detail["item"]["etag"], "mode": mode,
        },
    )
    action = "архивации/отключения" if mode == "soft" else "БЕЗВОЗВРАТНОГО УДАЛЕНИЯ"
    return UiReply(
        f"Причина {action} записи {entity}:{row_id}?\n"
        f"Зависимости: {summary[:1200]}.\n"
        "Напишите причину. /cancel — выход."
    )


async def _commit_patch(session: AsyncSession, actor: User, dialog: BotDialog) -> UiReply:
    if dialog.flow_kind != "admin_system_patch" or dialog.step != "confirm":
        raise ValueError("Форма изменения устарела")
    data = dialog.data
    result = await system_patch(
        session, actor,
        str(data["entity"]), str(data["row_id"]), str(data["etag"]),
        str(data["reason"]), {str(data["field"]): data["value"]},
    )
    await clear_dialog(session, actor.id)
    item = result["item"]
    return UiReply(
        f"Изменение сохранено. Операция {result['operation_id']}.",
        [[Button("Открыть запись", f"adm:sys:open:{data['entity']}:{item['id']}:0")]],
    )


async def _commit_delete(session: AsyncSession, actor: User, dialog: BotDialog) -> UiReply:
    if dialog.flow_kind != "admin_system_delete" or dialog.step != "confirm":
        raise ValueError("Форма удаления устарела")
    data = dialog.data
    result = await system_delete(
        session, actor,
        str(data["entity"]), str(data["row_id"]), str(data["etag"]),
        str(data["reason"]), str(data["mode"]),
    )
    await clear_dialog(session, actor.id)
    return UiReply(
        f"Операция выполнена: {result['mode']}. Номер {result['operation_id']}.",
        [[Button("К списку", f"adm:sys:list:{data['entity']}:0")]],
    )


async def handle_action(
    session: AsyncSession, actor: User, payload: str
) -> UiReply | None:
    if not payload.startswith("adm:sys:"):
        return None
    if payload == "adm:sys:patch-confirm" or payload == "adm:sys:delete-confirm":
        dialog = await get_dialog(session, actor.id)
        if dialog is None:
            raise ValueError("Форма уже закрыта")
        if payload == "adm:sys:patch-confirm":
            return await _commit_patch(session, actor, dialog)
        return await _commit_delete(session, actor, dialog)
    if payload == "adm:sys:null":
        dialog = await get_dialog(session, actor.id)
        if dialog is None or dialog.flow_kind != "admin_system_patch" or dialog.step != "value":
            raise ValueError("Сначала выберите поле для изменения")
        if not dialog.data["field_meta"].get("nullable"):
            raise ValueError("Это поле нельзя очистить")
        await update_dialog(dialog, step="reason", data={**dialog.data, "value": None})
        return UiReply("Поле будет очищено. Напишите причину изменения (5–2000 символов).")
    if payload.startswith("adm:sys:home:"):
        await clear_dialog(session, actor.id)
        return await home(session, actor, _integer(payload.removeprefix("adm:sys:home:")))
    if payload.startswith("adm:sys:operations:"):
        await clear_dialog(session, actor.id)
        return await _operations(
            session, actor, _integer(payload.removeprefix("adm:sys:operations:"))
        )
    if payload.startswith("adm:sys:operations-page:"):
        dialog = await get_dialog(session, actor.id)
        if dialog is None or dialog.flow_kind != "admin_system_operations_search":
            raise ValueError("Поиск устарел. Начните его заново")
        return await _operations(
            session, actor,
            _integer(payload.removeprefix("adm:sys:operations-page:")),
            q=str(dialog.data["q"]),
        )
    if payload == "adm:sys:search-operations":
        await set_dialog(
            session, actor.id, flow_kind="admin_system_operations_search",
            step="query", data={},
        )
        return UiReply("Что искать в журнале? Отправьте ID, название таблицы или часть причины.")
    if payload.startswith("adm:sys:list:") or payload.startswith("adm:sys:searchpage:"):
        searched = payload.startswith("adm:sys:searchpage:")
        raw = payload.removeprefix("adm:sys:searchpage:" if searched else "adm:sys:list:")
        entity, offset_text = raw.rsplit(":", 1)
        offset = _integer(offset_text)
        if searched:
            dialog = await get_dialog(session, actor.id)
            if dialog is None or dialog.flow_kind != "admin_system_search" or dialog.data.get("entity") != entity:
                raise ValueError("Поиск устарел. Начните его заново")
            return await _list(session, actor, entity, offset, q=str(dialog.data["q"]))
        await clear_dialog(session, actor.id)
        return await _list(session, actor, entity, offset)
    if payload.startswith("adm:sys:search:"):
        entity = payload.removeprefix("adm:sys:search:")
        await _entity_info(session, actor, entity)
        await set_dialog(
            session, actor.id, flow_kind="admin_system_search", step="query",
            data={"entity": entity},
        )
        return UiReply("Что искать? Напишите значение поля, часть названия или ID.")
    if payload.startswith("adm:sys:open:"):
        entity, rest = payload.removeprefix("adm:sys:open:").split(":", 1)
        row_id, page_text = rest.rsplit(":", 1)
        return await _detail(session, actor, entity, row_id, _integer(page_text))
    if payload.startswith("adm:sys:patch:"):
        entity, row_id = payload.removeprefix("adm:sys:patch:").split(":", 1)
        return await _start_patch(session, actor, entity, row_id)
    if payload.startswith("adm:sys:remove:"):
        mode, entity, row_id = payload.removeprefix("adm:sys:remove:").split(":", 2)
        return await _start_delete(session, actor, entity, row_id, mode)
    raise ValueError("Неизвестная кнопка системного редактора")


async def handle_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply | None:
    if not dialog.flow_kind.startswith("admin_system_"):
        return None
    if dialog.flow_kind == "admin_system_operations_search":
        if dialog.step != "query":
            return UiReply("Поиск завершён. Откройте запись или начните новый поиск.")
        q = text.strip()
        if not q or len(q) > 100:
            raise ValueError("Введите от 1 до 100 символов")
        await update_dialog(dialog, step="results", data={"q": q})
        return await _operations(session, actor, 0, q=q)
    if dialog.flow_kind == "admin_system_search":
        q = text.strip()
        if not q or len(q) > 100:
            raise ValueError("Введите от 1 до 100 символов")
        await update_dialog(dialog, step="results", data={"entity": dialog.data["entity"], "q": q})
        return await _list(session, actor, str(dialog.data["entity"]), 0, q=q)
    if dialog.flow_kind == "admin_system_delete":
        if dialog.step != "reason":
            return UiReply("Подтвердите удаление кнопкой или отправьте /cancel.")
        reason = text.strip()
        if len(reason) < 5 or len(reason) > 2000:
            raise ValueError("Причина должна содержать от 5 до 2000 символов")
        data = {**dialog.data, "reason": reason}
        await update_dialog(dialog, step="confirm", data=data)
        action = "АРХИВИРОВАТЬ" if data["mode"] == "soft" else "УДАЛИТЬ БЕЗВОЗВРАТНО"
        return UiReply(
            f"Последнее подтверждение: {action} {data['entity']}:{data['row_id']}?\n"
            f"Причина: {reason}",
            [[Button(action, "adm:sys:delete-confirm"), Button("Отмена", "adm:sys:home:0")]],
        )
    if dialog.flow_kind == "admin_system_patch":
        data = dict(dialog.data)
        if dialog.step == "field":
            info = await _entity_info(session, actor, str(data["entity"]))
            field = next(
                (item for item in info["fields"] if item["name"] == text.strip() and item.get("editable")),
                None,
            )
            if field is None:
                raise ValueError("Нет такого редактируемого поля")
            data["field"] = field["name"]
            data["field_meta"] = field
            await update_dialog(dialog, step="value", data=data)
            if field.get("clear_only"):
                return UiReply("Это поле можно только очистить.", [[Button("Очистить поле", "adm:sys:null")]])
            return UiReply(
                f"Новое значение для {field['name']} ({field['type']}). "
                "Для пустого значения отправьте /null или нажмите кнопку.",
                [[Button("Очистить поле", "adm:sys:null")]] if field.get("nullable") else [],
            )
        if dialog.step == "value":
            if data["field_meta"].get("clear_only") and text.strip() != "/null":
                raise ValueError("Это поле можно только очистить")
            data["value"] = _parse_value(text, data["field_meta"])
            await update_dialog(dialog, step="reason", data=data)
            return UiReply("Напишите причину изменения (5–2000 символов).")
        if dialog.step == "reason":
            reason = text.strip()
            if len(reason) < 5 or len(reason) > 2000:
                raise ValueError("Причина должна содержать от 5 до 2000 символов")
            data["reason"] = reason
            await update_dialog(dialog, step="confirm", data=data)
            preview = json.dumps(
                {"entity": data["entity"], "id": data["row_id"], "changes": {data["field"]: data["value"]}, "reason": reason},
                ensure_ascii=False, default=str,
            )
            return UiReply(
                f"Подтвердите изменение:\n{preview[:2500]}",
                [[Button("Сохранить", "adm:sys:patch-confirm"), Button("Отмена", "adm:sys:home:0")]],
            )
        return UiReply("Подтвердите изменение кнопкой или отправьте /cancel.")
    return None


async def handle_command(
    session: AsyncSession, actor: User, command: str
) -> UiReply:
    words = command.split(maxsplit=5)
    if len(words) <= 2:
        await clear_dialog(session, actor.id)
        return await home(session, actor)
    if words[2] == "operations":
        q = " ".join(words[3:]).strip()
        if q:
            if len(q) > 100:
                raise ValueError("Поисковый запрос слишком длинный")
            await set_dialog(
                session, actor.id, flow_kind="admin_system_operations_search",
                step="results", data={"q": q},
            )
        else:
            await clear_dialog(session, actor.id)
        return await _operations(session, actor, 0, q=q or None)
    if words[2] == "list" and len(words) >= 4:
        await clear_dialog(session, actor.id)
        return await _list(session, actor, words[3], 0)
    if words[2] == "get" and len(words) >= 5:
        await clear_dialog(session, actor.id)
        return await _detail(session, actor, words[3], words[4], 0)
    if words[2] == "search" and len(words) >= 5:
        q = " ".join(words[4:]).strip()
        if not q:
            raise ValueError("Укажите поисковый запрос")
        await set_dialog(
            session, actor.id, flow_kind="admin_system_search", step="results",
            data={"entity": words[3], "q": q},
        )
        return await _list(session, actor, words[3], 0, q=q)
    return UiReply(
        "Команды: /admin system, /admin system operations [ЗАПРОС], "
        "/admin system list ТАБЛИЦА, "
        "/admin system get ТАБЛИЦА ID, /admin system search ТАБЛИЦА ЗАПРОС. "
        "Изменение и удаление доступны кнопками в записи."
    )
