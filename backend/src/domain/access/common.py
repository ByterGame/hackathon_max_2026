"""Общие проверки и транзакционные помощники для сценариев доступа."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, bindparam, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditEvent, OutboxEvent, StaffAssignment, User
from src.domain.access.rules import AccessRuleError, require_verified_phone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def lock_phone_role(session: AsyncSession, normalized_phone: str) -> None:
    """Serialize cross-role invitations for a phone until transaction end."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                bindparam("phone_role_key", int(normalized_phone), type_=BigInteger)
            )
        )
    )


async def require_row(
    session: AsyncSession,
    model: type[Any],
    row_id: UUID,
    *,
    for_update: bool = False,
) -> Any:
    statement = select(model).where(model.id == row_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(statement)
    if row is None:
        raise AccessRuleError(404, "not_found", "Запись не найдена")
    return row


async def bind_staff_by_verified_phone(
    session: AsyncSession, user: User
) -> list[StaffAssignment]:
    """Привязать заранее выданные назначения после проверки номера через MAX.

    Функция не делает commit: вызывающая операция включает привязку в свою
    транзакцию. Не создаёт роль сотрудника у уже подтверждённого жителя.
    """
    locked_user = await session.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_user is None:
        raise AccessRuleError(404, "user_not_found", "Аккаунт не найден")
    if locked_user.phone_number is None or locked_user.phone_verified_at is None:
        return []
    phone = require_verified_phone(locked_user)
    assignments = list(
        (
            await session.scalars(
                select(StaffAssignment)
                .where(
                    StaffAssignment.phone_number == phone,
                    StaffAssignment.user_id.is_(None),
                    StaffAssignment.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
    )
    if not assignments:
        return []
    if locked_user.kind not in {"unassigned", "employee"}:
        raise AccessRuleError(
            403, "role_conflict", "Этот аккаунт не может получить роль сотрудника УК"
        )
    for assignment in assignments:
        assignment.user_id = locked_user.id
        assignment.bound_at = utcnow()
    locked_user.kind = "employee"
    await session.flush()
    return assignments


async def require_staff(
    session: AsyncSession,
    actor: User,
    company_id: UUID,
    permission: str | None = None,
) -> StaffAssignment | None:
    # Администратор управляет всеми УК, но не становится сотрудником каждой из них.
    if actor.kind == "admin":
        return None
    if actor.kind not in {"unassigned", "employee"}:
        raise AccessRuleError(403, "staff_required", "Требуется доступ сотрудника УК")
    await bind_staff_by_verified_phone(session, actor)
    if actor.kind != "employee":
        raise AccessRuleError(403, "staff_required", "Требуется доступ сотрудника УК")
    assignment = await session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.company_id == company_id,
            StaffAssignment.user_id == actor.id,
            StaffAssignment.revoked_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if assignment is None:
        raise AccessRuleError(
            403, "staff_required", "Нет доступа к этой управляющей компании"
        )
    if permission and not getattr(assignment, permission):
        raise AccessRuleError(
            403, "permission_required", "Недостаточно прав сотрудника УК"
        )
    return assignment


def require_support(actor: User) -> None:
    if actor.kind not in {"support", "admin"}:
        raise AccessRuleError(
            403,
            "support_required",
            "Требуется доступ поддержки или администратора",
        )


async def commit_or_conflict(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AccessRuleError(
            409, "conflict", "Данные изменились или уже существуют"
        ) from exc


def audit(
    session: AsyncSession,
    *,
    entity_kind: str,
    entity_id: UUID,
    action: str,
    actor_id: UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            entity_kind=entity_kind,
            entity_id=entity_id,
            action=action,
            actor_user_id=actor_id,
            before_data=before,
            after_data=after,
        )
    )
    # В той же транзакции сохраняем только идентификаторы события. Получателей
    # и видимость будущий доставщик пересчитает по текущим правам доступа.
    session.add(
        OutboxEvent(
            event_kind=f"access.{entity_kind}.{action}",
            subject_kind=entity_kind,
            subject_id=entity_id,
            payload={"actor_user_id": str(actor_id)},
        )
    )
