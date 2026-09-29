"""Verify a phone shared through MAX Bridge."""

from typing import Annotated

from fastapi import Body, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import apply_verified_contact, get_current_user
from src.core.config import load_bot_token
from src.core.logging import safe_exception_message
from src.db.models import User
from src.db.session import get_session
from src.domain.access.common import bind_staff_by_verified_phone
from src.domain.access.rules import AccessRuleError
from src.gen.auth.api import verify_contact as models


_CONTACT_ERROR_CODES = {
    "Expired MAX contact data": "expired_max_contact_data",
    "Invalid MAX contact signature": "invalid_max_contact_signature",
    "Phone number is empty": "phone_empty",
    "Invalid phone number": "invalid_phone",
    "Expected a Russian phone number": "invalid_phone",
    "MAX account is not connected": "max_account_missing",
    "Phone number change is not supported": "phone_change_not_supported",
    "Phone number is already linked to another account": "phone_conflict",
}


async def verify_contact(
    request: Request,
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        phone = await apply_verified_contact(
            session,
            actor,
            phone=body.phone,
            auth_date=body.auth_date,
            signature=body.signature,
            bot_token=load_bot_token(),
        )
        await bind_staff_by_verified_phone(session, actor)
        result = models.Response200(phone_number=phone, kind=actor.kind)
        await session.commit()
        return result
    except ValueError as error:
        await session.rollback()
        request.state.error_code = _CONTACT_ERROR_CODES.get(str(error), "invalid_contact")
        request.state.exception_message = safe_exception_message(
            error, sensitive_values=(body.phone, body.auth_date, body.signature)
        )
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AccessRuleError as error:
        await session.rollback()
        request.state.error_code = error.code
        request.state.exception_message = safe_exception_message(
            error, sensitive_values=(body.phone, body.auth_date, body.signature)
        )
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        request.state.error_code = "phone_conflict"
        request.state.exception_message = "Phone number is already linked"
        raise HTTPException(status_code=409, detail="Phone number is already linked") from error
