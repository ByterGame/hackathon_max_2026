"""Button-driven issue scenarios backed by the same services as HTTP and commands."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.drafts_text import _send_text
from src.bot.handlers.issues_text import (
    ISSUE_PAGE_SIZE,
    _page_bounds,
    _page_number,
    _participant_label,
    _staff_merge_candidates,
    _text_chunks,
)
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
    AuditEvent,
    BotDialog,
    File,
    House,
    IssueCategory,
    IssueMessage,
    IssueReport,
    IssueSupport,
    IssueTarget,
    ResidentGrant,
    StaffAssignment,
    User,
)
from src.domain.drafts.service import DraftError, get_draft, mark_submitted, save_draft
from src.domain.files.service import attach
from src.domain.issues.rules import ACTIVE_STATUSES
from src.domain.issues.service import (
    IssueError,
    _active_resident_apartments,
    add_comment,
    edit_card,
    get_visible_card,
    list_visible_cards,
    merge_cards,
    merged_card_ids,
    reopen_card,
    set_status,
    support_card,
)
from src.domain.issues.suggest import suggest_issue
from src.domain.notifications.service import NotificationError, is_muted, set_mute

STATUS_LABELS = {
    "open": "Открыта",
    "reviewing": "На рассмотрении",
    "needs_info": "Нужны уточнения",
    "in_progress": "В работе",
    "closed": "Закрыта",
}
HOUSE_PAGE_SIZE = 20


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Кнопка устарела или содержит неверный идентификатор"
        ) from error


def _button(text: str, payload: str) -> list[Button]:
    return [Button(text, payload)]


def _scope_label(
    all_house: bool, entrances: list[int], apartments: list[tuple[int | None, int]]
) -> str:
    if all_house:
        return "весь дом"
    parts = []
    if entrances:
        parts.append("подъезды " + ", ".join(map(str, sorted(set(entrances)))))
    if apartments:
        parts.append(
            "квартиры "
            + ", ".join(map(str, sorted({apartment for _, apartment in apartments})))
        )
    return "; ".join(parts) or "не указана"


def _summary_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _stale() -> UiReply:
    return UiReply(
        "Этот шаг уже неактуален. Откройте раздел проблем заново.",
        [_button("Проблемы домов", "i:houses")],
    )


def _history_page(
    body: str, requested_page: int, *, kind: str, card_id: UUID
) -> UiReply:
    chunks = _text_chunks(body)
    page, pages, _ = _page_bounds(len(chunks), requested_page, 1)
    buttons = []
    navigation = []
    if page > 1:
        navigation.append(Button("← Назад", f"i:{kind}:{card_id}:{page - 1}"))
    if page < pages:
        navigation.append(Button("Далее →", f"i:{kind}:{card_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(_button("К карточке", f"i:card:{card_id}"))
    title = "Обсуждение и дополнения" if kind == "discussion" else "История изменений"
    return UiReply(f"{title} · страница {page}/{pages}\n{chunks[page - 1]}", buttons)


async def _dialog(
    session: AsyncSession, actor: User, flow_kind: str, *steps: str
) -> BotDialog | None:
    dialog = await get_dialog(session, actor.id)
    if dialog is None or dialog.flow_kind != flow_kind or dialog.step not in steps:
        return None
    return dialog


async def _staff_can_manage(session: AsyncSession, actor: User, house_id: UUID) -> bool:
    if actor.kind != "employee":
        return False
    house = await session.get(House, house_id)
    if house is None:
        return False
    assignment = await session.scalar(
        select(StaffAssignment.id).where(
            StaffAssignment.user_id == actor.id,
            StaffAssignment.company_id == house.company_id,
            StaffAssignment.revoked_at.is_(None),
            StaffAssignment.can_manage_issues.is_(True),
        )
    )
    return assignment is not None


async def _houses(
    session: AsyncSession, actor: User, requested_page: int = 1
) -> UiReply:
    now = datetime.now(UTC)
    resident_houses = (
        select(Apartment.house_id)
        .join(ResidentGrant, ResidentGrant.apartment_id == Apartment.id)
        .where(
            ResidentGrant.user_id == actor.id,
            ResidentGrant.valid_from <= now,
            or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
            ResidentGrant.revoked_at.is_(None),
        )
    )
    staff_companies = select(StaffAssignment.company_id).where(
        StaffAssignment.user_id == actor.id,
        StaffAssignment.revoked_at.is_(None),
    )
    accessible = House.archived_at.is_(None), or_(
        House.id.in_(resident_houses), House.company_id.in_(staff_companies)
    )
    total = await session.scalar(
        select(func.count()).select_from(House).where(*accessible)
    )
    page, pages, selected = _page_bounds(total or 0, requested_page, HOUSE_PAGE_SIZE)
    houses = (
        await session.scalars(
            select(House)
            .where(*accessible)
            .order_by(House.address_display, House.id)
            .offset(selected.start)
            .limit(HOUSE_PAGE_SIZE)
        )
    ).all()
    if not houses:
        return UiReply(
            "Пока нет домов, к которым у вас есть доступ. Сначала подайте заявку на подключение к дому.",
            [_button("Подключиться к дому", "a:apply")],
        )
    buttons = [
        _button(house.address_display[:55], f"i:house:{house.id}") for house in houses
    ]
    navigation = []
    if page > 1:
        navigation.append(Button("← Назад", f"i:houses:{page - 1}"))
    if page < pages:
        navigation.append(Button("Далее →", f"i:houses:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    return UiReply(
        f"Выберите дом · страница {page}/{pages}:",
        buttons,
    )


async def _house_cards(
    session: AsyncSession, actor: User, house_id: UUID, requested_page: int = 1
) -> UiReply:
    cards = await list_visible_cards(session, actor, house_id, include_closed=True)
    page, pages, selected = _page_bounds(len(cards), requested_page, ISSUE_PAGE_SIZE)
    house = await session.get(House, house_id)
    buttons = []
    if actor.kind == "resident":
        buttons.append(_button("Сообщить о проблеме", f"i:new:{house_id}"))
    buttons.extend(
        _button(
            f"{card.title[:38]} · {STATUS_LABELS.get(card.status, card.status)}",
            f"i:card:{card.id}:{page}",
        )
        for card in cards[selected]
    )
    navigation = []
    if page > 1:
        navigation.append(Button("← Назад", f"i:house:{house_id}:{page - 1}"))
    if page < pages:
        navigation.append(Button("Далее →", f"i:house:{house_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(_button("Другой дом", "i:houses"))
    address = house.address_display if house else "Дом"
    text = (
        f"{address}\nПроблемы: {len(cards)} · страница {page}/{pages}. "
        "Выберите проблему или создайте новую."
    )
    if not cards:
        text += "\nПроблем пока нет."
    return UiReply(text, buttons)


async def _card(
    session: AsyncSession, actor: User, card_id: UUID, list_page: int = 1
) -> UiReply:
    card = await get_visible_card(session, actor, card_id)
    category = await session.get(IssueCategory, card.category_id)
    targets = (
        await session.execute(
            select(IssueTarget, Apartment)
            .outerjoin(Apartment, Apartment.id == IssueTarget.apartment_id)
            .where(IssueTarget.card_id == card.id)
        )
    ).all()
    scope = _scope_label(
        card.scope_all_house,
        [
            target.entrance_number
            for target, _ in targets
            if target.entrance_number is not None
        ],
        [
            (apartment.entrance_number, apartment.apartment_number)
            for target, apartment in targets
            if target.apartment_id is not None and apartment is not None
        ],
    )
    support_count = await session.scalar(
        select(func.count())
        .select_from(IssueSupport)
        .where(IssueSupport.card_id == card.id)
    )
    reports = (
        await session.scalars(
            select(IssueReport)
            .where(IssueReport.card_id == card.id)
            .order_by(IssueReport.created_at)
            .limit(1)
        )
    ).all()
    messages = (
        await session.scalars(
            select(IssueMessage)
            .where(IssueMessage.card_id == card.id)
            .order_by(IssueMessage.created_at.desc())
            .limit(5)
        )
    ).all()
    lines = [
        card.title,
        f"Статус: {STATUS_LABELS.get(card.status, card.status)}",
        f"Категория: {category.name if category else '—'} · Поддержали: {support_count or 0}",
        f"Область: {scope}",
    ]
    if summary := getattr(card, "summary_description", None):
        lines.append(f"Краткое формализованное описание: {summary}")
    if card.current_note:
        lines.append(f"Пояснение УК: {card.current_note}")
    if card.close_result:
        lines.append(f"Результат: {card.close_result}")
    if reports:
        lines.append("Исходное описание: " + reports[0].raw_description)
    if messages:
        lines.append("Обсуждение:")
        lines.extend(
            f"{_participant_label(actor.id, message.author_user_id, official=message.kind == 'official_uk')}: "
            f"{message.body}"
            for message in reversed(messages)
        )
    buttons: list[list[Button]] = []
    if card.status != "closed" and actor.kind == "resident":
        buttons.append(_button("Поддержать", f"i:support:{card.id}"))
    buttons.append(_button("Написать в обсуждение", f"i:comment:{card.id}"))
    buttons.append(_button("Обсуждение и дополнения", f"i:discussion:{card.id}"))
    buttons.append(_button("История изменений", f"i:history:{card.id}"))
    buttons.append(_button("Вложения", f"f:card:{card.id}"))
    muted = await is_muted(session, actor.id, "issue_card", card.id)
    buttons.append(
        _button(
            "Включить уведомления" if muted else "Отключить уведомления",
            f"i:{'unmute' if muted else 'mute'}:{card.id}",
        )
    )
    if card.status == "closed" and card.author_user_id == actor.id:
        buttons.append(_button("Переоткрыть", f"i:reopen:{card.id}"))
    if await _staff_can_manage(session, actor, card.house_id):
        buttons.extend(
            [
                _button("Сменить статус", f"i:status:{card.id}"),
                _button("Изменить название", f"i:edit:{card.id}"),
            ]
        )
        if card.status in ACTIVE_STATUSES:
            buttons.append(_button("Найти похожие", f"i:merge_suggest:{card.id}"))
            buttons.append(_button("Объединить с другой", f"i:merge:{card.id}"))
    buttons.append(_button("К списку проблем", f"i:house:{card.house_id}:{list_page}"))
    return UiReply("\n".join(lines)[:3500], buttons)


async def _discussion(
    session: AsyncSession, actor: User, card_id: UUID, page: int = 1
) -> UiReply:
    card = await get_visible_card(session, actor, card_id)
    reports = (
        await session.scalars(
            select(IssueReport)
            .where(IssueReport.card_id == card.id)
            .order_by(IssueReport.created_at, IssueReport.id)
        )
    ).all()
    messages = (
        await session.scalars(
            select(IssueMessage)
            .where(IssueMessage.card_id == card.id)
            .order_by(IssueMessage.created_at, IssueMessage.id)
        )
    ).all()
    entries = [
        (
            report.created_at,
            report.id,
            f"{report.created_at:%d.%m %H:%M} · "
            f"{'Ваше дополнение' if report.author_user_id == actor.id else 'Дополнение жильца'}: "
            f"{report.raw_description}",
        )
        for report in reports
    ]
    entries.extend(
        (
            message.created_at,
            message.id,
            f"{message.created_at:%d.%m %H:%M} · "
            f"{_participant_label(actor.id, message.author_user_id, official=message.kind == 'official_uk')}: "
            f"{message.body}",
        )
        for message in messages
    )
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    body = "\n\n".join(item[2] for item in entries) or "Обсуждение пока пусто."
    return _history_page(body, page, kind="discussion", card_id=card.id)


async def _audit_history(
    session: AsyncSession, actor: User, card_id: UUID, page: int = 1
) -> UiReply:
    card = await get_visible_card(session, actor, card_id)
    rows = (
        await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_kind == "issue_card",
                AuditEvent.entity_id.in_(await merged_card_ids(session, card.id)),
                AuditEvent.actor_user_id.is_not(None),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    ).all()
    rows = [row for row in rows if row.action != "comment_added"]
    body = (
        "\n".join(
            f"{row.created_at:%d.%m %H:%M} · {row.action} · "
            f"{'Вы' if row.actor_user_id == actor.id else 'Участник'}"
            for row in rows
        )
        or "История изменений пока пуста."
    )
    return _history_page(body, page, kind="history", card_id=card.id)


async def _categories(session: AsyncSession, actor: User, house_id: UUID) -> UiReply:
    await list_visible_cards(session, actor, house_id)
    await set_dialog(
        session,
        actor.id,
        flow_kind="issue_new",
        step="category",
        data={"house_id": str(house_id)},
    )
    rows = (
        await session.scalars(
            select(IssueCategory)
            .where(IssueCategory.is_active.is_(True))
            .order_by(IssueCategory.sort_order, IssueCategory.name)
        )
    ).all()
    return UiReply(
        "Выберите категорию проблемы:",
        [_button(row.name[:55], f"i:category:{row.id}") for row in rows]
        + [_button("Назад", "i:back")],
    )


async def _scope_options(
    session: AsyncSession, actor: User, dialog: BotDialog
) -> UiReply:
    house_id = _uuid(str(dialog.data["house_id"]))
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    apartments = await _active_resident_apartments(session, actor, house)
    if not apartments:
        raise IssueError("house_access_denied", "Нужен действующий доступ к дому", 403)
    buttons = [_button("Весь дом", "i:scope:house")]
    if any(apartment.entrance_number is not None for apartment in apartments.values()):
        buttons.append(_button("Мой подъезд", "i:scope:entrance"))
    buttons.append(_button("Моя квартира", "i:scope:apartment"))
    buttons.append(_button("Назад", "i:back"))
    note = (
        " Если ваш подъезд отсутствует, попросите УК указать его в вашей привязке."
        if all(apartment.entrance_number is None for apartment in apartments.values())
        else ""
    )
    return UiReply("Где проблема: в вашей квартире, подъезде или во всём доме?" + note, buttons)


async def _location_options(
    session: AsyncSession, actor: User, dialog: BotDialog, scope: str, page: int = 1
) -> UiReply:
    house_id = _uuid(str(dialog.data["house_id"]))
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    apartments = await _active_resident_apartments(session, actor, house)
    locations: list[tuple[UUID, str]] = []
    seen_entrances: set[int] = set()
    for apartment in sorted(apartments.values(), key=lambda item: (item.entrance_number or 0, item.apartment_number)):
        if scope == "entrance":
            entrance = apartment.entrance_number
            if entrance is None or entrance in seen_entrances:
                continue
            seen_entrances.add(entrance)
            locations.append((apartment.id, f"Подъезд {entrance}"))
        else:
            locations.append((apartment.id, f"Квартира {apartment.apartment_number}, подъезд {apartment.entrance_number or 'не указан'}"))
    if not locations:
        raise IssueError("location_unavailable", "Для этой области нет подтверждённой привязки", 409)
    if len(locations) == 1:
        return await _prepare_preview(session, actor, dialog, scope, locations[0][0])
    selected_page, pages, selected = _page_bounds(len(locations), page, 10)
    await update_dialog(dialog, step="scope_location", data={**dialog.data, "selected_scope": scope})
    buttons = [_button(label, f"i:loc:{apartment_id}") for apartment_id, label in locations[selected]]
    navigation = []
    if selected_page > 1:
        navigation.append(Button("← Назад", f"i:loc_page:{selected_page - 1}"))
    if selected_page < pages:
        navigation.append(Button("Далее →", f"i:loc_page:{selected_page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(_button("К выбору области", "i:back"))
    return UiReply(f"Выберите свою локацию · страница {selected_page}/{pages}:", buttons)


async def _prepare_preview(
    session: AsyncSession, actor: User, dialog: BotDialog, scope: str,
    apartment_id: UUID | None = None,
) -> UiReply:
    data = dict(dialog.data)
    house_id = _uuid(str(data["house_id"]))
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    apartments = await _active_resident_apartments(session, actor, house)
    if not apartments:
        raise IssueError("house_access_denied", "Нужен действующий доступ к дому", 403)
    if scope not in {"house", "entrance", "apartment"} or (scope == "house" and apartment_id is not None):
        raise IssueError("invalid_scope", "Выберите свою квартиру, подъезд или весь дом")
    selected_apartment = None
    if scope != "house":
        if apartment_id is None and len(apartments) == 1:
            apartment_id = next(iter(apartments))
        selected_apartment = apartments.get(apartment_id)
        if selected_apartment is None:
            raise IssueError("apartment_access_denied", "Выберите свою подтверждённую квартиру", 403)
        if scope == "entrance" and selected_apartment.entrance_number is None:
            raise IssueError("entrance_unknown", "Уточните подъезд через УК", 409)
    category_id = _uuid(str(data["category_id"]))
    description = str(data["description"])
    suggestion = await suggest_issue(
        session,
        actor,
        house_id=house_id,
        description=description,
        category_id=category_id,
        scope=scope,
        apartment_id=apartment_id,
    )
    data["scope"] = scope
    if apartment_id is None:
        data.pop("apartment_id", None)
    else:
        data["apartment_id"] = str(apartment_id)
    data["scope_label"] = (
        "весь дом" if scope == "house" else
        f"подъезд {selected_apartment.entrance_number}" if scope == "entrance" else
        f"квартира {selected_apartment.apartment_number}"
    )
    data.pop("selected_scope", None)
    data["suggestions"] = [
        {"id": str(item.id), "title": item.title}
        for item in suggestion.candidates
        if item.id in suggestion.similar_card_ids
    ]
    data["suggestion_source"] = suggestion.source
    data["description_check"] = suggestion.description_check
    data["description_warning"] = suggestion.description_warning
    title = str(data.get("title") or suggestion.suggested_title)
    data["title"] = title
    suggested_summary = _summary_text(getattr(suggestion, "summary_description", None))
    previous_summary = _summary_text(data.get("summary_description"))
    summary = previous_summary or suggested_summary or description
    data["summary_description"] = summary
    data["_summary_fallback"] = (
        bool(data.get("_summary_fallback")) if previous_summary else suggested_summary is None
    )
    payload = {
        "house_id": str(house_id),
        "category_id": str(category_id),
        "scope": scope,
        **({"apartment_id": str(apartment_id)} if apartment_id is not None else {}),
        "title": title,
        "description": description,
        "summary_description": summary,
    }
    previous = (
        await get_draft(session, actor, dialog.draft_id) if dialog.draft_id else None
    )
    if previous is not None and previous.revision != data.get("draft_revision"):
        raise ValueError("Черновик изменился в другом окне. Откройте его заново")
    draft = await save_draft(
        session,
        actor,
        flow_kind="issue_card",
        payload=payload,
        draft_id=previous.id if previous else None,
        revision=previous.revision if previous else None,
    )
    data["draft_revision"] = draft.revision
    await update_dialog(dialog, step="review", data=data, draft_id=draft.id)
    return _preview(dialog)


def _preview(dialog: BotDialog) -> UiReply:
    data = dialog.data
    scope_text = str(data.get("scope_label") or {"house": "весь дом"}.get(data.get("scope"), "не указана"))
    lines = [
        "Проверьте обращение:",
        f"Название: {data.get('title', '—')}",
        f"Область: {scope_text}",
        f"Исходное описание: {data.get('description', '—')}",
        "Краткое формализованное описание: "
        + str(data.get("summary_description") or data.get("description", "—")),
    ]
    if data.get("_summary_fallback") or not data.get("summary_description"):
        lines.append(
            "Автоматическая сводка недоступна: пока используется исходное описание. "
            + (
                "Его можно исправить ниже."
                if dialog.step == "attachments"
                else "Изменить его можно на заключительном экране."
            )
        )
    if data.get("suggestion_source") == "gigachat":
        lines.append("GigaChat обработал описание и поискал похожие проблемы.")
        if data.get("description_check") == "warning":
            lines.append(
                "Стоит уточнить описание: "
                + str(data.get("description_warning") or "Что именно случилось и где?")
            )
        else:
            lines.append("Описание достаточно понятно для подачи заявки.")
    elif data.get("suggestion_source") == "local":
        lines.append(
            "Нейросеть не использовалась: название взято из первой фразы, "
            "поиск похожих карточек — по совпадению слов."
        )
    buttons: list[list[Button]] = []
    for item in data.get("suggestions", []):
        buttons.append(
            _button(
                f"Совпадает: {str(item['title'])[:40]}",
                f"i:issue:duplicate:{item['id']}",
            )
        )
    if buttons:
        lines.append(
            "Если похожая карточка описывает ту же проблему, выберите её. Иначе создайте новую."
        )
    else:
        lines.append("Похожих открытых карточек не найдено.")
    buttons.extend(
        [
            _button("Изменить название", "i:issue:edit_title"),
            _button("Исправить описание", "i:issue:edit_description"),
        ]
    )
    if dialog.step == "attachments":
        lines.append(
            "Можете прислать фото, PDF или видео следующим сообщением либо отправить без вложений."
        )
        buttons.append(_button("Изменить краткое описание", "i:issue:edit_summary"))
        buttons.append(_button("Отправить обращение", "i:issue:submit"))
    else:
        buttons.append(_button("Создать новую карточку", "i:issue:continue"))
    buttons.append(_button("Назад", "i:back"))
    return UiReply("\n".join(lines)[:3900], buttons)


async def _resume(session: AsyncSession, actor: User, draft_id: UUID) -> UiReply:
    draft = await get_draft(session, actor, draft_id)
    if draft.flow_kind != "issue_card" or draft.submitted_at is not None:
        raise ValueError("Этот черновик проблемы уже отправлен или недоступен")
    payload = draft.payload
    required = {"house_id", "category_id", "description"}
    if not required.issubset(payload):
        return UiReply(
            "Этот черновик заполнен не полностью. Дополните его в мини-приложении "
            "или начните новое обращение в боте.",
            [_button("Новое обращение", "i:new")],
        )
    house_id = _uuid(str(payload["house_id"]))
    await list_visible_cards(session, actor, house_id)
    data = {
        "house_id": str(house_id),
        "category_id": str(payload["category_id"]),
        "description": str(payload["description"]),
        **({"summary_description": payload["summary_description"]} if payload.get("summary_description") else {}),
        **({"title": payload["title"]} if payload.get("title") else {}),
        "draft_revision": draft.revision,
    }
    dialog = await set_dialog(
        session,
        actor.id,
        flow_kind="issue_new",
        step="scope",
        data=data,
        draft_id=draft.id,
    )
    scope = payload.get("scope")
    if scope not in {"house", "entrance", "apartment"}:
        return await _scope_options(session, actor, dialog)
    try:
        apartment_id = _uuid(str(payload["apartment_id"])) if payload.get("apartment_id") else None
    except ValueError:
        return await _scope_options(session, actor, dialog)
    try:
        return await _prepare_preview(session, actor, dialog, str(scope), apartment_id)
    except IssueError as error:
        if error.code not in {"apartment_access_denied", "entrance_unknown"}:
            raise
        return await _scope_options(session, actor, dialog)


async def _status_menu(session: AsyncSession, actor: User, card_id: UUID) -> UiReply:
    card = await get_visible_card(session, actor, card_id)
    if not await _staff_can_manage(session, actor, card.house_id):
        raise IssueError("issue_permission_denied", "Нет права менять статус", 403)
    if card.status == "closed":
        return UiReply("Закрытую карточку может переоткрыть только её автор.")
    await set_dialog(
        session,
        actor.id,
        flow_kind="issue_status",
        step="pick",
        data={"card_id": str(card.id)},
    )
    return UiReply(
        f"Статус сейчас: {STATUS_LABELS.get(card.status, card.status)}. Выберите новый:",
        [
            _button(label, f"i:status_choice:{value}")
            for value, label in STATUS_LABELS.items()
        ]
        + [_button("Назад", "i:back")],
    )


async def _merge_menu(session: AsyncSession, actor: User, card_id: UUID) -> UiReply:
    card = await get_visible_card(session, actor, card_id)
    if not await _staff_can_manage(session, actor, card.house_id):
        raise IssueError(
            "issue_permission_denied", "Нет права объединять карточки", 403
        )
    candidates = [
        item
        for item in await list_visible_cards(session, actor, card.house_id)
        if item.id != card.id and item.status in ACTIVE_STATUSES
    ]
    await set_dialog(
        session,
        actor.id,
        flow_kind="issue_merge",
        step="pick",
        data={"card_id": str(card.id)},
    )
    if not candidates:
        return UiReply(
            "Других открытых карточек этого дома нет.", [_button("Назад", "i:back")]
        )
    return UiReply(
        f"С чем объединить «{card.title}»? Историю и голоса обеих карточек сохраним.",
        [
            _button(item.title[:55], f"i:merge_with:{item.id}")
            for item in candidates[:15]
        ]
        + [_button("Назад", "i:back")],
    )


async def _merge_suggestions(session: AsyncSession, actor: User, card_id: UUID) -> UiReply:
    result, candidates = await _staff_merge_candidates(session, actor, card_id)
    await set_dialog(
        session,
        actor.id,
        flow_kind="issue_merge",
        step="pick",
        data={"card_id": str(card_id)},
    )
    source = "GigaChat" if result.source == "gigachat" else "локальный поиск без нейросети"
    buttons = [
        _button(card.title[:55], f"i:merge_with:{card.id}")
        for card in candidates
    ]
    buttons.extend([
        _button("Все открытые карточки", f"i:merge:{card_id}"),
        _button("Назад", "i:back"),
    ])
    message = (
        "Выберите подходящую карточку и подтвердите объединение вручную."
        if candidates
        else "Похожих открытых карточек не найдено."
    )
    return UiReply(f"Похожие карточки ({source}). {message}", buttons)


async def _back(session: AsyncSession, actor: User) -> UiReply:
    dialog = await get_dialog(session, actor.id)
    if dialog is None:
        return await _houses(session, actor)
    if dialog.flow_kind == "issue_new":
        if dialog.step == "category":
            await clear_dialog(session, actor.id)
            return await _houses(session, actor)
        if dialog.step == "description":
            if dialog.data.get("_editing_description"):
                data = dict(dialog.data)
                data.pop("_editing_description", None)
                await update_dialog(dialog, step="review", data=data)
                return _preview(dialog)
            return await _categories(
                session, actor, _uuid(str(dialog.data["house_id"]))
            )
        if dialog.step == "scope_location":
            await update_dialog(dialog, step="scope")
            return await _scope_options(session, actor, dialog)
        if dialog.step == "scope":
            await update_dialog(dialog, step="description")
            return UiReply(
                "Опишите проблему одним сообщением:", [_button("Назад", "i:back")]
            )
        if dialog.step == "edit_title":
            await update_dialog(dialog, step="review")
            return _preview(dialog)
        if dialog.step == "edit_summary":
            await update_dialog(dialog, step="attachments")
            return _preview(dialog)
        if dialog.step == "attachments":
            await update_dialog(dialog, step="review")
            return _preview(dialog)
        if dialog.step == "review":
            await update_dialog(dialog, step="scope")
            return await _scope_options(session, actor, dialog)
    if dialog.flow_kind == "issue_status":
        card_id = _uuid(str(dialog.data["card_id"]))
        if dialog.step == "note" and dialog.data.get("status") == "closed":
            await update_dialog(dialog, step="result")
            return UiReply(
                "Чем закончилось обращение?",
                [
                    _button("Проблема решена", "i:status_result:solved"),
                    _button("Обращение некорректно", "i:status_result:invalid"),
                    _button("Назад", "i:back"),
                ],
            )
        if dialog.step in {"note", "result"}:
            return await _status_menu(session, actor, card_id)
    if dialog.flow_kind == "issue_merge" and dialog.step == "confirm":
        return await _merge_menu(session, actor, _uuid(str(dialog.data["card_id"])))
    card_id = dialog.data.get("card_id")
    await clear_dialog(session, actor.id)
    return (
        await _card(session, actor, _uuid(str(card_id)))
        if card_id
        else await _houses(session, actor)
    )


async def _handle_action(session: AsyncSession, actor: User, payload: str) -> UiReply:
    if payload == "i:back":
        return await _back(session, actor)
    if payload == "i:houses" or payload == "i:new":
        return await _houses(session, actor)
    if payload.startswith("i:houses:"):
        return await _houses(
            session, actor, _page_number(payload.removeprefix("i:houses:"))
        )
    if payload.startswith("i:house:"):
        parts = payload.split(":")
        if len(parts) not in {3, 4}:
            raise ValueError("Кнопка списка проблем устарела")
        return await _house_cards(
            session,
            actor,
            _uuid(parts[2]),
            _page_number(parts[3]) if len(parts) == 4 else 1,
        )
    if payload.startswith("i:card:"):
        parts = payload.split(":")
        if len(parts) not in {3, 4}:
            raise ValueError("Кнопка карточки устарела")
        return await _card(
            session,
            actor,
            _uuid(parts[2]),
            _page_number(parts[3]) if len(parts) == 4 else 1,
        )
    if payload.startswith("i:discussion:") or payload.startswith("i:history:"):
        parts = payload.split(":")
        if len(parts) not in {3, 4}:
            raise ValueError("Кнопка истории устарела")
        card_id = _uuid(parts[2])
        page = _page_number(parts[3]) if len(parts) == 4 else 1
        if parts[1] == "discussion":
            return await _discussion(session, actor, card_id, page)
        return await _audit_history(session, actor, card_id, page)
    if payload.startswith("i:new:"):
        return await _categories(session, actor, _uuid(payload.removeprefix("i:new:")))
    if payload.startswith("i:resume:"):
        return await _resume(session, actor, _uuid(payload.removeprefix("i:resume:")))
    if payload.startswith("i:category:"):
        dialog = await _dialog(session, actor, "issue_new", "category")
        if dialog is None:
            return _stale()
        category_id = _uuid(payload.removeprefix("i:category:"))
        category = await session.get(IssueCategory, category_id)
        if category is None or not category.is_active:
            raise ValueError("Категория больше не доступна")
        data = dict(dialog.data)
        data["category_id"] = str(category.id)
        await update_dialog(dialog, step="description", data=data)
        return UiReply(
            "Опишите проблему одним сообщением (до 1500 символов):",
            [_button("Назад", "i:back")],
        )
    if payload.startswith("i:scope:"):
        dialog = await _dialog(session, actor, "issue_new", "scope")
        if dialog is None:
            return _stale()
        scope = payload.removeprefix("i:scope:")
        if scope in {"house", "all"}:
            return await _prepare_preview(session, actor, dialog, "house")
        if scope in {"entrance", "apartment"}:
            return await _location_options(session, actor, dialog, scope)
        return _stale()
    if payload.startswith("i:loc_page:"):
        dialog = await _dialog(session, actor, "issue_new", "scope_location")
        if dialog is None:
            return _stale()
        return await _location_options(
            session, actor, dialog, str(dialog.data["selected_scope"]),
            _page_number(payload.removeprefix("i:loc_page:")),
        )
    if payload.startswith("i:loc:"):
        dialog = await _dialog(session, actor, "issue_new", "scope_location")
        if dialog is None:
            return _stale()
        return await _prepare_preview(
            session, actor, dialog, str(dialog.data["selected_scope"]),
            _uuid(payload.removeprefix("i:loc:")),
        )
    if payload == "i:issue:preview":
        dialog = await _dialog(session, actor, "issue_new", "review", "attachments")
        return _preview(dialog) if dialog else _stale()
    if payload == "i:issue:edit_title":
        dialog = await _dialog(session, actor, "issue_new", "review", "attachments")
        if dialog is None:
            return _stale()
        await update_dialog(dialog, step="edit_title")
        return UiReply(
            "Напишите новое короткое название:", [_button("Назад", "i:back")]
        )
    if payload == "i:issue:edit_description":
        dialog = await _dialog(session, actor, "issue_new", "review", "attachments")
        if dialog is None:
            return _stale()
        data = dict(dialog.data)
        data["_editing_description"] = True
        await update_dialog(dialog, step="description", data=data)
        return UiReply("Напишите исправленное описание:", [_button("Назад", "i:back")])
    if payload == "i:issue:edit_summary":
        dialog = await _dialog(session, actor, "issue_new", "attachments")
        if dialog is None:
            return _stale()
        await update_dialog(dialog, step="edit_summary")
        return UiReply(
            "Напишите краткое формализованное описание проблемы (до 1500 символов):",
            [_button("Назад", "i:back")],
        )
    if payload == "i:issue:continue":
        dialog = await _dialog(session, actor, "issue_new", "review")
        if dialog is None:
            return _stale()
        await update_dialog(dialog, step="attachments")
        return _preview(dialog)
    if payload == "i:issue:submit":
        dialog = await _dialog(session, actor, "issue_new", "attachments")
        if dialog is None or dialog.draft_id is None:
            return _stale()
        draft = await get_draft(session, actor, dialog.draft_id)
        if draft.revision != dialog.data.get("draft_revision"):
            raise ValueError(
                "Черновик изменился в другом окне. Откройте его из списка черновиков заново"
            )
        reply = await _send_text(session, actor, draft.id, draft.revision)
        house_id = _uuid(str(dialog.data["house_id"]))
        await clear_dialog(session, actor.id)
        return UiReply(reply, [_button("Проблемы дома", f"i:house:{house_id}")])
    if payload.startswith("i:issue:duplicate:"):
        dialog = await _dialog(session, actor, "issue_new", "review", "attachments")
        if dialog is None:
            return _stale()
        card_id = _uuid(payload.removeprefix("i:issue:duplicate:"))
        if str(card_id) not in {
            str(item["id"]) for item in dialog.data.get("suggestions", [])
        }:
            return _stale()
        draft = None
        if dialog.draft_id:
            draft = await get_draft(session, actor, dialog.draft_id)
            if draft.revision != dialog.data.get("draft_revision"):
                raise ValueError(
                    "Черновик изменился в другом окне. Откройте его заново"
                )
        if draft is not None:
            await mark_submitted(
                session, actor, draft_id=draft.id, revision=draft.revision
            )
        card, report_id = await support_card(
            session, actor, card_id, str(dialog.data["description"])
        )
        if draft is not None:
            staged_ids = (
                await session.scalars(
                    select(File.id).where(
                        File.draft_id == draft.id, File.state == "staged"
                    )
                )
            ).all()
            if staged_ids and report_id is None:
                raise RuntimeError("Создано обращение без исходного описания")
            for file_id in staged_ids:
                await attach(
                    session,
                    actor,
                    file_id=file_id,
                    parent_kind="issue_report",
                    parent_id=report_id,
                )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Ваше обращение добавлено к существующей проблеме.",
            [_button("Открыть карточку", f"i:card:{card.id}")],
        )
    if payload.startswith("i:support:"):
        card, _ = await support_card(
            session, actor, _uuid(payload.removeprefix("i:support:"))
        )
        return UiReply("Ваш голос учтён.", [_button("К карточке", f"i:card:{card.id}")])
    if payload.startswith("i:comment:"):
        card_id = _uuid(payload.removeprefix("i:comment:"))
        card = await get_visible_card(session, actor, card_id)
        await set_dialog(
            session,
            actor.id,
            flow_kind="issue_comment",
            step="text",
            data={"card_id": str(card.id)},
        )
        return UiReply(
            "Напишите сообщение для обсуждения. Фото или видео можно прислать отдельным сообщением.",
            [_button("Назад", "i:back")],
        )
    if payload.startswith("i:mute:") or payload.startswith("i:unmute:"):
        mute = payload.startswith("i:mute:")
        card_id = _uuid(payload.split(":", maxsplit=2)[2])
        await set_mute(
            session, actor, subject_kind="issue_card", subject_id=card_id, is_muted=mute
        )
        return await _card(session, actor, card_id)
    if payload.startswith("i:reopen:"):
        card_id = _uuid(payload.removeprefix("i:reopen:"))
        card = await get_visible_card(session, actor, card_id)
        if card.author_user_id != actor.id or card.status != "closed":
            raise IssueError(
                "reopen_not_allowed",
                "Переоткрыть можно только свою закрытую карточку",
                403,
            )
        await set_dialog(
            session,
            actor.id,
            flow_kind="issue_reopen",
            step="reason",
            data={"card_id": str(card.id)},
        )
        return UiReply(
            "Опишите, почему проблема не решена:", [_button("Назад", "i:back")]
        )
    if payload.startswith("i:status:"):
        return await _status_menu(
            session, actor, _uuid(payload.removeprefix("i:status:"))
        )
    if payload.startswith("i:status_choice:"):
        dialog = await _dialog(session, actor, "issue_status", "pick")
        if dialog is None:
            return _stale()
        status = payload.removeprefix("i:status_choice:")
        if status not in STATUS_LABELS:
            raise ValueError("Неизвестный статус")
        data = dict(dialog.data)
        data["status"] = status
        if status == "closed":
            await update_dialog(dialog, step="result", data=data)
            return UiReply(
                "Чем закончилось обращение?",
                [
                    _button("Проблема решена", "i:status_result:solved"),
                    _button("Обращение некорректно", "i:status_result:invalid"),
                    _button("Назад", "i:back"),
                ],
            )
        await update_dialog(dialog, step="note", data=data)
        return UiReply(
            "Напишите пояснение к статусу (или `-`, если его нет):",
            [_button("Назад", "i:back")],
        )
    if payload.startswith("i:status_result:"):
        dialog = await _dialog(session, actor, "issue_status", "result")
        if dialog is None:
            return _stale()
        result = payload.removeprefix("i:status_result:")
        if result not in {"solved", "invalid"}:
            raise ValueError("Неизвестный результат")
        data = dict(dialog.data)
        data["close_result"] = result
        await update_dialog(dialog, step="note", data=data)
        return UiReply(
            "Напишите обязательное пояснение к закрытию:", [_button("Назад", "i:back")]
        )
    if payload.startswith("i:edit:"):
        card = await get_visible_card(
            session, actor, _uuid(payload.removeprefix("i:edit:"))
        )
        if not await _staff_can_manage(session, actor, card.house_id):
            raise IssueError(
                "issue_permission_denied", "Нет права редактировать карточку", 403
            )
        await set_dialog(
            session,
            actor.id,
            flow_kind="issue_edit",
            step="title",
            data={
                "card_id": str(card.id),
                "version": card.version,
            },
        )
        return UiReply(
            f"Текущее название: {card.title}\nНапишите новое название:",
            [_button("Назад", "i:back")],
        )
    if payload.startswith("i:merge_suggest:"):
        return await _merge_suggestions(
            session, actor, _uuid(payload.removeprefix("i:merge_suggest:"))
        )
    if payload.startswith("i:merge:"):
        return await _merge_menu(
            session, actor, _uuid(payload.removeprefix("i:merge:"))
        )
    if payload.startswith("i:merge_with:"):
        dialog = await _dialog(session, actor, "issue_merge", "pick")
        if dialog is None:
            return _stale()
        left = await get_visible_card(
            session, actor, _uuid(str(dialog.data["card_id"]))
        )
        if not await _staff_can_manage(session, actor, left.house_id):
            raise IssueError("issue_permission_denied", "Нет права объединять карточки", 403)
        right = await get_visible_card(
            session, actor, _uuid(payload.removeprefix("i:merge_with:"))
        )
        if (
            left.id == right.id
            or left.house_id != right.house_id
            or right.status not in ACTIVE_STATUSES
        ):
            raise ValueError(
                "Объединить можно только разные открытые проблемы одного дома"
            )
        data = dict(dialog.data)
        data["other_id"] = str(right.id)
        await update_dialog(dialog, step="confirm", data=data)
        return UiReply(
            f"Объединить «{left.title}» и «{right.title}»?\n"
            f"Первая: {left.summary_description[:250]}\n"
            f"Вторая: {right.summary_description[:250]}\n"
            "Проверьте, что это одна и та же проблема. Это изменит обе карточки.",
            [
                _button("Подтвердить объединение", "i:merge_confirm"),
                _button("Назад", "i:back"),
            ],
        )
    if payload == "i:merge_confirm":
        dialog = await _dialog(session, actor, "issue_merge", "confirm")
        if dialog is None:
            return _stale()
        left = await get_visible_card(
            session, actor, _uuid(str(dialog.data["card_id"]))
        )
        result = await merge_cards(
            session,
            actor,
            left.id,
            _uuid(str(dialog.data["other_id"])),
            final_title=left.title,
            final_status=left.status,
            final_note=left.current_note,
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Карточки объединены.",
            [_button("Открыть результат", f"i:card:{result.id}")],
        )
    return _stale()


async def handle_action(
    session: AsyncSession, actor: User, payload: str
) -> UiReply | None:
    """Handle issue button callbacks; unknown namespaces belong to other modules."""
    if not payload.startswith("i:"):
        return None
    try:
        return await _handle_action(session, actor, payload)
    except (
        IssueError,
        DraftError,
        NotificationError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        await session.rollback()
        return UiReply(
            f"Не получилось: {error}", [_button("Проблемы домов", "i:houses")]
        )


async def _handle_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply | None:
    if dialog.flow_kind == "issue_new":
        if dialog.step == "description":
            description = text.strip()
            if not 1 <= len(description) <= 1500:
                raise ValueError("Описание должно содержать от 1 до 1500 символов")
            data = dict(dialog.data)
            data.pop("_editing_description", None)
            data["description"] = description
            data.pop("title", None)
            data.pop("summary_description", None)
            data.pop("_summary_fallback", None)
            await update_dialog(dialog, step="scope", data=data)
            return await _scope_options(session, actor, dialog)
        if dialog.step == "scope":
            return await _scope_options(session, actor, dialog)
        if dialog.step == "scope_location":
            return await _location_options(
                session, actor, dialog, str(dialog.data["selected_scope"])
            )
        if dialog.step == "edit_title":
            title = " ".join(text.split())
            if not 1 <= len(title) <= 100:
                raise ValueError("Название должно содержать от 1 до 100 символов")
            data = dict(dialog.data)
            data["title"] = title
            draft = await get_draft(session, actor, dialog.draft_id)
            if draft.revision != data.get("draft_revision"):
                raise ValueError(
                    "Черновик изменился в другом окне. Откройте его заново"
                )
            payload = dict(draft.payload)
            payload["title"] = title
            saved = await save_draft(
                session,
                actor,
                flow_kind="issue_card",
                payload=payload,
                draft_id=draft.id,
                revision=draft.revision,
            )
            data["draft_revision"] = saved.revision
            await update_dialog(dialog, step="review", data=data)
            return _preview(dialog)
        if dialog.step == "edit_summary":
            summary = _summary_text(text)
            if summary is None or len(summary) > 1500:
                raise ValueError(
                    "Краткое описание должно содержать от 1 до 1500 символов"
                )
            data = dict(dialog.data)
            draft = await get_draft(session, actor, dialog.draft_id)
            if draft.revision != data.get("draft_revision"):
                raise ValueError(
                    "Черновик изменился в другом окне. Откройте его заново"
                )
            saved = await save_draft(
                session,
                actor,
                flow_kind="issue_card",
                payload={**draft.payload, "summary_description": summary},
                draft_id=draft.id,
                revision=draft.revision,
            )
            data["summary_description"] = summary
            data["_summary_fallback"] = False
            data["draft_revision"] = saved.revision
            await update_dialog(dialog, step="attachments", data=data)
            return _preview(dialog)
        if dialog.step in {"review", "attachments"}:
            return _preview(dialog)
    if dialog.flow_kind == "issue_comment" and dialog.step == "text":
        card_id = _uuid(str(dialog.data["card_id"]))
        await add_comment(session, actor, card_id, text)
        await clear_dialog(session, actor.id)
        return UiReply(
            "Сообщение добавлено в обсуждение.",
            [_button("К карточке", f"i:card:{card_id}")],
        )
    if dialog.flow_kind == "issue_reopen" and dialog.step == "reason":
        card_id = _uuid(str(dialog.data["card_id"]))
        await reopen_card(session, actor, card_id, text)
        await clear_dialog(session, actor.id)
        return UiReply(
            "Карточка переоткрыта.", [_button("К карточке", f"i:card:{card_id}")]
        )
    if dialog.flow_kind == "issue_status" and dialog.step == "note":
        data = dialog.data
        card_id = _uuid(str(data["card_id"]))
        note = text.strip()
        if data["status"] == "closed" and note in {"", "-"}:
            raise ValueError("Для закрытия нужно пояснение")
        await set_status(
            session,
            actor,
            card_id,
            status=str(data["status"]),
            note=None if note == "-" else note,
            close_result=(
                str(data["close_result"]) if data.get("close_result") else None
            ),
        )
        await clear_dialog(session, actor.id)
        return UiReply("Статус обновлён.", [_button("К карточке", f"i:card:{card_id}")])
    if dialog.flow_kind == "issue_edit" and dialog.step == "title":
        data = dialog.data
        card_id = _uuid(str(data["card_id"]))
        card = await get_visible_card(session, actor, card_id)
        title = " ".join(text.split())
        if not 1 <= len(title) <= 100:
            raise ValueError("Название должно содержать от 1 до 100 символов")
        targets = (
            await session.execute(
                select(IssueTarget, Apartment)
                .outerjoin(Apartment, Apartment.id == IssueTarget.apartment_id)
                .where(IssueTarget.card_id == card.id)
            )
        ).all()
        await edit_card(
            session,
            actor,
            card.id,
            expected_version=int(data["version"]),
            category_id=card.category_id,
            title=title,
            scope_all_house=card.scope_all_house,
            target_entrances=[
                target.entrance_number
                for target, _ in targets
                if target.entrance_number is not None
            ],
            target_apartments=[
                (apartment.entrance_number, apartment.apartment_number)
                for target, apartment in targets
                if target.apartment_id is not None and apartment is not None
            ],
        )
        await clear_dialog(session, actor.id)
        return UiReply(
            "Название обновлено.", [_button("К карточке", f"i:card:{card_id}")]
        )
    return None


async def handle_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply | None:
    """Consume plain messages only while an issue dialog expects text."""
    if not dialog.flow_kind.startswith("issue_"):
        return None
    try:
        return await _handle_text(session, actor, dialog, text)
    except (IssueError, DraftError, ValueError, KeyError, TypeError) as error:
        await session.rollback()
        return UiReply(
            f"Не получилось: {error}. Попробуйте ещё раз или вернитесь назад.",
            [_button("Назад", "i:back")],
        )
