"""Bounded, atomic storage of private attachment bytes."""

import hashlib
import os
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from uuid import uuid4


MAX_FILE_BYTES = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
    "video/mp4": {".mp4", ".m4v"},
    "video/quicktime": {".mov"},
}
_STORAGE_KEY = re.compile(r"^[0-9a-f]{64}$")


class FileError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StoredBytes:
    key: str
    mime_type: str
    size_bytes: int
    sha256: str


def storage_root() -> Path:
    """Require an explicit absolute volume path; never fall back to repository storage."""
    raw = os.environ.get("FILE_STORAGE_ROOT", "")
    root = Path(raw)
    if not raw or not root.is_absolute() or root.is_symlink():
        raise RuntimeError("FILE_STORAGE_ROOT must be an absolute, non-symlink directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def path_for_key(root: Path, key: str) -> Path:
    if not _STORAGE_KEY.fullmatch(key):
        raise FileError(500, "invalid_storage_key", "Некорректный ключ файла")
    return root / key[:2] / key


def normalize_name(value: str, mime_type: str) -> str:
    if mime_type not in ALLOWED_EXTENSIONS:
        raise FileError(415, "unsupported_type", "Тип файла не поддерживается")
    name = unicodedata.normalize("NFC", value.replace("\\", "/").rsplit("/", 1)[-1])
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    name = name.strip().strip(".")
    if not name or len(name) > 200 or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS[mime_type]:
        raise FileError(400, "invalid_filename", "Укажите имя файла с подходящим расширением")
    return name


def detect_mime(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
        return "image/png"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP" and head[12:16] in {
        b"VP8 ", b"VP8L", b"VP8X"
    }:
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if head[8:12] == b"qt  ":
            return "video/quicktime"
        if head[8:12] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"MSNV"}:
            return "video/mp4"
    return None


async def store_stream(
    root: Path,
    chunks: AsyncIterator[bytes],
    *,
    filename: str,
    content_type: str,
) -> tuple[StoredBytes, str]:
    """Write a new file with O_EXCL and publish it only after full validation."""
    mime_type = content_type.split(";", 1)[0].strip().lower()
    name = normalize_name(filename, mime_type)
    key = token_hex(32)
    destination = path_for_key(root, key)
    directory = destination.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise FileError(500, "unsafe_storage", "Недоступно хранилище файлов")
    temporary = directory / f".{key}.{uuid4().hex}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    try:
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise FileError(400, "invalid_upload", "Ожидаются двоичные данные")
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise FileError(413, "file_too_large", "Файл больше 8 МиБ")
            if len(head) < 32:
                head.extend(chunk[: 32 - len(head)])
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                remaining = remaining[os.write(descriptor, remaining):]
        if size == 0:
            raise FileError(400, "empty_file", "Пустой файл не принимается")
        if detect_mime(bytes(head)) != mime_type:
            raise FileError(415, "content_mismatch", "Содержимое не совпадает с типом файла")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        # link() fails if an (extremely unlikely) generated key already exists.
        os.link(temporary, destination)
        os.unlink(temporary)
        return StoredBytes(key, mime_type, size, digest.hexdigest()), name
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def remove_failed_upload(root: Path, key: str) -> None:
    """Remove only bytes created by a DB transaction that failed to commit."""
    path_for_key(root, key).unlink(missing_ok=True)
