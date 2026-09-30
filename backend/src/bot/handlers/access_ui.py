"""Button-driven access workflows for MAX; domain services remain authoritative."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.access_discussion import discussion_pages, page_number
from src.bot.ui import (
    Button,
    UiReply,
    clear_dialog,
    get_dialog,
    set_dialog,
    update_dialog,
)
from src.db.models import (
    Apartment,
    BotDialog,
    Company,
    House,
    ResidentGrant,
    StaffAssignment,
    User,
)
from src.domain.access import company, queries, requests, resident, staff
from src.domain.access.common import require_staff, require_support
from src.domain.access.rules import normalize_phone
from src.domain.drafts.service import get_draft, list_drafts, mark_submitted, save_draft
from src.domain.files.service import _parent as require_file_parent
from src.domain.notifications.service import is_muted, set_mute
from src.domain.profile import has_confirmed_full_name, normalize_full_name, update_profile_name

STATUS = {
    "open": "Открыта",
    "reviewing": "На рассмотрении",
    "needs_info": "Нужны уточнения",
    "closed": "Закрыта",
    "cancelled": "Отменена",
}
OUTCOME = {
    "granted": "Доступ выдан",
    "denied": "Отказано",
    "approved": "Одобрено",
    "rejected": "Отклонено",
}
ACCESS_STATE = {
    "active": "действует",
    "expired": "истёк",
    "revoked": "отозван",
    "pending": "ожидает ответа",
    "accepted": "принято",
    "declined": "отклонено",
    "cancelled": "отменено",
}
KINDS = {"resident", "company_registration", "house_addition"}


def _button(text: str, payload: str) -> list[Button]:
    return [Button(text=text, payload=payload)]


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Не удалось открыть запись. Обновите список и попробуйте снова."
        ) from error


def _positive(value: str, label: str) -> int:
    try:
        number = int(value.strip())
    except ValueError:
        number = 0
    if number < 1:
        raise ValueError(f"{label}: укажите положительное целое число.")
    return number


def _date(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            "Дата должна быть в формате 2026-12-01T18:00:00+03:00."
        ) from error
    if result.tzinfo is None:
        raise ValueError("Укажите часовой пояс, например +03:00.")
    return result


def _short(value: str, limit: int = 38) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _kind(value: str) -> str:
    if value not in KINDS:
        raise ValueError("Неизвестный вид заявки.")
    return value


def _apply_review(data: dict[str, object]) -> UiReply:
    return _next(
        f"Проверьте заявку: {data['address']}; "
        f"подъезд {data['entrance_number']}, квартира {data['apartment_number']}; "
        f"{data['full_name']}. "
        "Номер MAX подтверждает аккаунт, а проживание проверит УК.",
        [_button("Отправить заявку", "a:submit_apply")],
    )


async def _staff_companies(
    session: AsyncSession, actor: User
) -> list[tuple[UUID, str]]:
    rows = (
        await session.execute(
            select(StaffAssignment.company_id, Company.display_name)
            .join(Company, Company.id == StaffAssignment.company_id)
            .where(
                StaffAssignment.user_id == actor.id,
                StaffAssignment.revoked_at.is_(None),
            )
            .order_by(Company.display_name)
        )
    ).all()
    return [(company_id, name) for company_id, name in rows]


async def _request_list(
    session: AsyncSession,
    actor: User,
    kind: str,
    *,
    staff_only: bool = False,
    page: int = 0,
) -> UiReply:
    kind = _kind(kind)
    items = await queries.list_requests(session, actor, kind=kind)
    if staff_only:
        items = [item for item in items if item["applicant_user_id"] != str(actor.id)]
    title = {
        "resident": "Заявки жильцов" if staff_only else "Заявки на доступ",
        "company_registration": "Обращения о регистрации УК",
        "house_addition": "Заявки на подключение домов",
    }[kind]
    if not items:
        return UiReply(f"{title}: пока ничего нет.")
    page = max(0, page)
    start = page * 10
    selected = items[start : start + 10]
    if not selected:
        base_payload = "a:staff_requests" if staff_only else f"a:requests:{kind}"
        return UiReply(
            "На этой странице больше нет заявок.", [_button("К началу", base_payload)]
        )
    lines = [f"{title} (записи {start + 1}–{start + len(selected)} из {len(items)}):"]
    buttons: list[list[Button]] = []
    for item in selected:
        name = (
            item.get("address_display")
            or item.get("proposed_company_name")
            or item.get("entered_address")
            or item.get("submitted_full_name")
            or "Заявка"
        )
        label = (
            f"{_short(str(name), 31)} · {STATUS.get(item['status'], item['status'])}"
        )
        buttons.append(_button(label, f"a:request:{kind}:{item['id']}"))
        lines.append(f"• {name}: {STATUS.get(item['status'], item['status'])}")
    if page > 0 or start + len(selected) < len(items):
        base_payload = "a:staff_requests" if staff_only else f"a:requests:{kind}"
        navigation = []
        if page > 0:
            navigation.append(Button("Назад", f"{base_payload}:{page - 1}"))
        if start + len(selected) < len(items):
            navigation.append(Button("Дальше", f"{base_payload}:{page + 1}"))
        buttons.append(navigation)
    return UiReply("\n".join(lines)[:3300], buttons)


async def _request_detail(
    session: AsyncSession, actor: User, kind: str, request_id: UUID
) -> UiReply:
    kind = _kind(kind)
    item = await queries.get_request(session, actor, kind=kind, request_id=request_id)
    applicant = item["applicant_user_id"] == str(actor.id)
    active = item["status"] not in {"closed", "cancelled"}
    can_write = applicant or actor.kind == "support"
    if kind == "resident" and actor.kind == "employee" and not applicant:
        house = await session.get(House, _uuid(item["house_id"]))
        if house is not None:
            can_write = (
                await require_staff(session, actor, house.company_id)
            ).can_manage_residents
    lines = [f"Заявка: {STATUS.get(item['status'], item['status'])}"]
    if kind == "resident":
        lines.extend(
            [
                f"Адрес: {item.get('address_display') or item['house_id']}",
                f"ФИО: {item['submitted_full_name']}",
                f"Подъезд: {item.get('submitted_entrance_number') or 'не указан'}",
                f"Квартира {item['submitted_apartment_number']}",
            ]
        )
    elif kind == "company_registration":
        lines.extend(
            [
                f"УК: {item.get('proposed_company_name') or 'не указана'}",
                f"Номер первого сотрудника: {item['phone_number']}",
                f"Описание: {item['free_text']}",
            ]
        )
    else:
        lines.extend(
            [
                f"Адрес: {item['entered_address']}",
                f"Подъездов: {item.get('entrance_count') or 'не указано'}",
                f"Квартир: {item.get('apartment_count') or 'не указано'}",
                f"Описание: {item.get('free_text') or 'нет'}",
            ]
        )
    if item.get("outcome"):
        lines.append(f"Результат: {OUTCOME.get(item['outcome'], item['outcome'])}")
    if item.get("decision_note"):
        lines.append(f"Пояснение: {item['decision_note']}")
    discussion = item.get("discussion") or []
    lines.append(f"Обсуждение: {len(discussion)} сообщений.")
    buttons = []
    buttons.append(
        _button("Читать обсуждение", f"a:req_discussion:{kind}:{request_id}:1")
    )
    if kind == "resident":
        buttons.append(_button("Вложения", f"f:access:{request_id}"))
        if active and can_write:
            buttons.append(_button("Прикрепить файл", f"a:req_file:{request_id}"))
    if (
        kind == "house_addition"
        and item.get("resolved_house_id")
        and actor.kind in {"support", "admin"}
    ):
        buttons.append(
            _button(
                "Исправить параметры дома",
                f"a:house_details:{item['resolved_house_id']}",
            )
        )
    if item["status"] != "cancelled" and can_write:
        buttons.append(
            _button("Написать в обсуждение", f"a:req_message:{kind}:{request_id}")
        )
    if active:
        if kind == "resident" and applicant:
            buttons.append(_button("Исправить данные", f"a:req_edit:{request_id}"))
        if item.get("cancel_requested_by"):
            if item["cancel_requested_by"] == str(actor.id):
                lines.append("Ваш запрос отмены ждёт ответа другой стороны.")
            elif can_write:
                lines.append("Другая сторона просит отменить заявку.")
                buttons.append(
                    [
                        Button(
                            "Подтвердить отмену",
                            f"a:req_cancel_resolve:{kind}:{request_id}:yes",
                        ),
                        Button(
                            "Оставить заявку",
                            f"a:req_cancel_resolve:{kind}:{request_id}:no",
                        ),
                    ]
                )
        elif can_write:
            buttons.append(
                _button("Запросить отмену", f"a:req_cancel:{kind}:{request_id}")
            )
        if (
            kind == "resident"
            and not applicant
            and actor.kind == "employee"
            and can_write
        ):
            buttons.extend(
                [
                    [
                        Button("Открыта", f"a:req_status:{kind}:{request_id}:open"),
                        Button(
                            "На рассмотрении",
                            f"a:req_status:{kind}:{request_id}:reviewing",
                        ),
                    ],
                    _button(
                        "Нужны уточнения",
                        f"a:req_status:{kind}:{request_id}:needs_info",
                    ),
                    [
                        Button(
                            "Выдать доступ", f"a:decision:{kind}:{request_id}:granted"
                        ),
                        Button("Отказать", f"a:decision:{kind}:{request_id}:denied"),
                    ],
                ]
            )
        if actor.kind == "support" and kind != "resident":
            buttons.extend(
                [
                    [
                        Button("Открыта", f"a:req_status:{kind}:{request_id}:open"),
                        Button(
                            "На рассмотрении",
                            f"a:req_status:{kind}:{request_id}:reviewing",
                        ),
                    ],
                    _button(
                        "Нужны уточнения",
                        f"a:req_status:{kind}:{request_id}:needs_info",
                    ),
                    [
                        Button("Одобрить", f"a:decision:{kind}:{request_id}:approved"),
                        Button("Отклонить", f"a:decision:{kind}:{request_id}:rejected"),
                    ],
                ]
            )
    if kind == "resident":
        buttons.append(_button("Уведомления по заявке", f"a:req_notify:{request_id}"))
    buttons.append(_button("К списку заявок", f"a:requests:{kind}"))
    return UiReply("\n".join(lines)[:3700], buttons)


async def _request_discussion(
    session: AsyncSession,
    actor: User,
    kind: str,
    request_id: UUID,
    page: int,
) -> UiReply:
    kind = _kind(kind)
    item = await queries.get_request(session, actor, kind=kind, request_id=request_id)
    messages = item.get("discussion") or []
    pages = discussion_pages(
        messages,
        actor_id=str(actor.id),
        applicant_id=item["applicant_user_id"],
        max_chars=3000,
    )
    back = _button("К заявке", f"a:request:{kind}:{request_id}")
    if not pages:
        return UiReply("В обсуждении пока нет сообщений.", [back])
    if page > len(pages):
        return UiReply(
            f"Страницы {page} нет. Всего страниц: {len(pages)}.",
            [
                _button("К началу обсуждения", f"a:req_discussion:{kind}:{request_id}:1"),
                back,
            ],
        )
    buttons: list[list[Button]] = []
    navigation: list[Button] = []
    if page > 1:
        navigation.append(Button("Назад", f"a:req_discussion:{kind}:{request_id}:{page - 1}"))
    if page < len(pages):
        navigation.append(Button("Дальше", f"a:req_discussion:{kind}:{request_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(back)
    return UiReply(
        f"Обсуждение заявки · страница {page}/{len(pages)} · сообщений {len(messages)}:\n\n"
        + pages[page - 1],
        buttons,
    )


async def _save_apply_draft(
    session: AsyncSession, actor: User, dialog: BotDialog, data: dict[str, object]
) -> None:
    payload = {
        key: data[key]
        for key in ("house_id", "full_name", "name_from_profile", "entrance_number", "apartment_number")
        if key in data
    }
    if dialog.draft_id is None:
        draft = await save_draft(
            session, actor, flow_kind="resident_request", payload=payload
        )
        dialog.draft_id = draft.id
        data["draft_revision"] = draft.revision
    else:
        draft = await save_draft(
            session,
            actor,
            flow_kind="resident_request",
            payload=payload,
            draft_id=dialog.draft_id,
            revision=int(data["draft_revision"]),
        )
        data["draft_revision"] = draft.revision
    await update_dialog(dialog, data=data)


async def _start_apply(session: AsyncSession, actor: User, *, fresh: bool) -> UiReply:
    draft = None
    if not fresh:
        draft = next(
            (
                item
                for item in await list_drafts(
                    session, actor, flow_kind="resident_request"
                )
                if item.submitted_at is None
            ),
            None,
        )
    data = dict(draft.payload) if draft else {}
    if has_confirmed_full_name(actor):
        data["full_name"] = actor.full_name
        data["name_from_profile"] = True
    elif data.get("name_from_profile"):
        data.pop("full_name", None)
        data["name_from_profile"] = False
    if draft:
        data["draft_revision"] = draft.revision
    await set_dialog(
        session,
        actor.id,
        flow_kind="access_apply",
        step="search",
        data=data,
        draft_id=draft.id if draft else None,
    )
    return UiReply(
        "Введите улицу и номер дома. Я покажу подключённые дома, затем попрошу подтвердить адрес."
        + (
            " Ваш незавершённый черновик найден; адрес выберем заново." if draft else ""
        ),
        [_button("Начать заново", "a:apply:new")] if draft else [],
    )


async def _start_company_registration(session: AsyncSession, actor: User) -> UiReply:
    await set_dialog(
        session, actor.id, flow_kind="access_company", step="name", data={}
    )
    return UiReply(
        "Как называется управляющая компания? Напишите её название.",
        [_button("Мои обращения УК", "a:requests:company_registration")],
    )


async def _start_house_request(
    session: AsyncSession, actor: User, *, source: str, source_id: UUID
) -> UiReply:
    if source == "reg":
        item = await queries.get_request(
            session, actor, kind="company_registration", request_id=source_id
        )
        if item["applicant_user_id"] != str(actor.id) or item["status"] in {
            "closed",
            "cancelled",
        }:
            raise ValueError("К этой регистрации больше нельзя добавить дом.")
    elif source == "company":
        await require_staff(session, actor, source_id, "can_manage_staff")
    else:
        raise ValueError("Неизвестный источник заявки на дом.")
    await set_dialog(
        session,
        actor.id,
        flow_kind="access_house",
        step="address",
        data={
            "source": source,
            "source_id": str(source_id),
        },
    )
    return UiReply("Напишите полный адрес дома: город, улица и номер.")


async def _start_house_details(
    session: AsyncSession, actor: User, house_id: UUID | None = None
) -> UiReply:
    require_support(actor)
    if house_id is None:
        await set_dialog(
            session, actor.id, flow_kind="access_house_details", step="house", data={}
        )
        return UiReply("Напишите UUID подключённого дома, параметры которого нужно исправить.")
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise ValueError("Подключённый дом не найден.")
    await set_dialog(
        session,
        actor.id,
        flow_kind="access_house_details",
        step="entrance_count",
        data={
            "house_id": str(house.id),
            "address": house.address_display,
            "old_entrance_count": house.entrance_count,
            "old_apartment_count": house.apartment_count,
        },
    )
    return UiReply(
        f"Дом «{house.address_display}»: сейчас подъездов {house.entrance_count}, "
        f"квартир {house.apartment_count}. Напишите новое количество подъездов."
    )


async def _offer_list(session: AsyncSession, actor: User, *, page: int = 0) -> UiReply:
    offers = await queries.list_offers(session, actor)
    if not offers:
        return UiReply("Предложений доступа пока нет.")
    page = max(0, page)
    start = page * 10
    selected = offers[start : start + 10]
    if not selected:
        return UiReply(
            "На этой странице больше нет предложений.",
            [_button("К началу", "a:offers")],
        )
    buttons = []
    lines = [
        f"Предложения доступа ({start + 1}–{start + len(selected)} из {len(offers)}):"
    ]
    for item in selected:
        label = f"{item['address_display']}, кв. {item['apartment_number']}"
        lines.append(f"• {label} — {ACCESS_STATE.get(item['status'], item['status'])}")
        if item["status"] == "pending":
            buttons.append(_button(_short(label), f"a:offer:{item['id']}"))
    if page > 0 or start + len(selected) < len(offers):
        navigation = []
        if page > 0:
            navigation.append(Button("Назад", f"a:offers:{page - 1}"))
        if start + len(selected) < len(offers):
            navigation.append(Button("Дальше", f"a:offers:{page + 1}"))
        buttons.append(navigation)
    return UiReply("\n".join(lines)[:3000], buttons)


async def _offer_detail(session: AsyncSession, actor: User, offer_id: UUID) -> UiReply:
    offers = await queries.list_offers(session, actor)
    offer = next((item for item in offers if item["id"] == str(offer_id)), None)
    if offer is None:
        raise ValueError(
            "Предложение не найдено. Возможно, оно адресовано другому номеру."
        )
    text = f"УК предлагает доступ: {offer['address_display']}, квартира {offer['apartment_number']}."
    if offer["proposed_access_until"]:
        text += f" Действует до {offer['proposed_access_until']}."
    if offer["status"] != "pending":
        return UiReply(
            text + f" Статус: {ACCESS_STATE.get(offer['status'], offer['status'])}."
        )
    text += " Доступ появится только после вашего согласия."
    return UiReply(
        text,
        [
            [
                Button("Принять", f"a:offer_answer:{offer_id}:yes"),
                Button("Отклонить", f"a:offer_answer:{offer_id}:no"),
            ]
        ],
    )


async def _staff_houses(session: AsyncSession, actor: User) -> UiReply:
    companies = await _staff_companies(session, actor)
    if not companies:
        return UiReply("У вас нет действующего назначения сотрудника УК.")
    lines = ["Дома ваших управляющих компаний:"]
    buttons = []
    for company_id, name in companies:
        assignment = await require_staff(session, actor, company_id)
        houses = await queries.list_company_houses(
            session, actor, company_id=company_id
        )
        lines.append(f"{name}: {len(houses)} дом(ов)")
        for house in houses[:8]:
            lines.append(f"• {house['address_display']}")
            buttons.append(
                _button(_short(house["address_display"]), f"i:house:{house['id']}")
            )
        if assignment.can_manage_staff:
            buttons.append(
                _button(
                    f"Заявить новый дом · {_short(name, 20)}",
                    f"a:house_new:company:{company_id}",
                )
            )
        buttons.append(
            _button(f"Жильцы · {_short(name, 25)}", f"a:people:{company_id}")
        )
        buttons.append(
            _button(
                f"Предложения · {_short(name, 25)}", f"a:company_offers:{company_id}"
            )
        )
    buttons.append(_button("Все доступные дома и проблемы", "i:houses"))
    buttons.append(_button("Мои заявки на дома", "a:requests:house_addition"))
    return UiReply("\n".join(lines)[:3400], buttons)


async def _staff_menu(session: AsyncSession, actor: User) -> UiReply:
    companies = await _staff_companies(session, actor)
    if not companies:
        return UiReply("У вас нет действующего назначения сотрудника УК.")
    return UiReply(
        "Выберите УК для просмотра и изменения сотрудников:",
        [
            _button(_short(name), f"a:staff_company:{company_id}")
            for company_id, name in companies
        ],
    )


async def _staff_company(
    session: AsyncSession, actor: User, company_id: UUID, *, page: int = 0
) -> UiReply:
    rows = [
        item
        for item in await queries.list_staff(session, actor, company_id=company_id)
        if not item["revoked_at"]
    ]
    page = max(0, page)
    start = page * 10
    selected = rows[start : start + 10]
    if page and not selected:
        return UiReply(
            "На этой странице больше нет сотрудников.",
            [_button("К началу", f"a:staff_company:{company_id}")],
        )
    lines = [f"Сотрудники УК ({len(rows)}):"]
    buttons = []
    for item in selected:
        rights = (
            ", ".join(
                label
                for key, label in (
                    ("can_manage_staff", "сотрудники"),
                    ("can_manage_residents", "жильцы"),
                    ("can_manage_issues", "проблемы"),
                )
                if item[key]
            )
            or "только просмотр"
        )
        lines.append(f"• {item['phone_number']}: {rights}")
        buttons.append(_button(item["phone_number"], f"a:staff_detail:{item['id']}"))
    if page > 0 or start + len(selected) < len(rows):
        navigation = []
        if page > 0:
            navigation.append(
                Button("Назад", f"a:staff_company:{company_id}:{page - 1}")
            )
        if start + len(selected) < len(rows):
            navigation.append(
                Button("Дальше", f"a:staff_company:{company_id}:{page + 1}")
            )
        buttons.append(navigation)
    buttons.append(_button("Добавить сотрудника", f"a:staff_new:{company_id}"))
    return UiReply("\n".join(lines)[:3300], buttons)


async def _staff_detail(
    session: AsyncSession, actor: User, assignment_id: UUID
) -> UiReply:
    assignment = await session.get(StaffAssignment, assignment_id)
    if assignment is None or assignment.revoked_at is not None:
        raise ValueError("Назначение сотрудника не найдено.")
    rows = await queries.list_staff(session, actor, company_id=assignment.company_id)
    if not any(str(item["id"]) == str(assignment_id) for item in rows):
        raise ValueError("Назначение сотрудника не найдено.")
    rights = (
        ", ".join(
            label
            for enabled, label in (
                (assignment.can_manage_staff, "сотрудники"),
                (assignment.can_manage_residents, "жильцы"),
                (assignment.can_manage_issues, "проблемы"),
            )
            if enabled
        )
        or "только просмотр"
    )
    return UiReply(
        f"Сотрудник {assignment.phone_number}. Права: {rights}.",
        [
            _button("Изменить права", f"a:staff_edit:{assignment_id}"),
            _button("Отозвать назначение", f"a:staff_revoke:{assignment_id}"),
        ],
    )


async def _staff_rights(dialog: BotDialog) -> UiReply:
    data = dict(dialog.data)
    rights = data.get("rights") or {}
    text = (
        "Права для "
        + str(data.get("phone", "сотрудника"))
        + ":\n"
        + "\n".join(
            ("☑ " if rights.get(key) else "☐ ") + label
            for key, label in (
                ("can_manage_staff", "управлять сотрудниками"),
                ("can_manage_residents", "управлять жильцами"),
                ("can_manage_issues", "вести проблемы"),
            )
        )
    )
    return UiReply(
        text,
        [
            _button("Сотрудники", "a:staff_right:can_manage_staff"),
            _button("Жильцы", "a:staff_right:can_manage_residents"),
            _button("Проблемы", "a:staff_right:can_manage_issues"),
            _button("Сохранить назначение", "a:staff_save"),
        ],
    )


async def _dialog(
    session: AsyncSession, actor: User, flow_kind: str, step: str | None = None
) -> BotDialog:
    dialog = await get_dialog(session, actor.id)
    if (
        dialog is None
        or dialog.flow_kind != flow_kind
        or (step is not None and dialog.step != step)
    ):
        raise ValueError(
            "Этот шаг уже недействителен. Начните действие заново через меню."
        )
    return dialog


async def handle_action(
    session: AsyncSession, actor: User, payload: str
) -> UiReply | None:
    """Handle one access button. Business checks are delegated to domain services."""
    if not payload.startswith("a:"):
        return None
    parts = payload.split(":")
    action = parts[1]

    if action == "profile":
        name = getattr(actor, "full_name", None) or "не указано"
        state = (
            "подтверждено"
            if has_confirmed_full_name(actor)
            else "получено из MAX и ещё не подтверждено"
        )
        await set_dialog(
            session, actor.id, flow_kind="access_profile", step="name", data={}
        )
        return UiReply(
            f"Общее ФИО: {name} ({state}). Напишите ФИО, которое должно быть во всех новых заявках. "
            "Незакрытые заявки тоже обновятся; закрытые сохранят прежнее ФИО."
        )
    if action == "profile_save":
        dialog = await _dialog(session, actor, "access_profile", "review")
        user = await update_profile_name(
            session, actor, full_name=str(dialog.data["full_name"])
        )
        await clear_dialog(session, actor.id)
        return UiReply(f"Общее ФИО подтверждено: {user.full_name}. Незакрытые заявки обновлены.")
    if action == "apply":
        return await _start_apply(
            session, actor, fresh=len(parts) > 2 and parts[2] == "new"
        )
    if action == "resume" and len(parts) == 3:
        draft = await get_draft(session, actor, _uuid(parts[2]))
        if draft.flow_kind != "resident_request" or draft.submitted_at is not None:
            raise ValueError("Нужен неотправленный черновик заявки на доступ.")
        data = {**draft.payload, "draft_revision": draft.revision}
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_apply",
            step="search",
            data=data,
            draft_id=draft.id,
        )
        return UiReply(
            "Продолжаем черновик. Найдите и подтвердите адрес дома заново: напишите улицу и номер дома."
        )
    if action == "back":
        dialog = await get_dialog(session, actor.id)
        if dialog is None:
            return UiReply("Незавершённого действия нет. Откройте главное меню.")
        if dialog.flow_kind == "access_staff" and dialog.data.get(
            "editing_assignment_id"
        ):
            assignment_id = _uuid(str(dialog.data["editing_assignment_id"]))
            await clear_dialog(session, actor.id)
            return await _staff_detail(session, actor, assignment_id)
        previous = (
            {
                "access_apply": {
                    "confirm": "search",
                    "name": "search",
                    "entrance": "name",
                    "apartment": "entrance",
                    "review": "apartment",
                },
                "access_edit": {
                    "entrance": None,
                    "apartment": "entrance",
                    "review": "apartment",
                },
                "access_profile": {"review": "name"},
                "access_company": {"phone": "name", "text": "phone", "review": "text"},
                "access_house": {
                    "entrance_count": "address",
                    "apartment_count": "entrance_count",
                    "note": "apartment_count",
                    "review": "note",
                },
                "access_house_details": {
                    "entrance_count": "house",
                    "apartment_count": "entrance_count",
                    "review": "apartment_count",
                },
                "access_decision": {
                    "note": "company_name",
                    "expiry": "note",
                    "entrance_count": "note",
                    "apartment_count": "entrance_count",
                    "review": "note",
                },
                "access_staff": {"rights": "phone"},
                "access_offer_new": {
                    "phone": "house",
                    "entrance": "phone",
                    "apartment": "entrance",
                    "review": "apartment",
                },
            }
            .get(dialog.flow_kind, {})
            .get(dialog.step)
        )
        if (
            dialog.flow_kind == "access_decision"
            and dialog.step == "note"
            and (
                dialog.data.get("kind") != "company_registration"
                or dialog.data.get("outcome") != "approved"
            )
        ):
            previous = None
        if (
            dialog.flow_kind == "access_decision"
            and dialog.step == "review"
            and dialog.data.get("kind") == "house_addition"
            and dialog.data.get("outcome") == "approved"
        ):
            previous = "apartment_count"
        if previous is None:
            await clear_dialog(session, actor.id)
            return UiReply("Действие остановлено. Откройте главное меню.")
        await update_dialog(dialog, step=previous)
        prompts = {
            "search": "Напишите улицу и номер подключённого дома.",
            "name": "Напишите ФИО или название УК заново.",
            "phone": "Напишите номер телефона заново.",
            "entrance": "Напишите номер подъезда заново.",
            "apartment": "Напишите номер квартиры заново.",
            "entrance_count": "Напишите количество подъездов заново.",
            "apartment_count": "Напишите количество квартир заново.",
            "text": "Напишите пояснение заново.",
            "note": "Напишите пояснение заново.",
            "address": "Напишите адрес дома заново.",
            "company_name": "Напишите утверждаемое название УК.",
            "house": "Выберите дом заново.",
        }
        return UiReply(
            prompts.get(previous, "Введите исправленное значение."),
            [_button("Ещё назад", "a:back")],
        )
    if action == "house" and len(parts) == 3:
        dialog = await _dialog(session, actor, "access_apply", "search")
        house = await session.get(House, _uuid(parts[2]))
        if house is None or house.archived_at is not None:
            raise ValueError("Дом больше не подключён. Поищите адрес заново.")
        data = dict(dialog.data)
        if data.get("house_id") != str(house.id):
            data.pop("entrance_number", None)
            data.pop("apartment_number", None)
        data.update(house_id=str(house.id), address=house.address_display)
        await update_dialog(dialog, step="confirm", data=data)
        return UiReply(
            f"Вы выбрали: {house.address_display}. Это ваш дом?",
            [
                [
                    Button("Да, адрес верный", "a:confirm_house"),
                    Button("Искать снова", "a:apply"),
                ]
            ],
        )
    if action == "confirm_house":
        dialog = await _dialog(session, actor, "access_apply", "confirm")
        data = dict(dialog.data)
        if has_confirmed_full_name(actor):
            data["full_name"] = actor.full_name
            data["name_from_profile"] = True
        elif data.get("name_from_profile"):
            data.pop("full_name", None)
            data["name_from_profile"] = False
        await _save_apply_draft(session, actor, dialog, data)
        if all(data.get(key) for key in ("full_name", "entrance_number", "apartment_number")):
            await update_dialog(dialog, step="review")
            return _apply_review(data)
        if not data.get("full_name"):
            await update_dialog(dialog, step="name")
            return UiReply("Введите ваше ФИО один раз для общего профиля. Его увидит УК.")
        if not data.get("entrance_number"):
            await update_dialog(dialog, step="entrance")
            return UiReply(f"ФИО из черновика: {data['full_name']}. Укажите номер подъезда.")
        await update_dialog(dialog, step="apartment")
        return UiReply(f"Подъезд {data['entrance_number']}. Укажите номер квартиры.")
    if action == "submit_apply":
        dialog = await _dialog(session, actor, "access_apply", "review")
        data = dict(dialog.data)
        if not has_confirmed_full_name(actor) and data.get("name_from_profile"):
            data.pop("full_name", None)
            data["name_from_profile"] = False
            await _save_apply_draft(session, actor, dialog, data)
            await update_dialog(dialog, step="name")
            return UiReply("ФИО в профиле больше не подтверждено. Введите ваше ФИО заново.")
        if has_confirmed_full_name(actor) and data.get("full_name") != actor.full_name:
            data["full_name"] = actor.full_name
            data["name_from_profile"] = True
            await _save_apply_draft(session, actor, dialog, data)
            return _apply_review(data)
        if dialog.draft_id is not None:
            await mark_submitted(
                session,
                actor,
                draft_id=dialog.draft_id,
                revision=int(data["draft_revision"]),
            )
        row = await resident.create_resident_request(
            session,
            actor,
            house_id=_uuid(str(data["house_id"])),
            full_name=(
                str(data["full_name"])
                if not has_confirmed_full_name(actor)
                else None
            ),
            entrance_number=int(data["entrance_number"]),
            apartment_number=int(data["apartment_number"]),
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Заявка отправлена в УК. Пока она не одобрена, проблемы дома недоступны.",
            [_button("Открыть заявку", f"a:request:resident:{row.id}")],
        )

    if action == "requests" and len(parts) in {3, 4}:
        await clear_dialog(session, actor.id)
        page = int(parts[3]) if len(parts) == 4 else 0
        return await _request_list(session, actor, parts[2], page=page)
    if action == "staff_requests":
        await clear_dialog(session, actor.id)
        if actor.kind != "employee":
            raise ValueError("Этот раздел доступен сотруднику УК.")
        page = int(parts[2]) if len(parts) == 3 else 0
        return await _request_list(
            session, actor, "resident", staff_only=True, page=page
        )
    if action == "request" and len(parts) == 4:
        await clear_dialog(session, actor.id)
        return await _request_detail(session, actor, parts[2], _uuid(parts[3]))
    if action == "req_discussion" and len(parts) == 5:
        return await _request_discussion(
            session, actor, parts[2], _uuid(parts[3]), page_number(parts[4])
        )
    if action == "req_message" and len(parts) == 4:
        kind, request_id = _kind(parts[2]), _uuid(parts[3])
        item = await queries.get_request(
            session, actor, kind=kind, request_id=request_id
        )
        if item["status"] == "cancelled":
            raise ValueError("Отменённую заявку нельзя обсуждать.")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_message",
            step="text",
            data={"kind": kind, "request_id": str(request_id)},
        )
        return UiReply("Напишите сообщение для обсуждения заявки одним сообщением.")
    if action == "req_file" and len(parts) == 3:
        request_id = _uuid(parts[2])
        await require_file_parent(
            session, actor, "resident", request_id, writing=True
        )
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_file",
            step="upload",
            data={"request_id": str(request_id)},
        )
        return UiReply(
            "Отправьте одним сообщением фото, PDF или видео без подписи (до 8 МиБ). "
            "Пояснение можно написать отдельно в обсуждении заявки."
        )
    if action == "req_edit" and len(parts) == 3:
        request_id = _uuid(parts[2])
        item = await queries.get_request(
            session, actor, kind="resident", request_id=request_id
        )
        if item["applicant_user_id"] != str(actor.id) or item["status"] in {
            "closed",
            "cancelled",
        }:
            raise ValueError("Можно исправить только свою незакрытую заявку.")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_edit",
            step="entrance",
            data={"request_id": str(request_id)},
        )
        return UiReply(
            f"ФИО в заявке: {item['submitted_full_name']}. Чтобы изменить общее ФИО, "
            "используйте «Моё ФИО» в меню. Напишите новый номер подъезда."
        )
    if action == "submit_edit":
        dialog = await _dialog(session, actor, "access_edit", "review")
        data = dialog.data
        row = await resident.update_resident_request(
            session,
            actor,
            request_id=_uuid(str(data["request_id"])),
            full_name=None,
            entrance_number=int(data["entrance_number"]),
            apartment_number=int(data["apartment_number"]),
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Данные заявки исправлены.",
            [_button("Открыть заявку", f"a:request:resident:{row.id}")],
        )
    if action == "req_status" and len(parts) == 5:
        kind, request_id, status = _kind(parts[2]), _uuid(parts[3]), parts[4]
        if status not in {"open", "reviewing", "needs_info"}:
            raise ValueError("Неизвестный рабочий статус.")
        await requests.change_request_status(
            session, actor, kind=kind, request_id=request_id, status=status
        )
        return await _request_detail(session, actor, kind, request_id)
    if action == "req_notify" and len(parts) == 3:
        request_id = _uuid(parts[2])
        await queries.get_request(
            session, actor, kind="resident", request_id=request_id
        )
        muted = await is_muted(session, actor.id, "resident_request", request_id)
        return UiReply(
            (
                "Личные сообщения бота по этой заявке отключены."
                if muted
                else "Личные сообщения бота по этой заявке включены."
            ),
            [
                _button(
                    "Включить сообщения" if muted else "Отключить сообщения",
                    f"a:req_mute:{request_id}:{'off' if muted else 'on'}",
                ),
                _button("Открыть заявку", f"a:request:resident:{request_id}"),
            ],
        )
    if action == "req_mute" and len(parts) == 4:
        request_id = _uuid(parts[2])
        if parts[3] not in {"on", "off"}:
            raise ValueError("Неизвестная настройка уведомлений.")
        await set_mute(
            session,
            actor,
            subject_kind="resident_request",
            subject_id=request_id,
            is_muted=parts[3] == "on",
        )
        return await handle_action(session, actor, f"a:req_notify:{request_id}")
    if action == "req_cancel" and len(parts) == 4:
        kind, request_id = _kind(parts[2]), _uuid(parts[3])
        await queries.get_request(session, actor, kind=kind, request_id=request_id)
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_cancel",
            step="confirm",
            data={"kind": kind, "request_id": str(request_id)},
        )
        return UiReply(
            "Подтвердите запрос отмены. Для заявки жильца со статусом «Открыта» отмена произойдёт сразу; в остальных случаях ответит другая сторона.",
            [
                [
                    Button("Подтвердить", "a:req_cancel_yes"),
                    Button("Не отменять", f"a:request:{kind}:{request_id}"),
                ]
            ],
        )
    if action == "req_cancel_yes":
        dialog = await _dialog(session, actor, "access_cancel", "confirm")
        kind, request_id = str(dialog.data["kind"]), _uuid(
            str(dialog.data["request_id"])
        )
        await requests.request_cancellation(
            session, actor, kind=kind, request_id=request_id
        )
        await clear_dialog(session, actor.id)
        return await _request_detail(session, actor, kind, request_id)
    if action == "req_cancel_resolve" and len(parts) == 5:
        kind, request_id = _kind(parts[2]), _uuid(parts[3])
        if parts[4] not in {"yes", "no"}:
            raise ValueError("Выберите ответ на запрос отмены.")
        await requests.resolve_cancellation(
            session, actor, kind=kind, request_id=request_id, accept=parts[4] == "yes"
        )
        return await _request_detail(session, actor, kind, request_id)
    if action == "decision" and len(parts) == 5:
        kind, request_id, outcome = _kind(parts[2]), _uuid(parts[3]), parts[4]
        allowed = {
            "resident": {"granted", "denied"},
            "company_registration": {"approved", "rejected"},
            "house_addition": {"approved", "rejected"},
        }
        if outcome not in allowed[kind]:
            raise ValueError("Неизвестный результат решения.")
        item = await queries.get_request(
            session, actor, kind=kind, request_id=request_id
        )
        if item["status"] in {"closed", "cancelled"}:
            raise ValueError("Эта заявка уже закрыта.")
        if kind == "resident" and item["applicant_user_id"] == str(actor.id):
            raise ValueError("Нельзя принять решение по своей заявке.")
        if kind != "resident":
            require_support(actor)
        step = (
            "company_name"
            if kind == "company_registration" and outcome == "approved"
            else "note"
        )
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_decision",
            step=step,
            data={
                "kind": kind,
                "request_id": str(request_id),
                "outcome": outcome,
                "proposed_entrance_count": item.get("entrance_count"),
                "proposed_apartment_count": item.get("apartment_count"),
            },
        )
        if step == "company_name":
            return UiReply(
                f"Какое название УК утвердить? В обращении: {item.get('proposed_company_name') or 'не указано'}."
            )
        return UiReply("Напишите обязательное пояснение решения. Его увидит заявитель.")
    if action == "decision_submit":
        dialog = await _dialog(session, actor, "access_decision", "review")
        data = dialog.data
        kind, request_id, outcome = (
            str(data["kind"]),
            _uuid(str(data["request_id"])),
            str(data["outcome"]),
        )
        note = str(data["note"])
        if kind == "resident":
            await resident.decide_resident_request(
                session,
                actor,
                request_id=request_id,
                outcome=outcome,
                decision_note=note,
                valid_to=_date(str(data["valid_to"])) if data.get("valid_to") else None,
            )
        elif kind == "company_registration":
            await company.decide_company_registration(
                session,
                actor,
                request_id=request_id,
                outcome=outcome,
                decision_note=note,
                display_name=str(data.get("company_name") or "") or None,
            )
        else:
            await company.decide_house_request(
                session,
                actor,
                request_id=request_id,
                outcome=outcome,
                decision_note=note,
                proposed_address_key=None,
                entrance_count=(
                    int(data["entrance_count"]) if data.get("entrance_count") else None
                ),
                apartment_count=(
                    int(data["apartment_count"]) if data.get("apartment_count") else None
                ),
            )
        await clear_dialog(session, actor.id)
        return await _request_detail(session, actor, kind, request_id)
    if action == "decision_no_expiry":
        dialog = await _dialog(session, actor, "access_decision", "expiry")
        await update_dialog(
            dialog, step="review", data={**dialog.data, "valid_to": None}
        )
        return UiReply(
            "Выдать бессрочный доступ?",
            [_button("Подтвердить выдачу", "a:decision_submit")],
        )
    if action in {"decision_no_count", "decision_keep_entrances"}:
        dialog = await _dialog(session, actor, "access_decision", "entrance_count")
        await update_dialog(
            dialog, step="apartment_count", data={**dialog.data, "entrance_count": None}
        )
        return UiReply(
            f"В заявке указано квартир: {dialog.data.get('proposed_apartment_count') or 'не указано'}. "
            "Если нужно исправить число, напишите новое положительное количество.",
            [_button("Оставить как в заявке", "a:decision_keep_apartments")],
        )
    if action == "decision_keep_apartments":
        dialog = await _dialog(session, actor, "access_decision", "apartment_count")
        await update_dialog(
            dialog, step="review", data={**dialog.data, "apartment_count": None}
        )
        return UiReply(
            "Подтвердить подключение дома с указанными в заявке или исправленными числами?",
            [_button("Подтвердить", "a:decision_submit")],
        )

    if action == "offers":
        await clear_dialog(session, actor.id)
        page = int(parts[2]) if len(parts) == 3 else 0
        return await _offer_list(session, actor, page=page)
    if action == "grants":
        await clear_dialog(session, actor.id)
        grants = await queries.list_grants(session, actor)
        if not grants:
            return UiReply(
                "Подключённых домов пока нет.",
                [_button("Подать заявку на доступ", "a:apply")],
            )
        lines = ["Мой доступ к домам:"]
        buttons = []
        for item in grants[:15]:
            lines.append(
                f"• {item['address_display']}, "
                f"кв. {item['apartment_number']} — {ACCESS_STATE.get(item['status'], item['status'])}"
                + (f" (до {item['valid_to']})" if item["valid_to"] else "")
            )
            if item["status"] == "active":
                buttons.append(
                    _button(
                        _short(item["address_display"]), f"i:house:{item['house_id']}"
                    )
                )
        buttons.append(_button("Подать заявку на другой дом", "a:apply"))
        return UiReply("\n".join(lines)[:3400], buttons)
    if action == "offer" and len(parts) == 3:
        return await _offer_detail(session, actor, _uuid(parts[2]))
    if action == "offer_answer" and len(parts) == 4:
        if parts[3] not in {"yes", "no"}:
            raise ValueError("Выберите, принять или отклонить предложение.")
        offer, _ = await resident.respond_resident_offer(
            session, actor, offer_id=_uuid(parts[2]), accept=parts[3] == "yes"
        )
        return UiReply(
            (
                "Предложение принято: доступ к квартире выдан."
                if offer.status == "accepted"
                else "Предложение отклонено."
            ),
            [_button("Мои предложения", "a:offers")],
        )

    if action == "register_company":
        return await _start_company_registration(session, actor)
    if action == "reg_submit":
        dialog = await _dialog(session, actor, "access_company", "review")
        data = dialog.data
        row = await company.create_company_registration(
            session,
            actor,
            phone_number=str(data["phone"]),
            proposed_company_name=str(data["name"]),
            free_text=str(data["text"]),
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Обращение о подключении УК отправлено поддержке. Каждый дом добавляется отдельной заявкой.",
            [
                _button("Добавить дом", f"a:house_new:reg:{row.id}"),
                _button(
                    "Открыть обращение", f"a:request:company_registration:{row.id}"
                ),
            ],
        )
    if action == "house_new" and len(parts) == 4:
        return await _start_house_request(
            session, actor, source=parts[2], source_id=_uuid(parts[3])
        )
    if action == "house_details" and len(parts) in {2, 3}:
        return await _start_house_details(
            session, actor, _uuid(parts[2]) if len(parts) == 3 else None
        )
    if action == "house_details_submit":
        dialog = await _dialog(session, actor, "access_house_details", "review")
        data = dialog.data
        house = await company.update_house_details(
            session,
            actor,
            house_id=_uuid(str(data["house_id"])),
            entrance_count=int(data["entrance_count"]),
            apartment_count=int(data["apartment_count"]),
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            f"Дом «{house.address_display}» обновлён: подъездов {house.entrance_count}, "
            f"квартир {house.apartment_count}."
        )
    if action == "house_submit":
        dialog = await _dialog(session, actor, "access_house", "review")
        data = dialog.data
        if not data.get("entrance_count") or not data.get("apartment_count"):
            missing_step = "entrance_count" if not data.get("entrance_count") else "apartment_count"
            await update_dialog(dialog, step=missing_step)
            field = "подъездов" if missing_step == "entrance_count" else "квартир"
            return UiReply(f"Для заявки укажите положительное количество {field}.")
        source_id = _uuid(str(data["source_id"]))
        row = await company.create_house_request(
            session,
            actor,
            registration_request_id=source_id if data["source"] == "reg" else None,
            company_id=source_id if data["source"] == "company" else None,
            entered_address=str(data["address"]),
            entrance_count=int(data["entrance_count"]),
            apartment_count=int(data["apartment_count"]),
            free_text=str(data.get("note") or "") or None,
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Заявка на дом отправлена поддержке.",
            [
                _button(
                    "Добавить ещё дом", f"a:house_new:{data['source']}:{source_id}"
                ),
                _button("Открыть заявку", f"a:request:house_addition:{row.id}"),
            ],
        )
    if action == "support":
        require_support(actor)
        await clear_dialog(session, actor.id)
        return UiReply(
            "Обращения для поддержки:",
            [
                _button("Регистрация УК", "a:requests:company_registration"),
                _button("Подключение домов", "a:requests:house_addition"),
                _button("Исправить параметры дома", "a:house_details"),
            ],
        )

    if action == "staff_houses":
        await clear_dialog(session, actor.id)
        return await _staff_houses(session, actor)
    if action == "staff":
        await clear_dialog(session, actor.id)
        return await _staff_menu(session, actor)
    if action == "staff_company" and len(parts) in {3, 4}:
        return await _staff_company(
            session,
            actor,
            _uuid(parts[2]),
            page=int(parts[3]) if len(parts) == 4 else 0,
        )
    if action == "staff_detail" and len(parts) == 3:
        return await _staff_detail(session, actor, _uuid(parts[2]))
    if action == "staff_new" and len(parts) == 3:
        company_id = _uuid(parts[2])
        await require_staff(session, actor, company_id, "can_manage_staff")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_staff",
            step="phone",
            data={"company_id": str(company_id)},
        )
        return UiReply(
            "Напишите номер телефона сотрудника. Он получит личный доступ после подтверждения номера в MAX."
        )
    if action == "staff_edit" and len(parts) == 3:
        assignment = await session.get(StaffAssignment, _uuid(parts[2]))
        if assignment is None or assignment.revoked_at is not None:
            raise ValueError("Назначение сотрудника не найдено.")
        await require_staff(session, actor, assignment.company_id, "can_manage_staff")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_staff",
            step="rights",
            data={
                "company_id": str(assignment.company_id),
                "phone": assignment.phone_number,
                "editing_assignment_id": str(assignment.id),
                "rights": {
                    "can_manage_staff": assignment.can_manage_staff,
                    "can_manage_residents": assignment.can_manage_residents,
                    "can_manage_issues": assignment.can_manage_issues,
                },
            },
        )
        return await _staff_rights(await _dialog(session, actor, "access_staff"))
    if action == "staff_right" and len(parts) == 3:
        dialog = await _dialog(session, actor, "access_staff", "rights")
        if parts[2] not in {
            "can_manage_staff",
            "can_manage_residents",
            "can_manage_issues",
        }:
            raise ValueError("Неизвестное право сотрудника.")
        data = dict(dialog.data)
        rights = dict(data.get("rights") or {})
        rights[parts[2]] = not bool(rights.get(parts[2]))
        data["rights"] = rights
        await update_dialog(dialog, data=data)
        return await _staff_rights(dialog)
    if action == "staff_save":
        dialog = await _dialog(session, actor, "access_staff", "rights")
        data = dialog.data
        rights = data.get("rights") or {}
        row = await staff.assign_staff(
            session,
            actor,
            company_id=_uuid(str(data["company_id"])),
            phone_number=str(data["phone"]),
            can_manage_staff=bool(rights.get("can_manage_staff")),
            can_manage_residents=bool(rights.get("can_manage_residents")),
            can_manage_issues=bool(rights.get("can_manage_issues")),
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Назначение сотрудника сохранено.",
            [_button("Открыть сотрудника", f"a:staff_detail:{row.id}")],
        )
    if action == "staff_revoke" and len(parts) == 3:
        assignment = await session.get(StaffAssignment, _uuid(parts[2]))
        if assignment is None or assignment.revoked_at is not None:
            raise ValueError("Назначение сотрудника не найдено.")
        await require_staff(session, actor, assignment.company_id, "can_manage_staff")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_staff_revoke",
            step="confirm",
            data={"assignment_id": str(assignment.id)},
        )
        return UiReply(
            f"Отозвать права сотрудника {assignment.phone_number}? Он потеряет доступ к этой УК.",
            [
                [
                    Button("Да, отозвать", "a:staff_revoke_yes"),
                    Button("Не отзывать", f"a:staff_detail:{assignment.id}"),
                ]
            ],
        )
    if action == "staff_revoke_yes":
        dialog = await _dialog(session, actor, "access_staff_revoke", "confirm")
        await staff.revoke_staff(
            session, actor, assignment_id=_uuid(str(dialog.data["assignment_id"]))
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Назначение сотрудника отозвано.", [_button("Сотрудники", "a:staff")]
        )

    if action == "people" and len(parts) == 3:
        company_id = _uuid(parts[2])
        assignment = await require_staff(session, actor, company_id)
        can_manage_residents = actor.kind == "admin" or (
            assignment is not None and assignment.can_manage_residents
        )
        items = await queries.list_residents(session, actor, company_id=company_id)
        if not items:
            return UiReply(
                "Выданных доступов пока нет.",
                (
                    [_button("Предложить доступ", f"a:offer_new:{company_id}")]
                    if can_manage_residents
                    else []
                ),
            )
        lines = ["Жильцы и их доступ:"]
        buttons = []
        for item in items[:12]:
            label = f"{item['full_name'] or item['phone_number']}: {item['address_display']}, кв. {item['apartment_number']}"
            lines.append(
                f"• {label} — {ACCESS_STATE.get(item['status'], item['status'])}"
            )
            buttons.append(_button(_short(label), f"a:grant:{item['grant_id']}"))
        if can_manage_residents:
            buttons.append(_button("Предложить доступ", f"a:offer_new:{company_id}"))
        return UiReply("\n".join(lines)[:3300], buttons)

    if action == "company_offers" and len(parts) == 3:
        company_id = _uuid(parts[2])
        assignment = await require_staff(session, actor, company_id)
        can_manage_residents = actor.kind == "admin" or (
            assignment is not None and assignment.can_manage_residents
        )
        offers = await queries.list_company_offers(
            session, actor, company_id=company_id
        )
        if not offers:
            return UiReply(
                "Предложений доступа пока нет.",
                (
                    [_button("Предложить доступ", f"a:offer_new:{company_id}")]
                    if can_manage_residents
                    else []
                ),
            )
        lines = ["Предложения УК жильцам:"]
        for item in offers[:20]:
            lines.append(
                f"• {item['phone_number']}: {item['address_display']}, "
                f"кв. {item['apartment_number']} — "
                f"{ACCESS_STATE.get(item['status'], item['status'])}"
            )
        buttons = (
            [_button("Предложить доступ", f"a:offer_new:{company_id}")]
            if can_manage_residents
            else []
        )
        return UiReply("\n".join(lines)[:3400], buttons)

    if action == "offer_new" and len(parts) == 3:
        company_id = _uuid(parts[2])
        await require_staff(session, actor, company_id, "can_manage_residents")
        houses = await queries.list_company_houses(
            session, actor, company_id=company_id
        )
        if not houses:
            return UiReply("Сначала подключите дом к УК.")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_offer_new",
            step="house",
            data={"company_id": str(company_id)},
        )
        return UiReply(
            "К какому дому предложить доступ?",
            [
                _button(_short(item["address_display"]), f"a:offer_house:{item['id']}")
                for item in houses[:20]
            ],
        )
    if action == "offer_house" and len(parts) == 3:
        dialog = await _dialog(session, actor, "access_offer_new", "house")
        house_id = _uuid(parts[2])
        houses = await queries.list_company_houses(
            session, actor, company_id=_uuid(str(dialog.data["company_id"]))
        )
        house = next(
            (item for item in houses if str(item["id"]) == str(house_id)), None
        )
        if house is None:
            raise ValueError("Этот дом не принадлежит вашей УК.")
        await update_dialog(
            dialog,
            step="phone",
            data={
                **dialog.data,
                "house_id": str(house_id),
                "address": house["address_display"],
            },
        )
        return UiReply("Напишите номер телефона жильца, которому УК предлагает доступ.")
    if action == "offer_submit":
        dialog = await _dialog(session, actor, "access_offer_new", "review")
        data = dialog.data
        await resident.create_resident_offer(
            session,
            actor,
            house_id=_uuid(str(data["house_id"])),
            phone_number=str(data["phone"]),
            entrance_number=int(data["entrance_number"]),
            apartment_number=int(data["apartment_number"]),
            valid_to=None,
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Предложение отправлено. Доступ появится только после согласия жильца с подтверждённым номером.",
            [_button("Предложить ещё", f"a:offer_new:{data['company_id']}")],
        )
    if action == "grant" and len(parts) == 3:
        grant = await session.get(ResidentGrant, _uuid(parts[2]))
        if grant is None:
            raise ValueError("Доступ не найден.")
        apartment = await session.get(Apartment, grant.apartment_id)
        house = await session.get(House, apartment.house_id) if apartment else None
        if house is None:
            raise ValueError("Дом доступа не найден.")
        assignment = await require_staff(session, actor, house.company_id)
        can_manage_residents = actor.kind == "admin" or (
            assignment is not None and assignment.can_manage_residents
        )
        text = (
            f"Доступ: {house.address_display}, квартира {apartment.apartment_number}. "
            + (f"До {grant.valid_to.isoformat()}." if grant.valid_to else "Бессрочный.")
        )
        buttons = []
        if grant.revoked_at is None and can_manage_residents:
            if grant.valid_to is not None:
                buttons.append(_button("Продлить", f"a:grant_change:{grant.id}:extend"))
            buttons.append(_button("Отозвать", f"a:grant_change:{grant.id}:revoke"))
        buttons.append(_button("Список жильцов", f"a:people:{house.company_id}"))
        return UiReply(text, buttons)
    if action == "grant_change" and len(parts) == 4:
        grant_id, change = _uuid(parts[2]), parts[3]
        if change not in {"extend", "revoke"}:
            raise ValueError("Неизвестное действие с доступом.")
        grant = await session.get(ResidentGrant, grant_id)
        if grant is None:
            raise ValueError("Доступ не найден.")
        apartment = await session.get(Apartment, grant.apartment_id)
        house = await session.get(House, apartment.house_id) if apartment else None
        if house is None:
            raise ValueError("Дом доступа не найден.")
        await require_staff(session, actor, house.company_id, "can_manage_residents")
        await set_dialog(
            session,
            actor.id,
            flow_kind="access_grant_change",
            step="date" if change == "extend" else "reason",
            data={"grant_id": str(grant_id), "change": change},
        )
        return UiReply(
            "Введите новый срок в формате 2026-12-01T18:00:00+03:00."
            if change == "extend"
            else "Напишите причину отзыва доступа."
        )
    if action == "grant_submit":
        dialog = await _dialog(session, actor, "access_grant_change", "review")
        data = dialog.data
        grant = await resident.change_resident_grant(
            session,
            actor,
            grant_id=_uuid(str(data["grant_id"])),
            action=str(data["change"]),
            valid_to=_date(str(data["date"])) if data["change"] == "extend" else None,
            reason=str(data.get("reason") or "") or None,
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Доступ продлён." if data["change"] == "extend" else "Доступ отозван.",
            [_button("Открыть доступ", f"a:grant:{grant.id}")],
        )

    raise ValueError("Кнопка устарела. Откройте меню и повторите действие.")


def _next(text: str, buttons: list[list[Button]] | None = None) -> UiReply:
    return UiReply(text, [*(buttons or []), _button("Назад", "a:back")])


async def handle_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply | None:
    """Consume an ordinary text reply while an access workflow is active."""
    if not dialog.flow_kind.startswith("access_"):
        return None
    value = text.strip()
    if not value:
        return _next("Напишите ответ текстом или выберите кнопку.")
    if value.casefold() in {"назад", "вернуться"}:
        return await handle_action(session, actor, "a:back")
    if len(value) > 3000:
        raise ValueError("Слишком длинный ответ. Сократите текст до 3000 символов.")
    flow, step = dialog.flow_kind, dialog.step
    data = dict(dialog.data)

    if flow == "access_profile":
        if step == "name":
            data["full_name"] = normalize_full_name(value)
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Подтвердить общее ФИО: {data['full_name']}? Незакрытые заявки тоже обновятся.",
                [_button("Сохранить ФИО", "a:profile_save")],
            )
        if step == "review":
            if value.casefold() in {"да", "сохранить", "подтвердить"}:
                return await handle_action(session, actor, "a:profile_save")
            return _next(
                "Проверьте ФИО и нажмите «Сохранить ФИО».",
                [_button("Сохранить ФИО", "a:profile_save")],
            )

    if flow == "access_apply":
        if step == "search":
            if len(value) < 2:
                raise ValueError("Введите хотя бы два символа адреса.")
            matches = await queries.search_houses(session, value)
            if not matches:
                return _next("Подключённый дом не найден. Уточните улицу и номер дома.")
            return _next(
                "Выберите и подтвердите адрес дома:",
                [
                    _button(_short(item["address_display"]), f"a:house:{item['id']}")
                    for item in matches[:12]
                ],
            )
        if step == "confirm":
            return UiReply(
                f"Подтвердите выбранный адрес: {data.get('address')}",
                [
                    [
                        Button("Да, адрес верный", "a:confirm_house"),
                        Button("Искать снова", "a:apply"),
                    ]
                ],
            )
        if step == "name":
            data["full_name"] = value
            data["name_from_profile"] = False
            await _save_apply_draft(session, actor, dialog, data)
            if data.get("entrance_number") and data.get("apartment_number"):
                await update_dialog(dialog, step="review")
                return _apply_review(data)
            if data.get("entrance_number"):
                await update_dialog(dialog, step="apartment")
                return _next("Укажите номер квартиры целым числом.")
            await update_dialog(dialog, step="entrance")
            return _next("Укажите номер подъезда целым числом.")
        if step == "entrance":
            data["entrance_number"] = _positive(value, "Подъезд")
            await _save_apply_draft(session, actor, dialog, data)
            if data.get("apartment_number"):
                await update_dialog(dialog, step="review")
                return _apply_review(data)
            await update_dialog(dialog, step="apartment")
            return _next("Укажите номер квартиры целым числом.")
        if step == "apartment":
            data["apartment_number"] = _positive(value, "Квартира")
            await _save_apply_draft(session, actor, dialog, data)
            await update_dialog(dialog, step="review")
            return _apply_review(data)
        if step == "review":
            if value.casefold() in {"отправить", "да", "подтвердить"}:
                return await handle_action(session, actor, "a:submit_apply")
            return _next(
                "Чтобы отправить заявку, нажмите «Отправить заявку».",
                [_button("Отправить заявку", "a:submit_apply")],
            )

    if flow == "access_edit":
        if step == "name":
            await update_dialog(dialog, step="entrance")
            return _next("ФИО меняется через профиль. Напишите новый номер подъезда.")
        if step == "entrance":
            data["entrance_number"] = _positive(value, "Подъезд")
            await update_dialog(dialog, step="apartment", data=data)
            return _next("Укажите новый номер квартиры.")
        if step == "apartment":
            data["apartment_number"] = _positive(value, "Квартира")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Сохранить подъезд {data['entrance_number']}, квартиру {data['apartment_number']}? "
                "ФИО не меняется.",
                [_button("Сохранить исправления", "a:submit_edit")],
            )
        if step == "review":
            if value.casefold() in {"да", "сохранить", "подтвердить"}:
                return await handle_action(session, actor, "a:submit_edit")
            return _next(
                "Проверьте новые данные и нажмите кнопку.",
                [_button("Сохранить исправления", "a:submit_edit")],
            )

    if flow == "access_message" and step == "text":
        kind, request_id = _kind(str(data["kind"])), _uuid(str(data["request_id"]))
        await requests.add_discussion_message(
            session, actor, kind=kind, request_id=request_id, text=value
        )
        await clear_dialog(session, actor.id)
        return await _request_detail(session, actor, kind, request_id)

    if flow == "access_company":
        if step == "name":
            data["name"] = value
            await update_dialog(dialog, step="phone", data=data)
            return _next(
                "На какой номер выдать первый персональный доступ сотрудника УК? Напишите номер."
            )
        if step == "phone":
            data["phone"] = normalize_phone(value)
            await update_dialog(dialog, step="text", data=data)
            return _next(
                "Опишите, кто обращается, как связаться с УК и какие дома она обслуживает."
            )
        if step == "text":
            data["text"] = value
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Проверьте обращение: УК «{data['name']}», номер первого сотрудника "
                f"{data['phone']}. Пояснение: {data['text']}",
                [_button("Отправить в поддержку", "a:reg_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "отправить", "подтвердить"}:
                return await handle_action(session, actor, "a:reg_submit")
            return _next(
                "Нажмите «Отправить в поддержку» или вернитесь назад.",
                [_button("Отправить в поддержку", "a:reg_submit")],
            )

    if flow == "access_house":
        if step == "address":
            data["address"] = value
            await update_dialog(dialog, step="entrance_count", data=data)
            return _next("Сколько подъездов в доме? Укажите положительное число.")
        if step == "entrance_count":
            data["entrance_count"] = _positive(value, "Количество подъездов")
            await update_dialog(dialog, step="apartment_count", data=data)
            return _next("Сколько квартир в доме? Укажите положительное число.")
        if step == "apartment_count":
            data["apartment_count"] = _positive(value, "Количество квартир")
            if "note" not in data:
                await update_dialog(dialog, step="note", data=data)
                return _next("Есть пояснение к дому? Напишите его или ответьте «нет».")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Проверка: добавить дом «{data['address']}»; "
                f"подъездов {data['entrance_count']}, квартир {data['apartment_count']}? "
                + (f"Пояснение: {data['note']}" if data["note"] else "Без пояснения."),
                [_button("Отправить заявку", "a:house_submit")],
            )
        if step == "note":
            data["note"] = (
                "" if value.casefold() in {"нет", "пропустить", "-"} else value
            )
            if not data.get("entrance_count") or not data.get("apartment_count"):
                missing_step = "entrance_count" if not data.get("entrance_count") else "apartment_count"
                await update_dialog(dialog, step=missing_step, data=data)
                field = "подъездов" if missing_step == "entrance_count" else "квартир"
                return _next(f"Для заявки укажите положительное количество {field}.")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Проверка: добавить дом «{data['address']}»; "
                f"подъездов {data['entrance_count']}, квартир {data['apartment_count']}? "
                + (f"Пояснение: {data['note']}" if data["note"] else "Без пояснения."),
                [_button("Отправить заявку", "a:house_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "отправить", "подтвердить"}:
                return await handle_action(session, actor, "a:house_submit")
            return _next(
                "Нажмите «Отправить заявку» или вернитесь назад.",
                [_button("Отправить заявку", "a:house_submit")],
            )

    if flow == "access_house_details":
        if step == "house":
            return await _start_house_details(session, actor, _uuid(value))
        if step == "entrance_count":
            data["entrance_count"] = _positive(value, "Количество подъездов")
            await update_dialog(dialog, step="apartment_count", data=data)
            return _next("Напишите новое количество квартир.")
        if step == "apartment_count":
            data["apartment_count"] = _positive(value, "Количество квартир")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Исправить параметры дома «{data['address']}»: "
                f"подъездов {data['entrance_count']}, квартир {data['apartment_count']}?",
                [_button("Сохранить", "a:house_details_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "сохранить", "подтвердить"}:
                return await handle_action(session, actor, "a:house_details_submit")
            return _next(
                "Проверьте оба числа и нажмите «Сохранить».",
                [_button("Сохранить", "a:house_details_submit")],
            )

    if flow == "access_decision":
        if step == "company_name":
            data["company_name"] = value
            await update_dialog(dialog, step="note", data=data)
            return _next(
                "Напишите обязательное пояснение решения. Его увидит заявитель."
            )
        if step == "note":
            data["note"] = value
            kind, outcome = data["kind"], data["outcome"]
            if kind == "resident" and outcome == "granted":
                await update_dialog(dialog, step="expiry", data=data)
                return _next(
                    "Доступ будет бессрочным? Если нужен срок, напишите дату в формате "
                    "2026-12-01T18:00:00+03:00.",
                    [_button("Бессрочный доступ", "a:decision_no_expiry")],
                )
            if kind == "house_addition" and outcome == "approved":
                await update_dialog(dialog, step="entrance_count", data=data)
                return _next(
                    f"В заявке указано подъездов: {data.get('proposed_entrance_count') or 'не указано'}. "
                    "Если нужно исправить число, напишите новое положительное количество.",
                    [_button("Оставить как в заявке", "a:decision_keep_entrances")],
                )
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Подтвердить решение «{OUTCOME.get(str(outcome), outcome)}»? Пояснение: {value}",
                [_button("Подтвердить решение", "a:decision_submit")],
            )
        if step == "expiry":
            if value.casefold() in {"бессрочно", "без срока", "не указывать"}:
                return await handle_action(session, actor, "a:decision_no_expiry")
            date = _date(value)
            data["valid_to"] = date.isoformat()
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Выдать доступ до {date.isoformat()}?",
                [_button("Подтвердить выдачу", "a:decision_submit")],
            )
        if step == "entrance_count":
            if value.casefold() in {"оставить", "без изменений", "не указывать", "пропустить"}:
                return await handle_action(session, actor, "a:decision_keep_entrances")
            data["entrance_count"] = _positive(value, "Количество подъездов")
            await update_dialog(dialog, step="apartment_count", data=data)
            return _next(
                f"Подъездов будет {data['entrance_count']}. "
                f"В заявке указано квартир: {data.get('proposed_apartment_count') or 'не указано'}. "
                "Если нужно исправить число, напишите новое положительное количество.",
                [_button("Оставить как в заявке", "a:decision_keep_apartments")],
            )
        if step == "apartment_count":
            if value.casefold() in {"оставить", "без изменений", "не указывать", "пропустить"}:
                return await handle_action(session, actor, "a:decision_keep_apartments")
            data["apartment_count"] = _positive(value, "Количество квартир")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Одобрить дом: подъездов {data.get('entrance_count') or data.get('proposed_entrance_count') or 'как в заявке'}, "
                f"квартир {data['apartment_count']}?",
                [_button("Подтвердить", "a:decision_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "подтвердить"}:
                return await handle_action(session, actor, "a:decision_submit")
            return _next(
                "Проверьте решение и нажмите подтверждение.",
                [_button("Подтвердить решение", "a:decision_submit")],
            )

    if flow == "access_staff":
        if step == "phone":
            data["phone"] = normalize_phone(value)
            data["rights"] = {
                "can_manage_staff": False,
                "can_manage_residents": False,
                "can_manage_issues": False,
            }
            await update_dialog(dialog, step="rights", data=data)
            return await _staff_rights(dialog)
        if step == "rights":
            choices = {
                "сотрудники": "can_manage_staff",
                "жильцы": "can_manage_residents",
                "проблемы": "can_manage_issues",
            }
            if value.casefold() in choices:
                return await handle_action(
                    session, actor, f"a:staff_right:{choices[value.casefold()]}"
                )
            if value.casefold() in {"сохранить", "готово"}:
                return await handle_action(session, actor, "a:staff_save")
            return await _staff_rights(dialog)

    if flow == "access_offer_new":
        if step == "house":
            return UiReply(
                "Выберите дом кнопкой из списка.",
                [_button("Показать дома", f"a:offer_new:{data['company_id']}")],
            )
        if step == "phone":
            data["phone"] = normalize_phone(value)
            await update_dialog(dialog, step="entrance", data=data)
            return _next("Номер подъезда жильца?")
        if step == "entrance":
            data["entrance_number"] = _positive(value, "Подъезд")
            await update_dialog(dialog, step="apartment", data=data)
            return _next("Номер квартиры жильца?")
        if step == "apartment":
            data["apartment_number"] = _positive(value, "Квартира")
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Предложить доступ номеру {data['phone']}: {data['address']}, "
                f"подъезд {data['entrance_number']}, квартира {data['apartment_number']}? "
                "Доступ появится только после согласия человека.",
                [_button("Отправить предложение", "a:offer_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "отправить", "подтвердить"}:
                return await handle_action(session, actor, "a:offer_submit")
            return _next(
                "Проверьте данные предложения и нажмите кнопку.",
                [_button("Отправить предложение", "a:offer_submit")],
            )

    if flow == "access_grant_change":
        if step == "date":
            data["date"] = _date(value).isoformat()
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Продлить доступ до {data['date']}?",
                [_button("Подтвердить продление", "a:grant_submit")],
            )
        if step == "reason":
            data["reason"] = value
            await update_dialog(dialog, step="review", data=data)
            return _next(
                f"Отозвать доступ? Причина: {value}",
                [_button("Подтвердить отзыв", "a:grant_submit")],
            )
        if step == "review":
            if value.casefold() in {"да", "подтвердить"}:
                return await handle_action(session, actor, "a:grant_submit")
            return _next(
                "Нажмите подтверждение или вернитесь назад.",
                [_button("Подтвердить изменение", "a:grant_submit")],
            )

    return UiReply("Выберите кнопку в предыдущем сообщении или откройте главное меню.")
