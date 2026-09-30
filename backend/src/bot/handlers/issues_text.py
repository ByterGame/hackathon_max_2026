"""Text commands for issue cards, backed by the same domain as the mini-app."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Apartment,
    AuditEvent,
    IssueCard,
    IssueCategory,
    IssueMessage,
    IssueReport,
    IssueSupport,
    IssueTarget,
    House,
    User,
)
from src.domain.issues.service import (
    IssueError,
    _active_resident_apartments,
    add_comment,
    create_card,
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
from src.domain.issues.staff_suggest import StaffMergeSuggestion, suggest_staff_merges

ISSUE_HELP = (
    "Проблемы дома:\n"
    "/categories — категории; /issues UUID_дома [страница] — видимые проблемы; "
    "/issue UUID_карточки [страница] — карточка и обсуждение; "
    "/history UUID_карточки [страница] — история.\n"
    "/newissue UUID_дома | код_категории | область | название | описание — сообщить о проблеме. "
    "/suggest UUID_дома | код_категории | область | описание — предложить название и дубли. "
    "Для жильца область: дом, квартира или подъезд. Если привязано несколько квартир, "
    "укажите квартира:НОМЕР или подъезд:НОМЕР своего подъезда.\n"
    "/support UUID_карточки | дополнение — поддержать (дополнение необязательно); "
    "/comment UUID_карточки | текст — написать в обсуждение; "
    "/filehelp — прикрепить фото, PDF или видео к карточке либо черновику; "
    "/reopen UUID_карточки | причина — переоткрыть свою закрытую карточку.\n"
    "Сотруднику УК: /status UUID | статус | пояснение | результат — сменить статус "
    "(результат solved/invalid нужен только для closed); "
    "/edit UUID | версия | категория | область | название — изменить сводку; "
    "/merge_suggest UUID_карточки — найти похожие карточки для объединения; "
    "/merge UUID UUID | статус | название | пояснение — объединить дубли."
)

ISSUE_PAGE_SIZE = 18
ISSUE_TEXT_PAGE_CHARS = 3000


def _page_number(value: str) -> int:
    if not value or len(value) > 7 or not value.isascii() or not value.isdecimal():
        raise ValueError("Номер страницы должен быть положительным целым числом.")
    page = int(value)
    if page < 1:
        raise ValueError("Номер страницы должен быть положительным целым числом.")
    return page


def _page_bounds(total: int, requested: int, size: int) -> tuple[int, int, slice]:
    pages = max(1, (total + size - 1) // size)
    page = min(requested, pages)
    start = (page - 1) * size
    return page, pages, slice(start, start + size)


def _target_and_page(argument: str) -> tuple[UUID, int]:
    parts = argument.split()
    if len(parts) not in {1, 2}:
        raise ValueError("Укажите UUID и необязательный номер страницы.")
    return _uuid(parts[0]), _page_number(parts[1]) if len(parts) == 2 else 1


def _text_chunks(body: str) -> list[str]:
    if not body:
        return [""]
    chunks = []
    position = 0
    while position < len(body):
        end = min(position + ISSUE_TEXT_PAGE_CHARS, len(body))
        if end < len(body):
            line_end = body.rfind("\n", position + ISSUE_TEXT_PAGE_CHARS // 2, end + 1)
            if line_end >= 0:
                end = line_end + 1
        chunks.append(body[position:end])
        position = end
    return chunks


def _paged_text(body: str, requested: int, command: str) -> str:
    chunks = _text_chunks(body)
    page = min(requested, len(chunks))
    navigation = []
    if page > 1:
        navigation.append(f"Назад: {command} {page - 1}")
    if page < len(chunks):
        navigation.append(f"Далее: {command} {page + 1}")
    return f"Страница {page}/{len(chunks)}\n{chunks[page - 1]}" + (
        "\n" + " · ".join(navigation) if navigation else ""
    )


def _participant_label(
    actor_id: UUID, author_id: UUID, *, official: bool = False
) -> str:
    if actor_id == author_id:
        return "Вы"
    return "УК" if official else "Жилец"


def _parts(value: str, *, count: int) -> list[str]:
    parts = [part.strip() for part in value.split("|", maxsplit=count - 1)]
    if len(parts) < count or any(not part for part in parts[: count - 1]):
        raise ValueError("Не хватает полей команды. Отправьте /issuehelp для примера.")
    return parts


def _uuid(value: str) -> UUID:
    try:
        return UUID(value.strip())
    except ValueError as error:
        raise ValueError("Нужен UUID из карточки или списка.") from error


def _scope(value: str) -> tuple[bool, list[int], list[tuple[int | None, int]]]:
    """Legacy multi-target syntax remains only for employee card edits."""
    scope = value.strip().lower()
    if scope in {"all", "дом", "весь дом"}:
        return True, [], []
    entrances: set[int] = set()
    apartments: dict[int, int | None] = {}
    sections = scope.split("+")
    if len(sections) > 2 or len(sections) != len(set(part[:2] for part in sections)):
        raise ValueError("Область: all, e:1,2, a:12 или e:1+a:18")
    for section in sections:
        if section.startswith("e:"):
            try:
                entrances.update(int(item.strip()) for item in section[2:].split(","))
            except ValueError as error:
                raise ValueError("Область подъездов: e:1,2") from error
        elif section.startswith("a:"):
            try:
                for item in section[2:].split(","):
                    numbers = [int(part) for part in item.strip().split(":")]
                    if len(numbers) == 1:
                        entrance, apartment = None, numbers[0]
                    elif len(numbers) == 2:
                        entrance, apartment = numbers
                    else:
                        raise ValueError("Неверное число частей")
                    if apartment <= 0 or (entrance is not None and entrance <= 0):
                        raise ValueError("Номер не положительный")
                    previous = apartments.get(apartment)
                    if previous is not None and entrance is not None and previous != entrance:
                        raise ValueError("Противоречивые подъезды")
                    apartments[apartment] = entrance if entrance is not None else previous
            except ValueError as error:
                raise ValueError("Область квартир: a:12,18") from error
        else:
            raise ValueError("Область: all, e:1,2, a:12 или e:1+a:18")
    if any(number <= 0 for number in entrances):
        raise ValueError("Номера подъездов и квартир должны быть положительными")
    if not entrances and not apartments:
        raise ValueError("Укажите хотя бы один подъезд или квартиру")
    return False, sorted(entrances), [
        (entrance, apartment) for apartment, entrance in sorted(apartments.items())
    ]


async def _resident_scope(
    session: AsyncSession, actor: User, house_id: UUID, value: str
) -> tuple[str, UUID | None]:
    """Resolve a resident's textual scope only through their active grants."""
    raw = value.strip().casefold()
    if raw in {"дом", "весь дом", "house", "all"}:
        return "house", None
    kind, separator, selected = raw.partition(":")
    scope = {
        "квартира": "apartment",
        "apartment": "apartment",
        "a": "apartment",
        "подъезд": "entrance",
        "entrance": "entrance",
        "e": "entrance",
    }.get(kind)
    if scope is None:
        raise ValueError("Область жильца: дом, квартира или подъезд; чужие номера указать нельзя")
    if separator and (not selected.isascii() or not selected.isdecimal() or int(selected) < 1):
        raise ValueError("После двоеточия укажите номер своей квартиры или подъезда")
    house = await session.get(House, house_id)
    if house is None or house.archived_at is not None:
        raise IssueError("house_not_found", "Дом не найден", 404)
    apartments = await _active_resident_apartments(session, actor, house)
    if not apartments:
        raise IssueError("house_access_denied", "Нужен действующий доступ к дому", 403)
    if separator:
        number = int(selected)
        matching = [
            apartment
            for apartment in apartments.values()
            if (apartment.apartment_number if scope == "apartment" else apartment.entrance_number)
            == number
        ]
        if not matching:
            raise IssueError(
                "location_access_denied",
                "Этот номер не относится к вашей квартире или подъезду",
                403,
            )
        return scope, matching[0].id
    if len(apartments) == 1:
        return scope, next(iter(apartments))
    if scope == "entrance":
        known = {apartment.entrance_number for apartment in apartments.values()}
        if len(known) == 1 and None not in known:
            return scope, next(iter(apartments))
    raise ValueError(
        "К этому дому привязано несколько квартир. Укажите квартира:НОМЕР или подъезд:НОМЕР из своих привязок"
    )


async def _category(session: AsyncSession, code: str) -> IssueCategory:
    category = await session.scalar(
        select(IssueCategory).where(IssueCategory.code == code.strip().lower())
    )
    if category is None or not category.is_active:
        raise ValueError("Категория не найдена. Список: /categories")
    return category


async def _card_text(
    session: AsyncSession, actor: User, card_id: UUID, page: int = 1
) -> str:
    card = await get_visible_card(session, actor, card_id)
    category = await session.get(IssueCategory, card.category_id)
    support_count = await session.scalar(
        select(func.count())
        .select_from(IssueSupport)
        .where(IssueSupport.card_id == card.id)
    )
    targets = (
        await session.execute(
            select(IssueTarget, Apartment)
            .outerjoin(Apartment, Apartment.id == IssueTarget.apartment_id)
            .where(IssueTarget.card_id == card.id)
        )
    ).all()
    if card.scope_all_house:
        area = "весь дом"
    else:
        area = ", ".join(
            (
                f"подъезд {target.entrance_number}"
                if target.entrance_number is not None
                else f"квартира {apartment.apartment_number}"
            )
            for target, apartment in targets
            if target.entrance_number is not None or apartment is not None
        )
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
    lines = [
        f"{card.title} ({card.status})",
        f"ID: {card.id}",
        f"Категория: {category.name if category else card.category_id}",
        f"Область: {area}; поддержали: {support_count or 0}; версия: {card.version}",
    ]
    if summary := getattr(card, "summary_description", None):
        lines.append(f"Краткое формализованное описание: {summary}")
    if card.current_note:
        lines.append(f"Пояснение УК: {card.current_note}")
    if card.close_result:
        lines.append(f"Результат закрытия: {card.close_result}")
    lines.extend(
        f"{_participant_label(actor.id, report.author_user_id)}: "
        f"{report.raw_description}"
        for report in reports
    )
    lines.extend(
        f"{_participant_label(actor.id, message.author_user_id, official=message.kind == 'official_uk')}: "
        f"{message.body}"
        for message in messages
    )
    return _paged_text("\n".join(lines), page, f"/issue {card.id}")


async def _staff_merge_candidates(
    session: AsyncSession, actor: User, card_id: UUID
) -> tuple[StaffMergeSuggestion, list[IssueCard]]:
    """Apply the domain permission check before reading suggested card details."""
    result = await suggest_staff_merges(session, actor, card_id)
    if not result.similar_card_ids:
        return result, []
    source = await session.get(IssueCard, card_id)
    if source is None:
        raise IssueError("issue_not_found", "Проблема не найдена", 404)
    candidates = (
        await session.scalars(
            select(IssueCard).where(
                IssueCard.id.in_(result.similar_card_ids),
                IssueCard.house_id == source.house_id,
                IssueCard.status != "closed",
                IssueCard.merged_into_id.is_(None),
            )
        )
    ).all()
    by_id = {card.id: card for card in candidates}
    return result, [by_id[card_id] for card_id in result.similar_card_ids if card_id in by_id]


async def handle_issue_text(
    session: AsyncSession, actor: User, text: str
) -> str | None:
    """Return a reply, or None when another command family should handle it."""
    command, _, argument = text.strip().partition(" ")
    command = command.lower()
    if command not in {
        "/issuehelp",
        "/categories",
        "/issues",
        "/issue",
        "/history",
        "/newissue",
        "/suggest",
        "/support",
        "/comment",
        "/status",
        "/reopen",
        "/merge",
        "/merge_suggest",
        "/edit",
    }:
        return None
    if command == "/issuehelp":
        return ISSUE_HELP
    try:
        if command == "/categories":
            categories = (
                await session.scalars(
                    select(IssueCategory)
                    .where(IssueCategory.is_active.is_(True))
                    .order_by(IssueCategory.sort_order, IssueCategory.name)
                )
            ).all()
            return "Категории:\n" + "\n".join(
                f"{item.code} — {item.name}" for item in categories
            )
        if command == "/issues":
            house_id, requested_page = _target_and_page(argument)
            cards = await list_visible_cards(
                session, actor, house_id, include_closed=True
            )
            if not cards:
                return "У вас пока нет видимых проблем в этом доме."
            page, pages, selected = _page_bounds(
                len(cards), requested_page, ISSUE_PAGE_SIZE
            )
            lines = [f"Проблемы дома · страница {page}/{pages} · всего {len(cards)}:"]
            lines.extend(
                f"{card.id} — {' '.join(card.title.split())[:70]} [{card.status}]"
                for card in cards[selected]
            )
            if page > 1:
                lines.append(f"Назад: /issues {house_id} {page - 1}")
            if page < pages:
                lines.append(f"Далее: /issues {house_id} {page + 1}")
            return "\n".join(lines)
        if command == "/issue":
            card_id, page = _target_and_page(argument)
            return await _card_text(session, actor, card_id, page)
        if command == "/history":
            card_id, page = _target_and_page(argument)
            card = await get_visible_card(session, actor, card_id)
            rows = (
                await session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.entity_kind == "issue_card",
                        AuditEvent.entity_id.in_(
                            await merged_card_ids(session, card.id)
                        ),
                        AuditEvent.actor_user_id.is_not(None),
                    )
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            ).all()
            rows = [row for row in rows if row.action != "comment_added"]
            if not rows:
                return "История этой карточки пока пуста."
            lines = [f"История карточки {card.id}:"]
            lines.extend(
                f"{row.created_at:%d.%m %H:%M} · {row.action} · "
                f"{'Вы' if row.actor_user_id == actor.id else 'Участник'}"
                for row in rows
            )
            return _paged_text("\n".join(lines), page, f"/history {card.id}")
        if command == "/suggest":
            house, category_code, area, description = _parts(argument, count=4)
            house_id = _uuid(house)
            scope, apartment_id = await _resident_scope(session, actor, house_id, area)
            category_id = None
            if category_code != "-":
                category_id = (await _category(session, category_code)).id
            result = await suggest_issue(
                session,
                actor,
                house_id=house_id,
                description=description,
                category_id=category_id,
                scope=scope,
                apartment_id=apartment_id,
            )
            lines = [f"Предложенное название: {result.suggested_title}"]
            summary = getattr(result, "summary_description", None)
            lines.append(
                "Краткое формализованное описание: "
                + (summary or description)
            )
            if not summary:
                lines.append(
                    "Автоматическая сводка недоступна: пока используется исходное описание."
                )
            if result.source == "gigachat":
                lines.append("GigaChat обработал описание и поискал похожие проблемы.")
                if result.description_check == "warning":
                    lines.append(
                        "Стоит уточнить описание: "
                        + (result.description_warning or "Что именно случилось и где?")
                    )
                else:
                    lines.append("Описание достаточно понятно для подачи заявки.")
            else:
                lines.append(
                    "Нейросеть не использовалась: название взято из первой фразы, "
                    "а похожие карточки найдены по совпадению слов."
                )
            if result.similar_card_ids:
                lines.append("Возможные дубли (проверьте сами):")
                candidate_titles = {item.id: item.title for item in result.candidates}
                lines.extend(
                    f"{card_id} — {candidate_titles[card_id]}"
                    for card_id in result.similar_card_ids
                )
            else:
                lines.append(
                    "Автоматически подходящих дублей не найдено. Посмотрите /issues перед созданием."
                )
            lines.append(
                "Если проблема совпадает, отправьте /support; иначе /newissue."
            )
            return "\n".join(lines)
        if command == "/newissue":
            house, category_code, area, title, description = _parts(argument, count=5)
            house_id = _uuid(house)
            scope, apartment_id = await _resident_scope(session, actor, house_id, area)
            category = await _category(session, category_code)
            card = await create_card(
                session,
                actor,
                house_id=house_id,
                category_id=category.id,
                title=title,
                description=description,
                summary_description=description,
                scope=scope,
                apartment_id=apartment_id,
            )
            await session.commit()
            return f"Проблема создана: {card.title}\nID: {card.id}"
        if command == "/support":
            card_id, _, description = argument.partition("|")
            card, _ = await support_card(
                session, actor, _uuid(card_id), description.strip() or None
            )
            await session.commit()
            return f"Поддержка учтена: {card.title}. ID: {card.id}"
        if command == "/comment":
            card_id, body = _parts(argument, count=2)
            message = await add_comment(session, actor, _uuid(card_id), body)
            await session.commit()
            return f"Сообщение добавлено. ID: {message.id}"
        if command == "/status":
            parts = _parts(argument, count=3)
            card_id = _uuid(parts[0])
            status = parts[1]
            note, _, close_result = parts[2].partition("|")
            card = await set_status(
                session,
                actor,
                card_id,
                status=status,
                note=note.strip() or None,
                close_result=close_result.strip() or None,
            )
            await session.commit()
            return f"Статус изменён: {card.status}. ID: {card.id}"
        if command == "/reopen":
            card_id, comment = _parts(argument, count=2)
            card = await reopen_card(session, actor, _uuid(card_id), comment)
            await session.commit()
            return f"Карточка переоткрыта: {card.id}"
        if command == "/merge_suggest":
            card_id = _uuid(argument)
            result, candidates = await _staff_merge_candidates(session, actor, card_id)
            source = "GigaChat" if result.source == "gigachat" else "локальный поиск без нейросети"
            lines = [f"Похожие карточки для объединения ({source}):"]
            if candidates:
                lines.extend(f"{card.id} — {card.title}" for card in candidates)
                lines.append("Проверьте карточки командой /issue и объедините вручную через /merge.")
            else:
                lines.append("Похожих открытых карточек не найдено. Можно проверить /issues вручную.")
            return "\n".join(lines)
        if command == "/merge":
            ids, status, title, note = _parts(argument, count=4)
            two_ids = ids.split()
            if len(two_ids) != 2:
                raise ValueError("Укажите два UUID перед первой вертикальной чертой.")
            card = await merge_cards(
                session,
                actor,
                _uuid(two_ids[0]),
                _uuid(two_ids[1]),
                final_status=status,
                final_title=title,
                final_note=note or None,
            )
            await session.commit()
            return f"Карточки объединены. Основная: {card.id}"
        if command == "/edit":
            card_id, version, category_code, scope, title = _parts(argument, count=5)
            all_house, entrances, apartments = _scope(scope)
            category = await _category(session, category_code)
            card = await edit_card(
                session,
                actor,
                _uuid(card_id),
                expected_version=int(version),
                category_id=category.id,
                title=title,
                scope_all_house=all_house,
                target_entrances=entrances,
                target_apartments=apartments,
            )
            await session.commit()
            return f"Сводка изменена: {card.title}. Версия: {card.version}"
    except (IssueError, ValueError) as error:
        await session.rollback()
        return f"Не получилось: {error}"
    return None
