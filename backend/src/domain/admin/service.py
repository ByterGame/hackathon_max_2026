"""Приглашения операторов поддержки и отзыв служебных ролей."""

from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AuditEvent,
    ResidentOffer,
    StaffAssignment,
    SupportInvitation,
    User,
)
from src.domain.access.common import commit_or_conflict, lock_phone_role, utcnow
from src.domain.access.rules import (
    AccessRuleError,
    normalize_phone,
    require_verified_phone,
)


def _audit(
    session: AsyncSession,
    *,
    entity_kind: str,
    entity_id: UUID,
    action: str,
    actor_id: UUID,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind=entity_kind,
            entity_id=entity_id,
            action=action,
            actor_user_id=actor_id,
            before_data=before,
            after_data=after,
        )
    )


async def require_admin(session: AsyncSession, actor: User) -> User:
    """Recheck current role in the database before any privileged mutation."""
    admin = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if admin is None or admin.kind != "admin":
        raise AccessRuleError(
            403, "admin_required", "Действие доступно только администратору"
        )
    return admin


async def invite_support(
    session: AsyncSession,
    actor: User,
    *,
    phone_number: str,
) -> SupportInvitation:
    admin = await require_admin(session, actor)
    phone = normalize_phone(phone_number)
    await lock_phone_role(session, phone)
    matching_user = await session.scalar(
        select(User)
        .where(User.phone_number == phone)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if matching_user is not None and matching_user.kind != "unassigned":
        raise AccessRuleError(
            409, "role_conflict", "У этого номера уже есть другая роль"
        )
    pending_staff = await session.scalar(
        select(StaffAssignment.id).where(
            StaffAssignment.phone_number == phone,
            StaffAssignment.revoked_at.is_(None),
        )
    )
    pending_resident = await session.scalar(
        select(ResidentOffer.id).where(
            ResidentOffer.phone_number == phone,
            ResidentOffer.status == "pending",
        )
    )
    if pending_staff is not None or pending_resident is not None:
        raise AccessRuleError(
            409,
            "role_conflict",
            "На этот номер уже выдано приглашение другой роли",
        )
    invitation = await session.scalar(
        select(SupportInvitation)
        .where(
            SupportInvitation.phone_number == phone,
            SupportInvitation.accepted_at.is_(None),
            SupportInvitation.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if invitation is not None:
        return invitation
    invitation = SupportInvitation(
        id=uuid4(),
        phone_number=phone,
        invited_by=admin.id,
        created_at=utcnow(),
        version=1,
    )
    session.add(invitation)
    _audit(
        session,
        entity_kind="support_invitation",
        entity_id=invitation.id,
        action="created",
        actor_id=admin.id,
        after={"phone_number": phone},
    )
    await commit_or_conflict(session)
    return invitation


async def pending_support_invitations(
    session: AsyncSession, actor: User
) -> list[SupportInvitation]:
    """Show offers only to the MAX account that verified the target phone."""
    current = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .execution_options(populate_existing=True)
    )
    if (
        current is None
        or current.max_user_id is None
        or current.kind != "unassigned"
        or current.phone_number is None
        or current.phone_verified_at is None
    ):
        return []
    phone = require_verified_phone(current)
    return list(
        (
            await session.scalars(
                select(SupportInvitation)
                .where(
                    SupportInvitation.phone_number == phone,
                    SupportInvitation.accepted_at.is_(None),
                    SupportInvitation.revoked_at.is_(None),
                )
                .order_by(SupportInvitation.created_at, SupportInvitation.id)
            )
        ).all()
    )


async def accept_support_invitation(
    session: AsyncSession,
    actor: User,
    *,
    invitation_id: UUID,
) -> User:
    """Explicit acceptance after the recipient verifies their own MAX phone."""
    # Read the invitation first, then lock the user before the invitation. The
    # same lock order is used when an administrator creates or revokes an offer.
    initial = await session.get(SupportInvitation, invitation_id)
    if initial is None:
        raise AccessRuleError(404, "not_found", "Приглашение не найдено")
    recipient = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if recipient is None:
        raise AccessRuleError(404, "user_not_found", "Аккаунт не найден")
    if recipient.max_user_id is None:
        raise AccessRuleError(403, "max_account_required", "Войдите через MAX")
    phone = require_verified_phone(recipient)
    if initial.phone_number != phone:
        raise AccessRuleError(404, "not_found", "Приглашение не найдено")
    invitation = await session.scalar(
        select(SupportInvitation)
        .where(SupportInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation is None or invitation.phone_number != phone:
        raise AccessRuleError(404, "not_found", "Приглашение не найдено")
    if invitation.accepted_at is not None or invitation.revoked_at is not None:
        raise AccessRuleError(409, "invitation_closed", "Приглашение уже закрыто")
    if recipient.kind != "unassigned":
        raise AccessRuleError(
            409, "role_conflict", "Этот аккаунт уже имеет другую роль"
        )
    now = utcnow()
    invitation.accepted_by = recipient.id
    invitation.accepted_at = now
    invitation.version += 1
    recipient.kind = "support"
    recipient.version += 1
    _audit(
        session,
        entity_kind="support_invitation",
        entity_id=invitation.id,
        action="accepted",
        actor_id=recipient.id,
        after={"user_id": str(recipient.id)},
    )
    _audit(
        session,
        entity_kind="user",
        entity_id=recipient.id,
        action="support_granted",
        actor_id=recipient.id,
        before={"kind": "unassigned"},
        after={"kind": "support"},
    )
    await commit_or_conflict(session)
    return recipient


def _close_invitation(
    session: AsyncSession,
    invitation: SupportInvitation,
    *,
    admin_id: UUID,
) -> None:
    invitation.revoked_by = admin_id
    invitation.revoked_at = utcnow()
    invitation.version += 1
    _audit(
        session,
        entity_kind="support_invitation",
        entity_id=invitation.id,
        action="revoked",
        actor_id=admin_id,
    )


async def revoke_support_invitation(
    session: AsyncSession,
    actor: User,
    *,
    invitation_id: UUID,
) -> SupportInvitation:
    admin = await require_admin(session, actor)
    invitation = await session.scalar(
        select(SupportInvitation)
        .where(SupportInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation is None:
        raise AccessRuleError(404, "not_found", "Приглашение не найдено")
    if invitation.accepted_at is not None or invitation.revoked_at is not None:
        raise AccessRuleError(409, "invitation_closed", "Приглашение уже закрыто")
    _close_invitation(session, invitation, admin_id=admin.id)
    await commit_or_conflict(session)
    return invitation


async def revoke_support(
    session: AsyncSession,
    actor: User,
    *,
    target_user_id: UUID,
) -> User:
    admin = await require_admin(session, actor)
    target = await session.scalar(
        select(User)
        .where(User.id == target_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise AccessRuleError(404, "user_not_found", "Аккаунт не найден")
    if target.kind != "support":
        raise AccessRuleError(409, "role_conflict", "У аккаунта нет роли поддержки")
    target.kind = "unassigned"
    target.version += 1
    if target.phone_number:
        pending = (
            await session.scalars(
                select(SupportInvitation)
                .where(
                    SupportInvitation.phone_number == target.phone_number,
                    SupportInvitation.accepted_at.is_(None),
                    SupportInvitation.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
        for invitation in pending:
            _close_invitation(session, invitation, admin_id=admin.id)
    _audit(
        session,
        entity_kind="user",
        entity_id=target.id,
        action="support_revoked",
        actor_id=admin.id,
        before={"kind": "support"},
        after={"kind": "unassigned"},
    )
    await commit_or_conflict(session)
    return target


async def revoke_admin(
    session: AsyncSession,
    actor: User,
    *,
    target_user_id: UUID,
) -> User:
    """Revoke one administrator while keeping at least one active administrator."""
    # Lock all current admins in stable order. Concurrent revocations then see
    # the updated role count and cannot both remove the final administrator.
    admins = list(
        (
            await session.scalars(
                select(User)
                .where(User.kind == "admin")
                .order_by(User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    admin_by_id = {admin.id: admin for admin in admins}
    if actor.id not in admin_by_id:
        raise AccessRuleError(
            403, "admin_required", "Действие доступно только администратору"
        )
    target = admin_by_id.get(target_user_id)
    if target is None:
        raise AccessRuleError(
            409, "role_conflict", "У аккаунта нет роли администратора"
        )
    if len(admins) <= 1:
        raise AccessRuleError(
            409, "last_admin", "Нельзя отозвать доступ последнего администратора"
        )
    target.kind = "unassigned"
    target.version += 1
    pending = (
        await session.scalars(
            select(SupportInvitation)
            .where(
                or_(
                    SupportInvitation.invited_by == target.id,
                    SupportInvitation.phone_number == target.phone_number,
                ),
                SupportInvitation.accepted_at.is_(None),
                SupportInvitation.revoked_at.is_(None),
            )
            .order_by(SupportInvitation.id)
            .with_for_update()
        )
    ).all()
    for invitation in pending:
        _close_invitation(session, invitation, admin_id=actor.id)
    _audit(
        session,
        entity_kind="user",
        entity_id=target.id,
        action="admin_revoked",
        actor_id=actor.id,
        before={"kind": "admin"},
        after={"kind": "unassigned"},
    )
    await commit_or_conflict(session)
    return target
