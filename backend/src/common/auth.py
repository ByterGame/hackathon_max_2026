"""Authentication of MAX mini-app requests and verified contact sharing."""

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import load_bot_token
from src.core.logging import safe_exception_message
from src.db.models import User
from src.db.session import get_session


MAX_AUTH_MAX_AGE_SECONDS = 3600
MAX_AUTH_FUTURE_SKEW_SECONDS = 300
logger = logging.getLogger(__name__)


def _is_fresh(value: str, *, now: int | None = None) -> bool:
    try:
        issued_at = int(value)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else now
    return current - MAX_AUTH_MAX_AGE_SECONDS <= issued_at <= current + MAX_AUTH_FUTURE_SKEW_SECONDS


def _is_contact_fresh(value: str, *, now: int | None = None) -> bool:
    """Accept MAX contact timestamps in seconds or milliseconds since epoch."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}|[0-9]{13}", value):
        return False
    if len(value) == 10:
        return _is_fresh(value, now=now)
    current = int(time.time()) if now is None else now
    issued_at_ms = int(value)
    return (
        (current - MAX_AUTH_MAX_AGE_SECONDS) * 1000
        <= issued_at_ms
        <= (current + MAX_AUTH_FUTURE_SKEW_SECONDS) * 1000
    )


def validate_init_data(raw: str, bot_token: str, *, now: int | None = None) -> dict:
    """Return the signed MAX user, rejecting malformed, duplicated or stale data."""
    if not raw or len(raw) > 16_384:
        raise ValueError("Invalid MAX launch data")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValueError("Malformed MAX launch data") from error
    fields = dict(pairs)
    if len(fields) != len(pairs) or not fields.get("hash") or not fields.get("user"):
        raise ValueError("Missing or repeated MAX launch parameters")
    if not _is_fresh(fields.get("auth_date", ""), now=now):
        raise ValueError("Expired MAX launch data")

    signed_text = "\n".join(f"{key}={value}" for key, value in sorted(pairs) if key != "hash")
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, signed_text.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, fields["hash"]):
        raise ValueError("Invalid MAX launch signature")
    try:
        profile = json.loads(fields["user"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid MAX user profile") from error
    user_id = profile.get("id") if isinstance(profile, dict) else None
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Invalid MAX user ID")
    return profile


def normalize_phone_number(value: str) -> str:
    """Normalize Russian phone numbers for matching pending invitations."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Phone number is empty")
    if re.search(r"[^\d\s+()\-]", value):
        raise ValueError("Invalid phone number")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Expected a Russian phone number")
    return digits


def validate_contact(
    *,
    phone: str,
    auth_date: str,
    signature: str,
    max_user_id: str,
    bot_token: str,
    now: int | None = None,
) -> str:
    """Verify a MAX Bridge requestContact() response and return normalized phone."""
    if not _is_contact_fresh(auth_date, now=now):
        age_seconds = None
        if isinstance(auth_date, str) and re.fullmatch(
            r"[0-9]{10}|[0-9]{13}", auth_date
        ):
            issued_at = int(auth_date) / (1000 if len(auth_date) == 13 else 1)
            age_seconds = round((time.time() if now is None else now) - issued_at)
        logger.info(
            "max_contact_rejected",
            extra={
                "event": "max_contact_rejected",
                "error_code": "expired_max_contact_data",
                "timestamp_length": (
                    len(auth_date) if isinstance(auth_date, str) else None
                ),
                "age_seconds": age_seconds,
            },
        )
        raise ValueError("Expired MAX contact data")
    normalized = normalize_phone_number(phone)
    signed_phone = phone.replace("+", "")
    signed_text = f"authDate={auth_date}\nphone={signed_phone}\nuserId={max_user_id}"
    expected = hmac.new(
        bot_token.encode("utf-8"), signed_text.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid MAX contact signature")
    return normalized


def validate_bot_contact(
    *,
    vcf_info: str,
    signature: str,
    sender_user_id: str,
    contact_user_id: str | None,
    bot_token: str,
) -> str:
    """Verify a MAX request_contact attachment before linking its phone."""
    if not vcf_info or len(vcf_info) > 16_384 or not signature:
        raise ValueError("MAX contact verification data is missing")
    if contact_user_id is None or str(contact_user_id) != str(sender_user_id):
        raise ValueError("The shared contact is not the message sender")
    vcard = vcf_info.replace("\\r\\n", "\r\n")
    expected = hmac.new(
        bot_token.encode("utf-8"), vcard.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise ValueError("Invalid MAX contact signature")
    lines = vcard.replace("\r\n", "\n").split("\n")
    numbers = [
        line.split(":", 1)[1]
        for line in lines
        if re.match(r"^TEL(?:;[^:]*)?:", line, re.IGNORECASE)
    ]
    if len(numbers) != 1:
        raise ValueError("The shared contact must contain one phone number")
    return normalize_phone_number(numbers[0])


async def get_or_create_user(
    session: AsyncSession, max_user_id: str, *, full_name: str | None = None
) -> User:
    user = await session.scalar(select(User).where(User.max_user_id == max_user_id))
    if user is not None:
        return user
    user = User(id=uuid4(), max_user_id=max_user_id, kind="unassigned", full_name=full_name)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(User).where(User.max_user_id == max_user_id))
        if existing is None:
            raise
        return existing
    return user


async def apply_verified_contact(
    session: AsyncSession,
    user: User,
    *,
    phone: str,
    auth_date: str,
    signature: str,
    bot_token: str,
) -> str:
    """Attach a MAX-verified phone to a user; changing it is not supported yet."""
    if user.max_user_id is None:
        raise ValueError("MAX account is not connected")
    number = validate_contact(
        phone=phone,
        auth_date=auth_date,
        signature=signature,
        max_user_id=user.max_user_id,
        bot_token=bot_token,
    )
    await link_verified_phone(session, user, number)
    return number


async def link_verified_phone(
    session: AsyncSession, user: User, number: str
) -> None:
    """Store a phone already verified by MAX Bridge or a signed bot contact."""
    number = normalize_phone_number(number)
    locked_user = await session.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_user is None:
        raise ValueError("MAX account is not connected")
    if user.phone_number is not None and user.phone_number != number:
        raise ValueError("Phone number change is not supported")
    owner = await session.scalar(select(User).where(User.phone_number == number))
    if owner is not None and owner.id != user.id:
        raise ValueError("Phone number is already linked to another account")
    user.phone_number = number
    user.phone_verified_at = datetime.now(UTC)
    await session.flush()


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    init_data: Annotated[str | None, Header(alias="X-Max-Init-Data")] = None,
) -> User:
    if init_data is None:
        request.state.error_code = "max_launch_data_required"
        request.state.exception_message = "MAX launch data required"
        raise HTTPException(status_code=401, detail="MAX launch data required")
    try:
        profile = validate_init_data(init_data, load_bot_token())
    except ValueError as error:
        request.state.error_code = {
            "Expired MAX launch data": "expired_max_launch_data",
            "Invalid MAX launch signature": "invalid_max_launch_signature",
        }.get(str(error), "invalid_max_launch_data")
        request.state.exception_message = safe_exception_message(error)
        raise HTTPException(status_code=401, detail=str(error)) from error
    full_name = " ".join(
        part for part in (profile.get("first_name"), profile.get("last_name")) if part
    ) or None
    return await get_or_create_user(session, str(profile["id"]), full_name=full_name)
