"""Authenticated upload, attachment, listing and download routes.

The raw upload body is streamed directly, without multipart pre-buffering.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.files.service import attach, file_data, get_file, list_files, upload
from src.domain.files.storage import MAX_FILE_BYTES, FileError, remove_failed_upload, storage_root


router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    id: UUID
    draft_id: UUID | None
    parent_kind: str | None
    parent_id: UUID | None
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    state: str
    created_at: datetime | None
    ready_at: datetime | None


class FileResponseBody(BaseModel):
    file: FileInfo


class FileListBody(BaseModel):
    files: list[FileInfo]


class AttachBody(BaseModel):
    file_id: UUID
    parent_kind: str
    parent_id: UUID


def _raise_file_error(error: FileError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.post(
    "/upload",
    response_model=FileResponseBody,
    status_code=201,
    summary="Загрузить частный файл в черновик или готовую заявку",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in (
                    "image/jpeg", "image/png", "image/webp", "application/pdf",
                    "video/mp4", "video/quicktime",
                )
            },
        }
    },
)
async def upload_file(
    request: Request,
    filename: Annotated[str, Query(max_length=255)],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    draft_id: UUID | None = None,
    parent_kind: str | None = None,
    parent_id: UUID | None = None,
) -> FileResponseBody:
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            size = int(declared_size)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
        if size < 0 or size > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 8 MiB")
    root = storage_root()
    file = None
    try:
        file = await upload(
            session,
            actor,
            root=root,
            chunks=request.stream(),
            filename=filename,
            content_type=request.headers.get("content-type", ""),
            draft_id=draft_id,
            parent_kind=parent_kind,
            parent_id=parent_id,
        )
        await session.commit()
        return FileResponseBody(file=FileInfo.model_validate(file_data(file)))
    except FileError as error:
        await session.rollback()
        _raise_file_error(error)
    except IntegrityError as error:
        await session.rollback()
        if file is not None:
            remove_failed_upload(root, file.storage_key)
        raise HTTPException(
            status_code=409,
            detail={"code": "file_conflict", "message": "Не удалось сохранить файл"},
        ) from error


@router.post(
    "/attach",
    response_model=FileResponseBody,
    summary="Привязать файл черновика к исходному описанию",
)
async def attach_file(
    body: AttachBody,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponseBody:
    try:
        file = await attach(
            session, actor, file_id=body.file_id,
            parent_kind=body.parent_kind, parent_id=body.parent_id,
        )
        await session.commit()
        return FileResponseBody(file=FileInfo.model_validate(file_data(file)))
    except FileError as error:
        await session.rollback()
        _raise_file_error(error)


@router.get("/list", response_model=FileListBody, summary="Список вложений заявки или черновика")
async def list_parent_files(
    parent_kind: str,
    parent_id: UUID,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileListBody:
    try:
        rows = await list_files(
            session, actor, parent_kind=parent_kind, parent_id=parent_id
        )
        return FileListBody(files=[FileInfo.model_validate(file_data(row)) for row in rows])
    except FileError as error:
        await session.rollback()
        _raise_file_error(error)


@router.get("/download", summary="Скачать файл после новой проверки доступа")
async def download_file(
    file_id: UUID,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    try:
        file, path = await get_file(session, actor, file_id=file_id, root=storage_root())
    except FileError as error:
        await session.rollback()
        _raise_file_error(error)
    response = FileResponse(
        path,
        filename=file.original_name,
        media_type=file.mime_type,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
    # Do not keep a PostgreSQL connection checked out while a slow client downloads.
    await session.rollback()
    return response
