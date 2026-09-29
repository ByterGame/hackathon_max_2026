"""MAX polling adapter for button-driven dialogs and text shortcuts."""

import asyncio
import hashlib
import logging
from uuid import UUID, uuid4

from maxapi import Router
from maxapi.enums import ChatType
from maxapi.types import BotStarted, MessageCallback, MessageCreated
from maxapi.types.attachments import Contact
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.diagnostics import (
    UpdateTrace,
    callback_event_type,
    message_event_type,
    safe_stack,
)
from src.bot.handlers import admin_ui
from src.bot.handlers.access_text import handle_access_text
from src.bot.handlers.access_ui import handle_action as handle_access_action
from src.bot.handlers.access_ui import handle_text as handle_access_input
from src.bot.handlers.drafts_text import handle_draft_text
from src.bot.handlers.issues_text import handle_issue_text
from src.bot.handlers.issues_ui import handle_action as handle_issue_action
from src.bot.handlers.issues_ui import handle_text as handle_issue_input
from src.bot.handlers.media_text import (
    get_card_attachment,
    handle_media_text,
    list_card_attachments,
)
from src.bot.handlers.notifications_text import handle_notification_text
from src.bot.ui import Button, UiReply, clear_dialog, get_dialog, keyboard_for
from src.common.auth import (
    get_or_create_user,
    link_verified_phone,
    max_profile_name,
    validate_bot_contact,
)
from src.core.logging import current_request_id
from src.db.models import BotDialog, CommandReceipt, User
from src.domain.access.common import bind_staff_by_verified_phone
from src.domain.access.rules import AccessRuleError
from src.domain.drafts.service import DraftError, list_drafts
from src.domain.files.storage import FileError, remove_failed_upload, storage_root
from src.domain.issues.service import IssueError
from src.domain.notifications.service import (
    NotificationError,
    list_notifications,
    mark_read,
)

logger = logging.getLogger(__name__)

GENERAL_HELP = (
    "Выберите действие кнопками под сообщением. На вопрос бота отвечайте обычным текстом. "
    "В любой момент можно отправить /cancel или нажать «Главное меню».\n"
    "Текстовые команды тоже доступны: /access help, /issuehelp, /drafthelp, "
    "/notificationhelp, /filehelp, /files UUID_карточки, "
    "/getfile UUID_карточки UUID_файла. /whoami показывает ваш MAX ID. "
    "Администратору доступна команда /admin."
)


async def _reserve_message(
    session: AsyncSession, *, user_id, message_id: str, content: str
) -> bool:
    receipt = await session.scalar(
        select(CommandReceipt.id).where(
            CommandReceipt.source == "max_bot",
            CommandReceipt.actor_user_id == user_id,
            CommandReceipt.external_key == message_id,
        )
    )
    if receipt is not None:
        return False
    session.add(
        CommandReceipt(
            id=uuid4(),
            source="max_bot",
            external_key=message_id,
            actor_user_id=user_id,
            operation="message_created",
            request_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False
    return True


def _home(actor: User) -> UiReply:
    if actor.kind == "admin":
        return admin_ui.menu()
    if actor.kind == "support":
        return UiReply(
            "Кабинет поддержки. Выберите, с чем работать:",
            [
                [Button("Обращения УК и домов", "a:support")],
                [Button("Моё ФИО", "a:profile")],
                [Button("Уведомления", "n:list")],
            ],
        )
    if actor.phone_verified_at is None:
        return UiReply(
            "Для заявок нужен номер вашего аккаунта MAX. Нажмите «Поделиться номером» — "
            "бот проверит его и покажет доступные действия.",
            request_contact=True,
        )
    if actor.kind == "employee":
        return UiReply(
            "Кабинет сотрудника УК. Выберите раздел:",
            [
                [Button("Дома и проблемы", "a:staff_houses")],
                [Button("Заявки жильцов", "a:staff_requests")],
                [Button("Сотрудники и права", "a:staff")],
                [Button("Моё ФИО", "a:profile")],
                [Button("Черновики", "d:list"), Button("Уведомления", "n:list")],
            ],
        )
    if actor.kind == "resident":
        return UiReply(
            "Что хотите сделать?",
            [
                [Button("Сообщить о проблеме", "i:new")],
                [Button("Проблемы моего дома", "i:houses")],
                [
                    Button("Мои дома", "a:grants"),
                    Button("Заявки на доступ", "a:requests:resident"),
                ],
                [Button("Предложения доступа", "a:offers")],
                [Button("Моё ФИО", "a:profile")],
                [Button("Черновики", "d:list"), Button("Уведомления", "n:list")],
            ],
        )
    return UiReply(
        "Добро пожаловать. Начните с подключения к дому или регистрации УК.",
        [
            [Button("Приглашения поддержки", "adm:invites")],
            [Button("Подключиться к дому", "a:apply")],
            [Button("Мои заявки", "a:requests:resident")],
            [Button("Мои обращения УК", "a:requests:company_registration")],
            [Button("Мои заявки на дома", "a:requests:house_addition")],
            [Button("Предложения доступа", "a:offers")],
            [Button("Зарегистрировать УК", "a:register_company")],
            [Button("Моё ФИО", "a:profile")],
            [Button("Черновики", "d:list"), Button("Уведомления", "n:list")],
        ],
    )


async def _draft_list(session: AsyncSession, actor: User) -> UiReply:
    drafts = [
        draft
        for draft in await list_drafts(session, actor)
        if draft.submitted_at is None
    ]
    if not drafts:
        return UiReply("Незавершённых черновиков нет.")
    buttons: list[list[Button]] = []
    for draft in drafts[:12]:
        if draft.flow_kind == "resident_request":
            label, payload = "Заявка на доступ", f"a:resume:{draft.id}"
        elif draft.flow_kind == "issue_card":
            label, payload = "Проблема дома", f"i:resume:{draft.id}"
        else:
            continue
        buttons.append([Button(f"{label} · {draft.updated_at:%d.%m %H:%M}", payload)])
    return UiReply("Незавершённые черновики. Нажмите, чтобы продолжить:", buttons)


async def _notification_action(
    session: AsyncSession, actor: User, payload: str
) -> UiReply:
    parts = payload.split(":")
    if len(parts) in {3, 4} and parts[:2] == ["n", "read"]:
        page = _notification_page(parts[3]) if len(parts) == 4 else 0
        await mark_read(session, actor, UUID(parts[2]))
        return await _notification_action(session, actor, f"n:list:{page}")
    if len(parts) not in {2, 3} or parts[:2] != ["n", "list"]:
        return UiReply("Неизвестное действие с уведомлением.")
    page = _notification_page(parts[2]) if len(parts) == 3 else 0
    rows = await list_notifications(session, actor)
    if not rows:
        return UiReply("Уведомлений пока нет.")
    page_size = 8
    start = page * page_size
    selected = rows[start : start + page_size]
    if not selected:
        return UiReply(
            "На этой странице уведомлений нет.",
            [[Button("К началу", "n:list")]],
        )
    total_pages = (len(rows) + page_size - 1) // page_size
    lines = [f"События · страница {page + 1}/{total_pages}:"]
    buttons: list[list[Button]] = []
    for index, (notification, _event) in enumerate(selected, start=start + 1):
        targets = {
            "issue_card": ("Проблема дома", f"i:card:{notification.subject_id}"),
            "resident_request": (
                "Заявка на доступ",
                f"a:request:resident:{notification.subject_id}",
            ),
            "company_registration_request": (
                "Регистрация УК",
                f"a:request:company_registration:{notification.subject_id}",
            ),
            "house_addition_request": (
                "Подключение дома",
                f"a:request:house_addition:{notification.subject_id}",
            ),
            "resident_offer": (
                "Предложение доступа",
                (
                    "a:staff_houses"
                    if actor.kind == "employee"
                    else f"a:offer:{notification.subject_id}"
                ),
            ),
            "resident_grant": (
                "Доступ к дому",
                "a:staff_houses" if actor.kind == "employee" else "a:grants",
            ),
            "staff_assignment": ("Назначение сотрудника", "a:staff_houses"),
        }
        subject, target = targets.get(notification.subject_kind, ("Событие", None))
        mark = "●" if notification.read_at is None else "✓"
        lines.append(
            f"{index}. {mark} {subject} · {notification.created_at:%d.%m %H:%M}"
        )
        if target is not None:
            buttons.append([Button(f"Открыть {index}", target)])
        if notification.read_at is None:
            buttons.append(
                [Button(f"Прочитано {index}", f"n:read:{notification.id}:{page}")]
            )
    navigation: list[Button] = []
    if page > 0:
        navigation.append(Button("Назад", f"n:list:{page - 1}"))
    if start + len(selected) < len(rows):
        navigation.append(Button("Дальше", f"n:list:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    return UiReply("\n".join(lines), buttons)


def _notification_page(raw: str) -> int:
    try:
        page = int(raw)
    except ValueError as error:
        raise ValueError("Некорректная страница уведомлений") from error
    if page < 0 or page > 1000:
        raise ValueError("Некорректная страница уведомлений")
    return page


async def _handle_action(session: AsyncSession, actor: User, payload: str) -> UiReply:
    if payload == "menu":
        await clear_dialog(session, actor.id)
        return _home(actor)
    if payload == "d:list":
        await clear_dialog(session, actor.id)
        return await _draft_list(session, actor)
    if payload.startswith("n:"):
        await clear_dialog(session, actor.id)
        return await _notification_action(session, actor, payload)
    if payload.startswith("adm:"):
        result = await admin_ui.handle_action(session, actor, payload)
        return result or UiReply("Кнопка больше не действует. Откройте кабинет заново.")
    if payload.startswith("f:"):
        await clear_dialog(session, actor.id)
        parts = payload.split(":")
        if len(parts) in {3, 4} and parts[1] == "card":
            page = int(parts[3]) if len(parts) == 4 else 0
            return await list_card_attachments(
                session, actor, UUID(parts[2]), page=page
            )
        if len(parts) == 4 and parts[1] == "get":
            return await get_card_attachment(
                session, actor, UUID(parts[2]), UUID(parts[3])
            )
        return UiReply("Кнопка вложения устарела. Откройте карточку заново.")
    if payload.startswith("a:"):
        result = await handle_access_action(session, actor, payload)
    elif payload.startswith("i:"):
        result = await handle_issue_action(session, actor, payload)
    else:
        result = None
    return result or UiReply(
        "Кнопка больше не действует. Откройте нужный раздел из меню."
    )


async def _handle_dialog_text(
    session: AsyncSession, actor: User, dialog: BotDialog, text: str
) -> UiReply:
    if dialog.flow_kind.startswith("access_"):
        result = await handle_access_input(session, actor, dialog, text)
    elif dialog.flow_kind.startswith("issue_"):
        result = await handle_issue_input(session, actor, dialog, text)
    elif dialog.flow_kind.startswith("admin_"):
        result = await admin_ui.handle_text(session, actor, dialog, text)
    else:
        result = None
    return result or UiReply(
        "Не удалось определить текущий шаг. Вернитесь в главное меню."
    )


def build_router(
    session_factory: async_sessionmaker[AsyncSession], bot_token: str
) -> Router:
    router = Router()

    def failure(error: Exception) -> UiReply:
        message = getattr(error, "message", str(error))
        return UiReply(f"Не получилось: {message}")

    def cleanup_upload(storage_key: str | None, trace: UpdateTrace) -> None:
        if storage_key is None:
            return
        try:
            remove_failed_upload(storage_root(), storage_key)
        except (OSError, RuntimeError) as error:
            logger.error(
                "bot_media_cleanup_failed",
                extra={
                    **trace.fields(event="bot_media_cleanup_failed"),
                    "error_code": "media_cleanup_failed",
                    "exception_type": type(error).__name__,
                    "exception_message": trace.safe_message(error),
                    "stack": safe_stack(error),
                },
            )

    async def legacy_command(
        session: AsyncSession,
        actor: User,
        command: str,
        attachments: list[object] | None,
        *,
        bot: object | None,
    ) -> tuple[UiReply, str | None]:
        fields = command.split()
        if fields and fields[0] == "/admin":
            return await admin_ui.handle_command(session, actor, command), None
        if fields and fields[0].lower() == "/files":
            if len(fields) != 2:
                return UiReply("Формат: /files UUID_карточки"), None
            return await list_card_attachments(session, actor, UUID(fields[1])), None
        if fields and fields[0].lower() == "/getfile":
            if len(fields) != 3:
                return UiReply("Формат: /getfile UUID_карточки UUID_файла"), None
            return (
                await get_card_attachment(
                    session, actor, UUID(fields[1]), UUID(fields[2])
                ),
                None,
            )
        media = await handle_media_text(session, actor, command, attachments, bot=bot)
        result = media.reply if media is not None else None
        if result is None:
            result = await handle_access_text(session, actor, command)
        if result is None:
            result = await handle_issue_text(session, actor, command)
        if result is None:
            result = await handle_notification_text(session, actor, command)
        if result is None:
            result = await handle_draft_text(session, actor, command)
        return UiReply(result or GENERAL_HELP), media.storage_key if media else None

    async def media_in_dialog(
        session: AsyncSession,
        actor: User,
        dialog: BotDialog,
        attachments: list[object],
        *,
        caption: str,
        bot: object | None,
    ) -> tuple[UiReply, str | None]:
        if (
            dialog.flow_kind == "issue_new"
            and dialog.step == "attachments"
            and dialog.draft_id is not None
        ):
            command = f"/file draft {dialog.draft_id}"
            buttons = [
                [Button("Проверить заявку", "i:issue:preview")],
                [Button("Отправить заявку", "i:issue:submit")],
            ]
        elif dialog.flow_kind == "issue_comment" and dialog.data.get("card_id"):
            card_id = dialog.data["card_id"]
            command = f"/file card {card_id}"
            if caption:
                command += f" | {caption}"
            buttons = [[Button("Открыть карточку", f"i:card:{card_id}")]]
        else:
            return (
                UiReply(
                    "Сначала дойдите до шага вложений или откройте обсуждение проблемы."
                ),
                None,
            )
        result = await handle_media_text(session, actor, command, attachments, bot=bot)
        if result is None:
            return UiReply("Не удалось определить вложение. Попробуйте ещё раз."), None
        return UiReply(result.reply, buttons), result.storage_key

    async def process_message(event: MessageCreated, trace: UpdateTrace) -> None:
        message = event.message
        sender = message.sender
        body = message.body
        if sender is None or sender.is_bot or body is None:
            trace.result = "ignored_sender_or_body"
            return
        if message.recipient.chat_type != ChatType.DIALOG:
            trace.result = "ignored_chat_type"
            return

        contact = next(
            (item for item in body.attachments or [] if isinstance(item, Contact)), None
        )
        reply = UiReply(GENERAL_HELP)
        uploaded_storage_key: str | None = None
        async with session_factory() as session:
            try:
                trace.phase = "resolve_actor"
                full_name = max_profile_name(sender.first_name, sender.last_name)
                actor = await get_or_create_user(
                    session,
                    str(sender.user_id),
                    full_name=full_name,
                    max_username=getattr(sender, "username", None),
                )
                trace.actor_id = str(actor.id)
                content = body.text or (
                    contact.payload.vcf_info if contact and contact.payload else ""
                )
                trace.phase = "reserve_update"
                if not await _reserve_message(
                    session, user_id=actor.id, message_id=body.mid, content=content
                ):
                    trace.result = "duplicate"
                    reply = UiReply(
                        "Это сообщение уже обработано. Откройте нужный раздел из меню, чтобы увидеть результат."
                    )
                elif contact is not None:
                    trace.phase = "validate_contact"
                    payload = contact.payload
                    if payload is None:
                        raise ValueError("Контакт MAX не содержит проверочных данных")
                    phone = validate_bot_contact(
                        vcf_info=payload.vcf_info or "",
                        signature=payload.hash or "",
                        sender_user_id=str(sender.user_id),
                        contact_user_id=(
                            str(payload.max_info.user_id) if payload.max_info else None
                        ),
                        bot_token=bot_token,
                    )
                    trace.phase = "link_contact"
                    await link_verified_phone(session, actor, phone)
                    await bind_staff_by_verified_phone(session, actor)
                    reply = _home(actor)
                    reply.text = "Номер подтверждён. " + reply.text
                else:
                    command = (body.text or "").strip()
                    trace.phase = "load_dialog"
                    dialog = await get_dialog(session, actor.id)
                    if dialog is not None:
                        trace.set_dialog(dialog)
                    trace.phase = "route_message"
                    if command == "/whoami":
                        reply = UiReply(f"Ваш MAX ID: {sender.user_id}")
                    elif command in {"/start", "/menu", "Меню", "меню", "/phone"}:
                        await clear_dialog(session, actor.id)
                        reply = _home(actor)
                    elif command in {"/help", "Помощь", "помощь"}:
                        home = _home(actor)
                        reply = UiReply(
                            GENERAL_HELP, home.buttons, home.request_contact
                        )
                    elif command in {"/cancel", "Отмена", "отмена"}:
                        await clear_dialog(session, actor.id)
                        reply = _home(actor)
                        reply.text = "Действие отменено. " + reply.text
                    elif command.startswith("/"):
                        trace.phase = "legacy_command"
                        reply, uploaded_storage_key = await legacy_command(
                            session, actor, command, body.attachments, bot=message.bot
                        )
                    elif body.attachments and dialog is not None:
                        trace.phase = "dialog_media"
                        reply, uploaded_storage_key = await media_in_dialog(
                            session,
                            actor,
                            dialog,
                            body.attachments,
                            caption=command,
                            bot=message.bot,
                        )
                    elif dialog is not None and command:
                        trace.phase = "dialog_text"
                        reply = await _handle_dialog_text(
                            session, actor, dialog, command
                        )
                    elif body.attachments:
                        reply = UiReply(
                            "Сначала откройте проблему или черновик, затем отправьте вложение."
                        )
                    else:
                        reply = _home(actor)
                if session.in_transaction():
                    trace.phase = "commit"
                    await session.commit()
            except (
                FileError,
                DraftError,
                IssueError,
                AccessRuleError,
                NotificationError,
                ValueError,
            ) as error:
                await session.rollback()
                cleanup_upload(uploaded_storage_key, trace)
                trace.fail(error)
                reply = failure(error)
            except IntegrityError as error:
                await session.rollback()
                cleanup_upload(uploaded_storage_key, trace)
                trace.fail(error)
                reply = UiReply(
                    "Данные изменились. Обновите список и повторите действие."
                )
            except Exception as error:
                await session.rollback()
                cleanup_upload(uploaded_storage_key, trace)
                trace.fail(error, result="failed")
                reply = UiReply(
                    "Не удалось обработать сообщение. Попробуйте ещё раз позже."
                )

        if trace.result == "processed":
            trace.phase = "send_reply"
        try:
            reply_attachments = keyboard_for(reply)
            if reply.media is not None:
                reply_attachments.append(reply.media)
            await message.answer(
                text=reply.text[:3900],
                attachments=reply_attachments,
            )
        except Exception:
            trace.phase = "send_reply"
            raise

    @router.message_created()
    async def on_message(event: MessageCreated) -> None:
        message = getattr(event, "message", None)
        body = getattr(message, "body", None)
        sensitive_values = [getattr(body, "text", None) or ""]
        for item in getattr(body, "attachments", None) or []:
            if isinstance(item, Contact):
                payload = item.payload
                if payload is not None:
                    sensitive_values.extend(
                        (
                            getattr(payload, "vcf_info", None) or "",
                            getattr(payload, "hash", None) or "",
                        )
                    )
        trace = UpdateTrace(
            logger,
            event_type=message_event_type(body),
            message_id=getattr(body, "mid", None),
            attachments=getattr(body, "attachments", None),
            sensitive_values=tuple(sensitive_values),
        )
        context_token = current_request_id.set(trace.request_id)
        trace.received()
        try:
            await process_message(event, trace)
        except asyncio.CancelledError:
            trace.result = "cancelled"
            raise
        except Exception as error:
            trace.fail(error, result="failed")
            raise
        finally:
            trace.finished()
            current_request_id.reset(context_token)

    @router.bot_started()
    async def on_started(event: BotStarted) -> None:
        trace = UpdateTrace(
            logger,
            event_type="bot_started",
            message_id=f"bot_started:{event.chat_id}:{event.timestamp}",
        )
        context_token = current_request_id.set(trace.request_id)
        trace.received()
        try:
            sender = event.user
            if sender.is_bot:
                trace.result = "ignored_bot"
                return

            reply = UiReply("Не удалось открыть меню. Попробуйте позже.")
            async with session_factory() as session:
                try:
                    trace.phase = "resolve_actor"
                    full_name = max_profile_name(sender.first_name, sender.last_name)
                    actor = await get_or_create_user(
                        session,
                        str(sender.user_id),
                        full_name=full_name,
                        max_username=getattr(sender, "username", None),
                    )
                    trace.actor_id = str(actor.id)
                    trace.phase = "reserve_update"
                    if not await _reserve_message(
                        session,
                        user_id=actor.id,
                        message_id=f"bot_started:{event.chat_id}:{event.timestamp}",
                        content="start",
                    ):
                        trace.result = "duplicate"
                        return
                    trace.phase = "route_start"
                    await clear_dialog(session, actor.id)
                    reply = _home(actor)
                    if session.in_transaction():
                        trace.phase = "commit"
                        await session.commit()
                except Exception as error:
                    await session.rollback()
                    trace.fail(error, result="failed")

            if trace.result == "processed":
                trace.phase = "send_reply"
            await event.send(
                text=reply.text[:3900], attachments=keyboard_for(reply)
            )
        except asyncio.CancelledError:
            trace.result = "cancelled"
            raise
        except Exception as error:
            trace.fail(error, result="failed")
            raise
        finally:
            trace.finished()
            current_request_id.reset(context_token)

    async def process_callback(event: MessageCallback, trace: UpdateTrace) -> None:
        # MAX expects every button press to be acknowledged, even if its message is gone.
        trace.phase = "acknowledge"
        await event.ack(notification="Обрабатываю...")
        message = event.message
        if message is None or message.recipient.chat_type != ChatType.DIALOG:
            trace.result = "ignored_chat_type"
            return
        sender = event.callback.user
        if sender.is_bot:
            trace.result = "ignored_bot"
            return
        payload = event.callback.payload or ""
        reply = UiReply("Кнопка больше не действует. Вернитесь в главное меню.")
        async with session_factory() as session:
            try:
                trace.phase = "resolve_actor"
                full_name = max_profile_name(sender.first_name, sender.last_name)
                actor = await get_or_create_user(
                    session,
                    str(sender.user_id),
                    full_name=full_name,
                    max_username=getattr(sender, "username", None),
                )
                trace.actor_id = str(actor.id)
                trace.phase = "reserve_update"
                if await _reserve_message(
                    session,
                    user_id=actor.id,
                    message_id=f"callback:{event.callback.callback_id}",
                    content=payload,
                ):
                    trace.phase = "route_callback"
                    reply = await _handle_action(session, actor, payload)
                else:
                    trace.result = "duplicate"
                    reply = UiReply(
                        "Нажатие уже обработано. Откройте раздел из меню, чтобы увидеть результат."
                    )
                if session.in_transaction():
                    trace.phase = "commit"
                    await session.commit()
            except (
                FileError,
                DraftError,
                IssueError,
                AccessRuleError,
                NotificationError,
                ValueError,
            ) as error:
                await session.rollback()
                trace.fail(error)
                reply = failure(error)
            except IntegrityError as error:
                await session.rollback()
                trace.fail(error)
                reply = UiReply(
                    "Данные изменились. Обновите список и повторите действие."
                )
            except Exception as error:
                await session.rollback()
                trace.fail(error, result="failed")
                reply = UiReply("Не удалось выполнить действие. Попробуйте позже.")
        if trace.result == "processed":
            trace.phase = "send_reply"
        try:
            attachments = keyboard_for(reply)
            if reply.media is not None:
                attachments.append(reply.media)
            await message.answer(text=reply.text[:3900], attachments=attachments)
        except Exception:
            trace.phase = "send_reply"
            raise

    @router.message_callback()
    async def on_callback(event: MessageCallback) -> None:
        callback = getattr(event, "callback", None)
        trace = UpdateTrace(
            logger,
            event_type=callback_event_type(getattr(callback, "payload", None)),
            callback_id=getattr(callback, "callback_id", None),
            sensitive_values=(getattr(callback, "payload", None) or "",),
        )
        context_token = current_request_id.set(trace.request_id)
        trace.received()
        try:
            await process_callback(event, trace)
        except asyncio.CancelledError:
            trace.result = "cancelled"
            raise
        except Exception as error:
            trace.fail(error, result="failed")
            raise
        finally:
            trace.finished()
            current_request_id.reset(context_token)

    return router
