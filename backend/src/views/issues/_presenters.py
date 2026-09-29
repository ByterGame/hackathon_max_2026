"""Convert issue ORM rows to generated HTTP response models."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Apartment, AuditEvent, IssueCard, IssueMessage, IssueReport, IssueSupport, IssueTarget, User
from src.domain.issues.service import IssueError
from src.gen.issues.internal import issue_data as models


def raise_http_issue(error: IssueError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


async def card_model(
    session: AsyncSession, actor: User, card: IssueCard
) -> models.Card:
    support_count = await session.scalar(
        select(func.count()).select_from(IssueSupport).where(IssueSupport.card_id == card.id)
    )
    supported_by_me = await session.scalar(
        select(IssueSupport.user_id).where(
            IssueSupport.card_id == card.id, IssueSupport.user_id == actor.id
        )
    )
    return models.Card(
        id=card.id,
        house_id=card.house_id,
        author_user_id=card.author_user_id,
        category_id=card.category_id,
        title=card.title,
        summary_description=card.summary_description,
        status=card.status,
        close_result=card.close_result,
        current_note=card.current_note,
        scope_all_house=card.scope_all_house,
        support_count=support_count or 0,
        supported_by_me=supported_by_me is not None,
        version=card.version,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def target_model(target: IssueTarget, apartment: Apartment | None) -> models.Target:
    return models.Target(
        id=target.id,
        entrance_number=target.entrance_number,
        apartment_id=target.apartment_id,
        apartment_entrance_number=apartment.entrance_number if apartment else None,
        apartment_number=apartment.apartment_number if apartment else None,
    )


def report_model(report: IssueReport) -> models.Report:
    return models.Report(
        id=report.id,
        author_user_id=report.author_user_id,
        raw_description=report.raw_description,
        created_at=report.created_at,
    )


def message_model(message: IssueMessage) -> models.Message:
    return models.Message(
        id=message.id,
        author_user_id=message.author_user_id,
        kind=message.kind,
        body=message.body,
        created_at=message.created_at,
    )


def history_model(event: AuditEvent) -> models.HistoryEvent:
    after = event.after_data or {}
    source_id = after.get("source_card_id")
    return models.HistoryEvent(
        id=event.id,
        action=event.action,
        actor_user_id=event.actor_user_id,
        status=after.get("status"),
        note=after.get("note"),
        close_result=after.get("close_result"),
        source_card_id=UUID(source_id) if isinstance(source_id, str) else None,
        created_at=event.created_at,
    )
