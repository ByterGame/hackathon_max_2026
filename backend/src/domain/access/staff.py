"""Персональные назначения сотрудников управляющей компании."""

from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Company,
    ResidentOffer,
    StaffAssignment,
    SupportInvitation,
    User,
)
from src.domain.access.common import (
    audit,
    commit_or_conflict,
    lock_phone_role,
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
    await lock_phone_role(session, phone)
    competing_support = await session.scalar(
        select(SupportInvitation.id).where(
            SupportInvitation.phone_number == phone,
            SupportInvitation.accepted_at.is_(None),
            SupportInvitation.revoked_at.is_(None),
        )
    )
    competing_resident = await session.scalar(
        select(ResidentOffer.id).where(
            ResidentOffer.phone_number == phone,
            ResidentOffer.status == "pending",
        )
    )
    if competing_support is not None or competing_resident is not None:
        raise AccessRuleError(
            409,
            "role_conflict",
            "На этот номер уже выдано приглашение другой роли",
        )
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
    # require_staff may bind an unclaimed assignment to this same user.
    initial_user_id = initial.user_id
    # Phone binding locks the user before the assignment. Keep the same order
    # here so a concurrent bind cannot deadlock with a revocation.
    if initial_user_id is not None:
        employee = await require_row(
            session, User, initial_user_id, for_update=True
        )
    else:
        employee = await session.scalar(
            select(User)
            .where(
                User.phone_number == normalize_phone(initial.phone_number),
                User.phone_verified_at.is_not(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    assignment = await require_row(
        session, StaffAssignment, assignment_id, for_update=True
    )
    if assignment.user_id != initial_user_id:
        raise AccessRuleError(
            409, "assignment_changed", "Назначение изменилось; повторите действие"
        )
    if assignment.revoked_at is not None:
        raise AccessRuleError(409, "already_revoked", "Доступ сотрудника уже отозван")
    if actor.kind == "employee" and assignment.user_id == actor.id:
        # Serialize self-revocations in one company: two employees must not
        # both see the other as active and revoke themselves concurrently.
        await require_row(session, Company, assignment.company_id, for_update=True)
        remaining_staff = await session.scalar(
            select(StaffAssignment.id)
            .join(User, User.id == StaffAssignment.user_id)
            .where(
                StaffAssignment.company_id == assignment.company_id,
                StaffAssignment.id != assignment.id,
                StaffAssignment.revoked_at.is_(None),
                User.id != actor.id,
                User.kind == "employee",
                User.phone_verified_at.is_not(None),
            )
            .limit(1)
        )
        if remaining_staff is None:
            raise AccessRuleError(
                409,
                "last_staff_self_revoke",
                "Последний сотрудник УК не может отозвать свой доступ. "
                "Сначала назначьте другого сотрудника или обратитесь к администратору.",
            )
    now = utcnow()
    assignment.revoked_at = now
    assignment.version += 1
    if employee is not None and employee.kind == "employee":
        identities = [StaffAssignment.user_id == employee.id]
        if employee.phone_number and employee.phone_verified_at is not None:
            identities.append(
                and_(
                    StaffAssignment.user_id.is_(None),
                    StaffAssignment.phone_number
                    == normalize_phone(employee.phone_number),
                )
            )
        other_assignment = await session.scalar(
            select(StaffAssignment.id).where(
                StaffAssignment.id != assignment.id,
                StaffAssignment.revoked_at.is_(None),
                or_(*identities),
            )
        )
        if other_assignment is None:
            employee.kind = "unassigned"
            employee.version += 1
            employee.updated_at = now
            audit(
                session,
                entity_kind="user",
                entity_id=employee.id,
                action="employee_revoked",
                actor_id=actor.id,
                before={"kind": "employee"},
                after={"kind": "unassigned"},
            )
    audit(
        session,
        entity_kind="staff_assignment",
        entity_id=assignment.id,
        action="revoked",
        actor_id=actor.id,
    )
    await commit_or_conflict(session)
    return assignment
