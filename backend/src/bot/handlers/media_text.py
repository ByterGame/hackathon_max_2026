"""Private MAX media uploads with an explicit draft or issue-card destination."""

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from aiohttp import ClientError, ClientSession, ClientTimeout
from maxapi.client.ssl import create_default_connector
from maxapi.enums import UploadType
from maxapi.types.attachments import File as MaxFile
from maxapi.types.attachments import Image, Video
from maxapi.types.attachments.attachment import (
    OtherAttachmentPayload,
    PhotoAttachmentPayload,
)
from maxapi.types.input_media import InputMediaBuffer
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.ui import Button, UiReply
from src.db.models import File, IssueMessage, IssueReport, User
from src.domain.drafts.service import get_draft
from src.domain.files.service import _parent as require_file_parent
from src.domain.files.service import get_file, list_files, upload
from src.domain.files.storage import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_BYTES,
    FileError,
    detect_mime,
    storage_root,
)
from src.domain.issues.service import add_comment, get_visible_card

MEDIA_HELP = (
    "Чтобы прикрепить фото, PDF или видео, отправьте одно вложение с подписью:\n"
    "/file draft UUID_черновика — в свой черновик проблемы;\n"
    "/file card UUID_карточки | комментарий — в обсуждение доступной проблемы;\n"
    "/file access UUID_заявки — в заявку на доступ к дому.\n"
    "Чтобы посмотреть вложения заявки: /files access UUID_заявки. "
    "Допустимы JPEG, PNG, WebP, PDF, MP4 и MOV до 8 МиБ. "
    "При /draft send файлы черновика привяжутся к исходному описанию карточки."
)
_MAX_MEDIA_HOSTS = frozenset(
    {
        "i.oneme.ru",
        "iu.oneme.ru",
        "fu.oneme.ru",
        "vu.okcdn.ru",
    }
)
_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
_FILE_PAGE_SIZE = 10


@dataclass(frozen=True)
class MediaResult:
    reply: str
    storage_key: str | None = None


def _file_button(label: str, payload: str) -> list[Button]:
    return [Button(label, payload)]


async def list_card_attachments(
    session: AsyncSession, actor: User, card_id: UUID, *, page: int = 0
) -> UiReply:
    """List only ready files on a card the actor can currently read."""
    if page < 0 or page > 1000:
        raise ValueError("Некорректная страница вложений")
    card = await get_visible_card(session, actor, card_id)
    report_ids = select(IssueReport.id).where(IssueReport.card_id == card.id)
    message_ids = select(IssueMessage.id).where(IssueMessage.card_id == card.id)
    rows = (
        await session.scalars(
            select(File)
            .where(
                File.state == "ready",
                or_(
                    File.issue_report_id.in_(report_ids),
                    File.issue_message_id.in_(message_ids),
                ),
            )
            .order_by(File.created_at, File.id)
            .offset(page * _FILE_PAGE_SIZE)
            .limit(_FILE_PAGE_SIZE + 1)
        )
    ).all()
    files = rows[:_FILE_PAGE_SIZE]
    buttons = [
        _file_button(
            f"{'Описание' if item.issue_report_id else 'Обсуждение'} · {item.original_name[:45]}",
            f"f:get:{card.id}:{item.id}",
        )
        for item in files
    ]
    navigation: list[Button] = []
    if page:
        navigation.append(Button("Назад", f"f:card:{card.id}:{page - 1}"))
    if len(rows) > _FILE_PAGE_SIZE:
        navigation.append(Button("Далее", f"f:card:{card.id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(_file_button("К карточке", f"i:card:{card.id}"))
    if not files:
        return UiReply("У этой проблемы пока нет вложений.", buttons)
    return UiReply(
        f"Вложения проблемы «{card.title[:100]}», страница {page + 1}. "
        "Нажмите на файл, чтобы получить его в этом чате.",
        buttons,
    )


def _read_private_file(path: Path, expected_size: int) -> bytes:
    """Read bounded bytes without following a symlink if a path changed after access check."""
    if expected_size <= 0 or expected_size > MAX_FILE_BYTES:
        raise FileError(404, "file_not_found", "Файл не найден")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
                raise FileError(404, "file_not_found", "Файл не найден")
            data = bytearray()
            while len(data) < expected_size:
                chunk = os.read(descriptor, min(64 * 1024, expected_size - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != expected_size or os.read(descriptor, 1):
                raise FileError(404, "file_not_found", "Файл не найден")
            return bytes(data)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise FileError(404, "file_not_found", "Файл не найден") from error


async def get_card_attachment(
    session: AsyncSession, actor: User, card_id: UUID, file_id: UUID
) -> UiReply:
    """Recheck both card and file visibility before sending private bytes to MAX."""
    card = await get_visible_card(session, actor, card_id)
    file, path = await get_file(session, actor, file_id=file_id, root=storage_root())
    if file.issue_report_id is not None:
        parent = await session.get(IssueReport, file.issue_report_id)
    elif file.issue_message_id is not None:
        parent = await session.get(IssueMessage, file.issue_message_id)
    else:
        parent = None
    if parent is None or parent.card_id != card.id:
        raise FileError(404, "file_not_found", "Файл не найден")
    data = await asyncio.to_thread(_read_private_file, path, file.size_bytes)
    media = InputMediaBuffer(
        buffer=data,
        filename=Path(file.original_name).stem,
        type=UploadType.FILE,
    )
    return UiReply(
        f"Вложение: {file.original_name}",
        [
            _file_button("Другие вложения", f"f:card:{card.id}"),
            _file_button("К карточке", f"i:card:{card.id}"),
        ],
        media=media,
    )


async def list_access_attachments(
    session: AsyncSession, actor: User, request_id: UUID, *, page: int = 0
) -> UiReply:
    if page < 0 or page > 1000:
        raise ValueError("Некорректная страница вложений")
    rows = await list_files(
        session, actor, parent_kind="resident", parent_id=request_id
    )
    start = page * _FILE_PAGE_SIZE
    selected = rows[start : start + _FILE_PAGE_SIZE]
    buttons = [
        _file_button(item.original_name[:55], f"f:access_get:{request_id}:{item.id}")
        for item in selected
    ]
    navigation: list[Button] = []
    if page:
        navigation.append(Button("Назад", f"f:access:{request_id}:{page - 1}"))
    if start + len(selected) < len(rows):
        navigation.append(Button("Далее", f"f:access:{request_id}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append(_file_button("К заявке", f"a:request:resident:{request_id}"))
    if not selected:
        return UiReply("У этой заявки пока нет вложений.", buttons)
    return UiReply(
        f"Вложения заявки на доступ · страница {page + 1}. "
        "Нажмите на файл, чтобы получить его в чате.",
        buttons,
    )


async def get_access_attachment(
    session: AsyncSession, actor: User, request_id: UUID, file_id: UUID
) -> UiReply:
    file, path = await get_file(session, actor, file_id=file_id, root=storage_root())
    if file.resident_request_id != request_id:
        raise FileError(404, "file_not_found", "Файл не найден")
    data = await asyncio.to_thread(_read_private_file, path, file.size_bytes)
    media = InputMediaBuffer(
        buffer=data,
        filename=Path(file.original_name).stem,
        type=UploadType.FILE,
    )
    return UiReply(
        f"Вложение заявки: {file.original_name}",
        [
            _file_button("Другие вложения", f"f:access:{request_id}"),
            _file_button("К заявке", f"a:request:resident:{request_id}"),
        ],
        media=media,
    )


def _safe_max_url(value: str) -> str:
    if (
        len(value) > 4096
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise FileError(400, "invalid_max_url", "Некорректная ссылка вложения MAX")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise FileError(
            400, "invalid_max_url", "Некорректная ссылка вложения MAX"
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _MAX_MEDIA_HOSTS
        or parsed.netloc.lower() not in {parsed.hostname, f"{parsed.hostname}:443"}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise FileError(
            400, "invalid_max_url", "Вложение должно быть загружено на медиа-сервер MAX"
        )
    return value


async def _source(
    attachment: Image | Video | MaxFile, *, bot: object | None
) -> tuple[str, str | None, str]:
    if isinstance(attachment, Image):
        payload = attachment.payload
        if not isinstance(payload, PhotoAttachmentPayload):
            raise FileError(
                400, "invalid_max_media", "MAX не передал ссылку на изображение"
            )
        return _safe_max_url(payload.url), None, "image"
    if isinstance(attachment, MaxFile):
        payload = attachment.payload
        if not isinstance(payload, OtherAttachmentPayload):
            raise FileError(400, "invalid_max_media", "MAX не передал ссылку на файл")
        if attachment.size is not None and attachment.size > MAX_FILE_BYTES:
            raise FileError(413, "file_too_large", "Файл больше 8 МиБ")
        return _safe_max_url(payload.url), attachment.filename, "file"
    if isinstance(attachment, Video):
        video = attachment
        if video.urls is None and video.token and bot is not None:
            video = await bot.get_video(video.token)
        urls = video.urls
        url = (
            next(
                (
                    candidate
                    for candidate in (
                        urls.mp4_360,
                        urls.mp4_480,
                        urls.mp4_240,
                        urls.mp4_720,
                        urls.mp4_144,
                        urls.mp4_1080,
                    )
                    if candidate
                ),
                None,
            )
            if urls
            else None
        )
        if url is None:
            raise FileError(400, "invalid_max_media", "MAX не передал ссылку на видео")
        return _safe_max_url(url), None, "video"
    raise FileError(415, "unsupported_type", "Это вложение не поддерживается")


async def _chunks(
    first: list[bytes], remaining: AsyncIterator[bytes]
) -> AsyncIterator[bytes]:
    for chunk in first:
        yield chunk
    async for chunk in remaining:
        yield chunk


async def _download_and_store(
    session: AsyncSession,
    actor: User,
    *,
    url: str,
    original_name: str | None,
    kind: str,
    draft_id: UUID | None,
    parent_id: UUID | None,
    parent_kind: str | None = None,
):
    timeout = ClientTimeout(total=40, sock_connect=5, sock_read=15)
    async with ClientSession(
        connector=create_default_connector(),
        timeout=timeout,
        trust_env=False,
        auto_decompress=False,
    ) as client:
        current_url = url
        for _ in range(4):
            async with client.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise FileError(
                            502, "max_media_unavailable", "MAX не отдал вложение"
                        )
                    current_url = _safe_max_url(urljoin(current_url, location))
                    continue
                if response.status != 200:
                    raise FileError(
                        502,
                        "max_media_unavailable",
                        "MAX не отдал вложение; попробуйте позже",
                    )
                if (
                    response.content_length is not None
                    and response.content_length > MAX_FILE_BYTES
                ):
                    raise FileError(413, "file_too_large", "Файл больше 8 МиБ")
                iterator = response.content.iter_chunked(64 * 1024)
                first: list[bytes] = []
                head = bytearray()
                while len(head) < 32:
                    chunk = await anext(iterator, None)
                    if chunk is None:
                        break
                    first.append(chunk)
                    head.extend(chunk[: 32 - len(head)])
                mime = detect_mime(bytes(head))
                if (
                    mime not in ALLOWED_EXTENSIONS
                    or (kind == "image" and not mime.startswith("image/"))
                    or (kind == "video" and not mime.startswith("video/"))
                ):
                    raise FileError(
                        415,
                        "unsupported_type",
                        "Тип содержимого вложения не поддерживается",
                    )
                name = original_name or f"max_{kind}{_EXTENSION[mime]}"
                return await upload(
                    session,
                    actor,
                    root=storage_root(),
                    chunks=_chunks(first, iterator),
                    filename=name,
                    content_type=mime,
                    draft_id=draft_id,
                    parent_kind=parent_kind or ("issue_message" if parent_id is not None else None),
                    parent_id=parent_id,
                )
        raise FileError(
            502, "max_media_redirects", "MAX перенаправляет вложение слишком много раз"
        )


async def handle_media_text(
    session: AsyncSession,
    actor: User,
    text: str,
    attachments: list[object] | None,
    *,
    bot: object | None,
) -> MediaResult | None:
    """Return a reply and a cleanup key; the caller commits the transaction."""
    command, _, argument = text.strip().partition(" ")
    if command.lower() == "/filehelp":
        return MediaResult(MEDIA_HELP)
    if command.lower() != "/file":
        return None
    destination, separator, comment = argument.partition("|")
    fields = destination.split()
    if len(fields) != 2 or fields[0] not in {"draft", "card", "access"}:
        return MediaResult(MEDIA_HELP)
    try:
        destination_id = UUID(fields[1])
    except ValueError as error:
        raise FileError(
            400, "invalid_destination", "Нужен UUID черновика, карточки или заявки"
        ) from error
    if not attachments:
        return MediaResult(
            "Прикрепите одно вложение к сообщению с командой.\n" + MEDIA_HELP
        )
    if len(attachments) != 1:
        raise FileError(400, "too_many_files", "Отправляйте по одному вложению за раз")
    attachment = attachments[0]
    if not isinstance(attachment, (Image, Video, MaxFile)):
        raise FileError(415, "unsupported_type", "Прикрепите фото, PDF или видео")

    parent_kind = None
    if fields[0] == "draft":
        if separator:
            raise FileError(
                400, "invalid_destination", "Комментарий допустим только для карточки"
            )
        draft = await get_draft(session, actor, destination_id)
        if draft.flow_kind != "issue_card" or draft.submitted_at is not None:
            raise FileError(
                409, "invalid_draft", "Нужен неотправленный черновик проблемы"
            )
        parent_id = None
        draft_id = destination_id
    elif fields[0] == "card":
        card = await get_visible_card(session, actor, destination_id)
        if card.status == "closed":
            raise FileError(
                409, "issue_closed", "В закрытую заявку нельзя добавить файл"
            )
        message = await add_comment(
            session,
            actor,
            card.id,
            comment.strip() or "Вложение от участника обсуждения",
        )
        parent_id = message.id
        draft_id = None
    else:
        if separator:
            raise FileError(
                400, "invalid_destination",
                "Пояснение к вложению напишите отдельным сообщением в обсуждении заявки",
            )
        await require_file_parent(
            session, actor, "resident", destination_id, writing=True
        )
        parent_id = destination_id
        parent_kind = "resident"
        draft_id = None

    url, original_name, kind = await _source(attachment, bot=bot)
    try:
        file = await _download_and_store(
            session,
            actor,
            url=url,
            original_name=original_name,
            kind=kind,
            draft_id=draft_id,
            parent_id=parent_id,
            parent_kind=parent_kind,
        )
    except (ClientError, TimeoutError) as error:
        raise FileError(
            502, "max_media_unavailable", "Не удалось скачать вложение из MAX"
        ) from error
    return MediaResult(
        f"Вложение сохранено: {file.id}. "
        + (
            "При /draft send оно перейдёт в созданную карточку."
            if draft_id is not None
            else (
                f"Оно прикреплено к заявке на доступ {destination_id}."
                if parent_kind == "resident"
                else f"Оно добавлено в обсуждение карточки {destination_id}."
            )
        ),
        storage_key=file.storage_key,
    )
