"""Персональные назначения сотрудников управляющей компании."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Company, StaffAssignment, User
from src.domain.access.common import (
    audit,
    commit_or_conflict,
    require_row,
    require_staff,
    utcnow,
)
from src.domain.access.rules import AccessRuleError, normalize_phone


async def assign_staff(
    session: AsyncSession,
    actor: User,
    *,
    company_id: UUID,
    phone_number: str,
    can_manage_staff: bool,
    can_manage_residents: bool,
    can_manage_issues: bool,
) -> StaffAssignment:
    await require_row(session, Company, company_id)
    await require_staff(session, actor, company_id, "can_manage_staff")
    phone = normalize_phone(phone_number)
    matching_user = await session.scalar(
        select(User)
        .where(User.phone_number == phone, User.phone_verified_at.is_not(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if matching_user is not None and matching_user.kind not in {
        "unassigned",
        "employee",
    }:
        raise AccessRuleError(
            409, "role_conflict", "Этот человек не может стать сотрудником УК"
        )
    assignment = await session.scalar(
        select(StaffAssignment)
        .where(
            StaffAssignment.company_id == company_id,
            StaffAssignment.phone_number == phone,
            StaffAssignment.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if assignment is None:
        assignment = StaffAssignment(
            id=uuid4(),
            company_id=company_id,
            phone_number=phone,
            user_id=matching_user.id if matching_user else None,
            can_manage_staff=can_manage_staff,
            can_manage_residents=can_manage_residents,
            can_manage_issues=can_manage_issues,
            granted_by=actor.id,
            created_at=utcnow(),
            bound_at=utcnow() if matching_user else None,
            version=1,
        )
        session.add(assignment)
        action = "assigned"
    else:
        assignment.can_manage_staff = can_manage_staff
        assignment.can_manage_residents = can_manage_residents
        assignment.can_manage_issues = can_manage_issues
        assignment.version += 1
        action = "permissions_changed"
    if matching_user is not None:
        matching_user.kind = "employee"
    audit(
        session,
        entity_kind="staff_assignment",
        entity_id=assignment.id,
        action=action,
        actor_id=actor.id,
        after={
            "can_manage_staff": can_manage_staff,
            "can_manage_residents": can_manage_residents,
            "can_manage_issues": can_manage_issues,
        },
    )
    await commit_or_conflict(session)
    return assignment


async def revoke_staff(
    session: AsyncSession,
    actor: User,
    *,
    assignment_id: UUID,
) -> StaffAssignment:
    initial = await require_row(session, StaffAssignment, assignment_id)
    await require_staff(session, actor, initial.company_id, "can_manage_staff")
    assignment = await require_row(
        session, StaffAssignment, assignment_id, for_update=True
    )
    if assignment.revoked_at is not None:
        raise AccessRuleError(409, "already_revoked", "Доступ сотрудника уже отозван")
    assignment.revoked_at = utcnow()
    assignment.version += 1
    audit(
        session,
        entity_kind="staff_assignment",
        entity_id=assignment.id,
        action="revoked",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return assignment
