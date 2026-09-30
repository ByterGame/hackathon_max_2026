"""Текстовые команды доступа. Бизнес-правила общие с HTTP-ручками."""

import shlex
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.access_discussion import discussion_pages, page_number
from src.db.models import User
from src.domain.access import company, queries, requests, resident, staff
from src.domain.access.rules import AccessRuleError
from src.domain.profile import has_confirmed_full_name, update_profile_name


class UsageError(ValueError):
    pass


HELP = """Команды доступа (ID можно скопировать из списков):
/access houses <часть адреса>
/access profile [name <ФИО>] — посмотреть или явно изменить общее ФИО
/access resident apply <house_id> <подъезд> <квартира> [ФИО при первой заявке]
/access resident edit <request_id> <подъезд> <квартира>
/access resident decide <request_id> grant|deny <пояснение> [until=2026-12-01T00:00:00+03:00]
/access resident offer <house_id> <подъезд> <квартира> <телефон> [until=...]
/access resident offers | grants | people <company_id> [house_id]
/access resident respond <offer_id> yes|no
/access resident extend <grant_id> <дата ISO>; revoke <grant_id> <причина>
/access company register <телефон> <название> | <описание>
/access company house reg:<request_id>|company:<company_id> <подъезды> <квартиры> <адрес>
/access company decide <request_id> approve|reject <название или -> | <пояснение>
/access company house_decide <request_id> approve|reject <пояснение> [entrances=N] [apartments=N]
/access company house_details <house_id> <подъезды> <квартиры> — исправить подключённый дом
/access company houses <company_id>
/access staff list <company_id>; assign <company_id> <телефон> <0|1> <0|1> <0|1>; revoke <assignment_id>
/access requests list <company_registration|house_addition|resident> [house_id]
/access requests show <вид> <request_id> [страница обсуждения]
/access requests message|status|cancel|cancel_resolve <вид> <request_id> ...
Для нескольких слов используйте кавычки или пишите остаток строки свободно."""


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise UsageError("Нужен корректный UUID из списка заявок/домов") from error


def _positive(value: str, label: str = "Номер квартиры") -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise UsageError(f"{label} должен быть целым числом") from error
    if number <= 0:
        raise UsageError(f"{label} должен быть положительным")
    return number


def _bool(value: str) -> bool:
    if value.lower() in {"1", "yes", "да", "true", "approve", "accept"}:
        return True
    if value.lower() in {"0", "no", "нет", "false", "reject", "decline"}:
        return False
    raise UsageError("Укажите yes/no или 1/0")


def _until(value: str) -> datetime:
    raw = value.removeprefix("until=")
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise UsageError("Дата должна быть в ISO-формате с часовым поясом") from error
    if result.tzinfo is None:
        raise UsageError("Укажите часовой пояс даты, например +03:00")
    return result


def _required(parts: list[str], count: int, usage: str) -> None:
    if len(parts) < count:
        raise UsageError(f"Формат: {usage}")


def _tail(parts: list[str], start: int) -> str:
    value = " ".join(parts[start:]).strip()
    if not value:
        raise UsageError("Введите текст после обязательных аргументов")
    return value


def _split_note(parts: list[str], start: int) -> tuple[str | None, str]:
    if "|" not in parts[start:]:
        raise UsageError("Разделите название и пояснение знаком | с пробелами")
    bar = parts.index("|", start)
    name = " ".join(parts[start:bar]).strip()
    note = " ".join(parts[bar + 1 :]).strip()
    if not note:
        raise UsageError("После | нужно пояснение")
    return (None if name in {"", "-"} else name), note


def _list(items: list[dict], *, empty: str = "Пока ничего нет.") -> str:
    if not items:
        return empty
    lines = []
    for item in items[:20]:
        label = item.get("address_display") or item.get("entered_address") or ""
        status = item.get("status") or ""
        identifier = item.get("id") or item.get("grant_id") or ""
        details = []
        if item.get("full_name") or item.get("submitted_full_name"):
            details.append(item.get("full_name") or item["submitted_full_name"])
        if item.get("phone_number"):
            details.append(item["phone_number"])
        if item.get("entrance_number") is not None:
            details.append(f"подъезд {item['entrance_number']}")
        if item.get("apartment_number") is not None:
            details.append(f"квартира {item['apartment_number']}")
        if "can_manage_staff" in item:
            enabled = [
                name
                for flag, name in (
                    ("can_manage_staff", "сотрудники"),
                    ("can_manage_residents", "жильцы"),
                    ("can_manage_issues", "обращения"),
                )
                if item.get(flag)
            ]
            details.append("права: " + (", ".join(enabled) or "только просмотр"))
        lines.append(
            " · ".join(
                str(value) for value in (identifier, status, label, *details) if value
            )
        )
    if len(items) > 20:
        lines.append("Показаны первые 20 записей.")
    return "\n".join(lines)


def _request_view(data: dict, *, actor_id: str, page: int = 1) -> str:
    fields = [
        f"Заявка {data['id']} ({data['kind']})",
        f"Статус: {data['status']}",
    ]
    for name, label in (
        ("house_id", "Дом"),
        ("address_display", "Адрес"),
        ("submitted_full_name", "ФИО"),
        ("submitted_entrance_number", "Подъезд"),
        ("submitted_apartment_number", "Квартира"),
        ("phone_number", "Номер для доступа"),
        ("entered_address", "Адрес дома"),
        ("free_text", "Описание"),
        ("decision_note", "Пояснение"),
    ):
        if data.get(name) is not None:
            fields.append(f"{label}: {data[name]}")
    if data.get("outcome"):
        fields.append(f"Результат: {data['outcome']}")
    if data.get("cancel_requested_by"):
        fields.append("Ожидается ответ второй стороны на запрос отмены.")
    discussion = data.get("discussion") or []
    summary = "\n".join(fields)
    if not discussion:
        if page != 1:
            return "В обсуждении пока нет сообщений; доступна только первая страница."
        return summary[:3500] + "\nОбсуждение: сообщений пока нет."
    pages = discussion_pages(
        discussion,
        actor_id=actor_id,
        applicant_id=data["applicant_user_id"],
        max_chars=2600,
    )
    if page > len(pages):
        return f"Страницы {page} нет. Всего страниц обсуждения: {len(pages)}."
    if len(summary) > 700:
        summary = summary[:697].rstrip() + "…"
    command = f"/access requests show {data['kind']} {data['id']}"
    navigation = []
    if page > 1:
        navigation.append(f"Назад: {command} {page - 1}")
    if page < len(pages):
        navigation.append(f"Дальше: {command} {page + 1}")
    return (
        f"{summary}\nОбсуждение · страница {page}/{len(pages)} "
        f"· сообщений {len(discussion)}:\n{pages[page - 1]}"
        + ("\n" + "\n".join(navigation) if navigation else "")
    )


async def _resident(session: AsyncSession, user: User, parts: list[str]) -> str:
    _required(
        parts,
        2,
        "/access resident <apply|edit|decide|offer|offers|grants|people|respond|extend|revoke> ...",
    )
    action = parts[1]
    if action == "apply":
        _required(
            parts, 5, "/access resident apply <house_id> <подъезд> <квартира> [ФИО при первой заявке]"
        )
        entrance = _positive(parts[3], "Номер подъезда")
        apartment = _positive(parts[4])
        row = await resident.create_resident_request(
            session,
            user,
            house_id=_uuid(parts[2]),
            apartment_number=apartment,
            full_name=" ".join(parts[5:]).strip() or None,
            entrance_number=entrance,
        )
        return f"Заявка отправлена: {row.id}. Статус: открыта."
    if action == "edit":
        _required(
            parts, 5, "/access resident edit <request_id> <подъезд> <квартира>"
        )
        entrance = _positive(parts[3], "Номер подъезда")
        apartment = _positive(parts[4])
        row = await resident.update_resident_request(
            session,
            user,
            request_id=_uuid(parts[2]),
            apartment_number=apartment,
            full_name=" ".join(parts[5:]).strip() or None,
            entrance_number=entrance,
        )
        return f"Заявка {row.id} исправлена."
    if action == "decide":
        _required(
            parts,
            5,
            "/access resident decide <request_id> grant|deny <пояснение> [until=...]",
        )
        outcome = {"grant": "granted", "deny": "denied"}.get(parts[3])
        if outcome is None:
            raise UsageError("Результат: grant или deny")
        tail = parts[4:]
        valid_to = _until(tail.pop()) if tail[-1].startswith("until=") else None
        row, grant = await resident.decide_resident_request(
            session,
            user,
            request_id=_uuid(parts[2]),
            outcome=outcome,
            decision_note=_tail(tail, 0),
            valid_to=valid_to,
        )
        return f"Заявка {row.id} закрыта: {outcome}." + (
            f" Доступ: {grant.id}." if grant else ""
        )
    if action == "offer":
        _required(
            parts,
            6,
            "/access resident offer <house_id> <подъезд> <квартира> <телефон> [until=...]",
        )
        entrance = _positive(parts[3], "Номер подъезда")
        apartment = _positive(parts[4])
        phone_index = 5
        valid_to = _until(parts[phone_index + 1]) if len(parts) > phone_index + 1 else None
        row = await resident.create_resident_offer(
            session,
            user,
            house_id=_uuid(parts[2]),
            apartment_number=apartment,
            phone_number=parts[phone_index],
            valid_to=valid_to,
            entrance_number=entrance,
        )
        return f"Предложение доступа создано: {row.id}. Жилец должен его принять."
    if action == "offers":
        return _list(await queries.list_offers(session, user))
    if action == "grants":
        return _list(await queries.list_grants(session, user))
    if action == "people":
        _required(parts, 3, "/access resident people <company_id> [house_id]")
        items = await queries.list_residents(
            session,
            user,
            company_id=_uuid(parts[2]),
            house_id=_uuid(parts[3]) if len(parts) > 3 else None,
        )
        return _list(items)
    if action == "respond":
        _required(parts, 4, "/access resident respond <offer_id> yes|no")
        row, grant = await resident.respond_resident_offer(
            session, user, offer_id=_uuid(parts[2]), accept=_bool(parts[3])
        )
        return f"Предложение {row.id}: {row.status}." + (
            f" Доступ: {grant.id}." if grant else ""
        )
    if action == "extend":
        _required(parts, 4, "/access resident extend <grant_id> <дата ISO>")
        row = await resident.change_resident_grant(
            session,
            user,
            grant_id=_uuid(parts[2]),
            action="extend",
            valid_to=_until(parts[3]),
            reason=None,
        )
        return f"Доступ {row.id} продлён до {row.valid_to.isoformat()}."
    if action == "revoke":
        _required(parts, 4, "/access resident revoke <grant_id> <причина>")
        row = await resident.change_resident_grant(
            session,
            user,
            grant_id=_uuid(parts[2]),
            action="revoke",
            valid_to=None,
            reason=_tail(parts, 3),
        )
        return f"Доступ {row.id} отозван."
    raise UsageError("Неизвестная команда жильца. Отправьте /access help")


async def _company(session: AsyncSession, user: User, parts: list[str]) -> str:
    _required(
        parts, 2, "/access company <register|house|decide|house_decide|house_details|houses> ..."
    )
    action = parts[1]
    if action == "register":
        _required(
            parts, 6, "/access company register <телефон> <название> | <описание>"
        )
        name, description = _split_note(parts, 3)
        row = await company.create_company_registration(
            session,
            user,
            phone_number=parts[2],
            proposed_company_name=name,
            free_text=description,
        )
        return f"Обращение о регистрации УК отправлено: {row.id}."
    if action == "house":
        _required(
            parts,
            6,
            "/access company house reg:<id>|company:<id> <подъезды> <квартиры> <адрес>",
        )
        source = parts[2]
        if source.startswith("reg:"):
            reg_id, company_id = _uuid(source[4:]), None
        elif source.startswith("company:"):
            reg_id, company_id = None, _uuid(source[8:])
        else:
            raise UsageError("Укажите reg:<request_id> либо company:<company_id>")
        row = await company.create_house_request(
            session,
            user,
            registration_request_id=reg_id,
            company_id=company_id,
            entered_address=_tail(parts, 5),
            entrance_count=_positive(parts[3], "Количество подъездов"),
            apartment_count=_positive(parts[4], "Количество квартир"),
            free_text=None,
        )
        return f"Заявка на дом отправлена: {row.id}."
    if action == "decide":
        _required(
            parts,
            6,
            "/access company decide <id> approve|reject <название или -> | <пояснение>",
        )
        outcome = {"approve": "approved", "reject": "rejected"}.get(parts[3])
        if outcome is None:
            raise UsageError("Результат: approve или reject")
        name, note = _split_note(parts, 4)
        row, created = await company.decide_company_registration(
            session,
            user,
            request_id=_uuid(parts[2]),
            outcome=outcome,
            display_name=name,
            decision_note=note,
        )
        return f"Обращение {row.id} закрыто: {outcome}." + (
            f" УК: {created.id}." if created else ""
        )
    if action == "house_decide":
        _required(
            parts,
            5,
            "/access company house_decide <id> approve|reject <пояснение> [entrances=N] [apartments=N]",
        )
        outcome = {"approve": "approved", "reject": "rejected"}.get(parts[3])
        if outcome is None:
            raise UsageError("Результат: approve или reject")
        tail = parts[4:]
        counts: dict[str, int] = {}
        while tail and (tail[-1].startswith("entrances=") or tail[-1].startswith("apartments=")):
            name, _, value = tail.pop().partition("=")
            if name in counts:
                raise UsageError(f"Параметр {name} указан дважды")
            label = "Количество подъездов" if name == "entrances" else "Количество квартир"
            counts[name] = _positive(value, label)
        if outcome == "rejected" and counts:
            raise UsageError("Для отказа не нужно менять количество подъездов или квартир")
        row, house = await company.decide_house_request(
            session,
            user,
            request_id=_uuid(parts[2]),
            outcome=outcome,
            decision_note=_tail(tail, 0),
            proposed_address_key=None,
            entrance_count=counts.get("entrances"),
            apartment_count=counts.get("apartments"),
        )
        return f"Заявка {row.id} закрыта: {outcome}." + (
            f" Дом: {house.id}." if house else ""
        )
    if action == "house_details":
        _required(
            parts,
            5,
            "/access company house_details <house_id> <подъезды> <квартиры>",
        )
        if len(parts) != 5:
            raise UsageError("Укажите UUID дома, количество подъездов и квартир")
        house = await company.update_house_details(
            session,
            user,
            house_id=_uuid(parts[2]),
            entrance_count=_positive(parts[3], "Количество подъездов"),
            apartment_count=_positive(parts[4], "Количество квартир"),
        )
        return (
            f"Дом {house.id} обновлён: подъездов {house.entrance_count}, "
            f"квартир {house.apartment_count}."
        )
    if action == "houses":
        _required(parts, 3, "/access company houses <company_id>")
        return _list(
            await queries.list_company_houses(session, user, company_id=_uuid(parts[2]))
        )
    raise UsageError("Неизвестная команда УК. Отправьте /access help")


async def _staff(session: AsyncSession, user: User, parts: list[str]) -> str:
    _required(parts, 2, "/access staff <list|assign|revoke> ...")
    action = parts[1]
    if action == "list":
        _required(parts, 3, "/access staff list <company_id>")
        return _list(
            await queries.list_staff(session, user, company_id=_uuid(parts[2]))
        )
    if action == "assign":
        _required(
            parts, 7, "/access staff assign <company_id> <телефон> <0|1> <0|1> <0|1>"
        )
        row = await staff.assign_staff(
            session,
            user,
            company_id=_uuid(parts[2]),
            phone_number=parts[3],
            can_manage_staff=_bool(parts[4]),
            can_manage_residents=_bool(parts[5]),
            can_manage_issues=_bool(parts[6]),
        )
        return f"Назначение сотрудника сохранено: {row.id}."
    if action == "revoke":
        _required(parts, 3, "/access staff revoke <assignment_id>")
        row = await staff.revoke_staff(session, user, assignment_id=_uuid(parts[2]))
        return f"Назначение сотрудника {row.id} отозвано."
    raise UsageError("Неизвестная команда сотрудников. Отправьте /access help")


async def _requests(session: AsyncSession, user: User, parts: list[str]) -> str:
    _required(
        parts,
        2,
        "/access requests <list|show|message|status|cancel|cancel_resolve> ...",
    )
    action = parts[1]
    if action == "list":
        _required(parts, 3, "/access requests list <вид> [house_id]")
        return _list(
            await queries.list_requests(
                session,
                user,
                kind=parts[2],
                house_id=_uuid(parts[3]) if len(parts) > 3 else None,
            )
        )
    _required(parts, 4, "/access requests <действие> <вид> <request_id> ...")
    kind, request_id = parts[2], _uuid(parts[3])
    if action == "show":
        if len(parts) > 5:
            raise UsageError("Формат: /access requests show <вид> <request_id> [страница]")
        page = page_number(parts[4]) if len(parts) == 5 else 1
        return _request_view(
            await queries.get_request(session, user, kind=kind, request_id=request_id),
            actor_id=str(user.id),
            page=page,
        )
    if action == "message":
        row, message_id = await requests.add_discussion_message(
            session, user, kind=kind, request_id=request_id, text=_tail(parts, 4)
        )
        return f"Сообщение {message_id} добавлено к заявке {row.id}."
    if action == "status":
        _required(
            parts, 5, "/access requests status <вид> <id> open|reviewing|needs_info"
        )
        row = await requests.change_request_status(
            session, user, kind=kind, request_id=request_id, status=parts[4]
        )
        return f"Статус заявки {row.id}: {row.status}."
    if action == "cancel":
        row = await requests.request_cancellation(
            session, user, kind=kind, request_id=request_id
        )
        return (
            f"Заявка {row.id} отменена."
            if row.status == "cancelled"
            else f"Запрос на отмену заявки {row.id} отправлен другой стороне."
        )
    if action == "cancel_resolve":
        _required(parts, 5, "/access requests cancel_resolve <вид> <id> yes|no")
        row = await requests.resolve_cancellation(
            session, user, kind=kind, request_id=request_id, accept=_bool(parts[4])
        )
        return f"Запрос отмены рассмотрен. Статус заявки {row.id}: {row.status}."
    raise UsageError("Неизвестная команда заявок. Отправьте /access help")


async def handle_access_text(
    session: AsyncSession, user: User, text: str
) -> str | None:
    """Возвращает ответ бота или None для команд другого домена."""
    if not text or not text.strip():
        return None
    command = text.lstrip().split(maxsplit=1)[0].lower()
    if command not in {"/access", "/доступ"}:
        return None
    if len(text) > 4000:
        return "Сообщение слишком длинное; сократите его и отправьте снова."
    try:
        parts = shlex.split(text)
        if len(parts) <= 1 or parts[1] == "help":
            return HELP
        section = parts[1]
        args = parts[1:]
        if section == "houses":
            _required(args, 2, "/access houses <часть адреса>")
            return _list(await queries.search_houses(session, _tail(args, 1)))
        if section == "profile":
            if len(args) == 1:
                name = getattr(user, "full_name", None) or "не указано"
                state = (
                    "подтверждено"
                    if has_confirmed_full_name(user)
                    else "получено из MAX, требуется подтверждение"
                )
                return f"Общее ФИО: {name} ({state}). Изменить: /access profile name <ФИО>"
            if len(args) < 3 or args[1] != "name":
                raise UsageError("Формат: /access profile name <ФИО>")
            updated = await update_profile_name(session, user, full_name=_tail(args, 2))
            return f"Общее ФИО подтверждено: {updated.full_name}. Незакрытые заявки обновлены."
        if section == "resident":
            return await _resident(session, user, args)
        if section == "company":
            return await _company(session, user, args)
        if section == "staff":
            return await _staff(session, user, args)
        if section == "requests":
            return await _requests(session, user, args)
        return HELP
    except (UsageError, ValueError) as error:
        await session.rollback()
        return f"{error}\nОтправьте /access help для списка команд."
    except AccessRuleError as error:
        await session.rollback()
        return error.message
    except IntegrityError:
        await session.rollback()
        return "Не удалось сохранить изменение: данные уже изменились. Обновите список и повторите."
