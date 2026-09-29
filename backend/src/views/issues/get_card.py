"""Read one issue with reports and discussion."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import Apartment, AuditEvent, IssueMessage, IssueReport, IssueTarget, User
from src.db.session import get_session
from src.domain.issues.service import IssueError, get_visible_card, merged_card_ids
from src.gen.issues.api import get_card as models
from ._presenters import (
    card_model,
    history_model,
    message_model,
    raise_http_issue,
    report_model,
    target_model,
)


async def get_card(
    query: Annotated[models.QueryParams, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card = await get_visible_card(session, actor, query.card_id)
    except IssueError as error:
        raise_http_issue(error)
    targets = (
        await session.execute(
            select(IssueTarget, Apartment)
            .outerjoin(Apartment, Apartment.id == IssueTarget.apartment_id)
            .where(IssueTarget.card_id == card.id)
        )
    ).all()
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
    history = (
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
    return models.Response200(
        card=await card_model(session, actor, card),
        targets=[target_model(target, apartment) for target, apartment in targets],
        reports=[report_model(item) for item in reports],
        messages=[message_model(item) for item in messages],
        history=[history_model(item) for item in history],
    )
