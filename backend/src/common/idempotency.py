"""Transactional at-most-once guard for keyed, mutating HTTP requests.

The dependency deliberately does not commit. Existing handlers commit their
domain mutation and this receipt in the same cached request session. A replay
is rejected with 409; storing/replaying the original HTTP response is outside
the current CommandReceipt schema.
"""

import hashlib
import json
import re
from typing import Annotated
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import CommandReceipt, User
from src.db.session import get_session


MAX_KEYED_BODY_BYTES = 256 * 1024
KEY_PATTERN = re.compile(r"[A-Za-z0-9._~:-]{16,128}\Z")
MUTATING_PREFIXES = ("/access/", "/issues/", "/auth/", "/notifications/", "/drafts/")
READ_ONLY_POST_PATHS = frozenset({"/issues/suggest"})


def needs_http_idempotency(path: str, method: str) -> bool:
    """Only subject-changing API routes; legacy/demo and suggestions are excluded."""
    return (
        method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith(MUTATING_PREFIXES)
        and path not in READ_ONLY_POST_PATHS
    )


def _request_hash(method: str, path: str, query: str, body: bytes) -> str:
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        canonical_body = body
    else:
        canonical_body = json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    canonical_query = "&".join(
        f"{key}={value}" for key, value in sorted(parse_qsl(query, keep_blank_values=True))
    )
    digest = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), canonical_query.encode(), canonical_body):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


async def reserve_http_command(
    request: Request,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> None:
    """Reserve a key without a commit; a successful handler commits it atomically.

    Keyless requests retain their existing behavior. A replay gets 409 instead
    of a reconstructed 2xx because CommandReceipt has no response snapshot.
    """
    if idempotency_key is None:
        return
    if KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_idempotency_key", "message": "Некорректный Idempotency-Key"},
        )
    body = await request.body()
    if len(body) > MAX_KEYED_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Слишком большой запрос с Idempotency-Key")

    operation = f"{request.method.upper()} {request.url.path}"
    request_hash = _request_hash(
        request.method, request.url.path, request.url.query, body
    )
    inserted = await session.scalar(
        insert(CommandReceipt)
        .values(
            id=uuid4(),
            source="miniapp",
            external_key=idempotency_key,
            actor_user_id=actor.id,
            operation=operation,
            request_hash=request_hash,
        )
        .on_conflict_do_nothing(
            constraint="uq_command_receipts_source_actor_key"
        )
        .returning(CommandReceipt.id)
    )
    if inserted is not None:
        return

    existing = await session.scalar(
        select(CommandReceipt).where(
            CommandReceipt.source == "miniapp",
            CommandReceipt.actor_user_id == actor.id,
            CommandReceipt.external_key == idempotency_key,
        )
    )
    if existing is not None and existing.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_key_reused", "message": "Ключ уже использован для другого запроса"},
        )
    raise HTTPException(
        status_code=409,
        detail={"code": "already_processed", "message": "Запрос с этим ключом уже обработан"},
    )
