"""Verify a phone shared through MAX Bridge."""

from typing import Annotated

from fastapi import Body, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import apply_verified_contact, get_current_user
from src.core.config import load_bot_token
from src.db.models import User
from src.db.session import get_session
from src.domain.access.common import bind_staff_by_verified_phone
from src.domain.access.rules import AccessRuleError
from src.gen.auth.api import verify_contact as models


async def verify_contact(
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
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AccessRuleError as error:
        await session.rollback()
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Phone number is already linked") from error
