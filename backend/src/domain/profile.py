"""Explicit, account-wide full names independent of MAX display names."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditEvent, ResidentRequest, User
from src.domain.access.common import commit_or_conflict
from src.domain.access.rules import AccessRuleError


OPEN_REQUEST_STATUSES = ("open", "reviewing", "needs_info")


def has_confirmed_full_name(user: User) -> bool:
    return bool(
        getattr(user, "full_name_is_manual", False)
        and getattr(user, "full_name_confirmed_at", None) is not None
        and getattr(user, "full_name", None)
    )


def normalize_full_name(value: str) -> str:
    if not isinstance(value, str):
        raise AccessRuleError(400, "name_required", "Укажите ФИО")
    name = " ".join(value.split())
    if not name:
        raise AccessRuleError(400, "name_required", "Укажите ФИО")
    if len(name) > 255:
        raise AccessRuleError(400, "name_too_long", "ФИО должно быть не длиннее 255 символов")
    return name


async def lock_profile_user(session: AsyncSession, actor: User) -> User:
    user = await session.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise AccessRuleError(404, "user_not_found", "Аккаунт не найден")
    return user


async def apply_full_name(
    session: AsyncSession, user: User, full_name: str
) -> None:
    """Stage a profile update and sync only unfinished applications in this transaction."""
    name = normalize_full_name(full_name)
    now = datetime.now(UTC)
    old_name = user.full_name
    was_confirmed = has_confirmed_full_name(user)
    if old_name != name or not user.full_name_is_manual or not was_confirmed:
        user.full_name = name
        user.full_name_is_manual = True
        user.full_name_confirmed_at = now
        user.updated_at = now
        user.version += 1
        session.add(
            AuditEvent(
                entity_kind="user",
                entity_id=user.id,
                action="full_name_confirmed" if not was_confirmed else "full_name_changed",
                actor_user_id=user.id,
                before_data={"full_name": old_name},
                after_data={"full_name": name},
            )
        )

    requests = (
        await session.scalars(
            select(ResidentRequest)
            .where(
                ResidentRequest.applicant_user_id == user.id,
                ResidentRequest.status.in_(OPEN_REQUEST_STATUSES),
            )
            .with_for_update()
        )
    ).all()
    for request in requests:
        if request.submitted_full_name == name:
            continue
        previous = request.submitted_full_name
        request.submitted_full_name = name
        request.updated_at = now
        request.version += 1
        session.add(
            AuditEvent(
                entity_kind="resident_request",
                entity_id=request.id,
                action="profile_name_synced",
                actor_user_id=user.id,
                before_data={"full_name": previous},
                after_data={"full_name": name},
            )
        )


async def resolve_request_full_name(
    session: AsyncSession, actor: User, submitted_name: str | None
) -> str:
    user = await lock_profile_user(session, actor)
    if not has_confirmed_full_name(user):
        if submitted_name is None:
            raise AccessRuleError(
                400,
                "name_confirmation_required",
                "Введите ФИО один раз для общего профиля перед подачей заявки",
            )
        name = normalize_full_name(submitted_name)
        await apply_full_name(session, user, name)
        return name
    if not user.full_name:
        raise AccessRuleError(409, "profile_name_missing", "Укажите ФИО в профиле")
    if submitted_name is not None and normalize_full_name(submitted_name) != user.full_name:
        raise AccessRuleError(
            409,
            "profile_name_mismatch",
            "В заявках используется ФИО из профиля. Сначала измените его явно в профиле",
        )
    return user.full_name


async def update_profile_name(
    session: AsyncSession, actor: User, *, full_name: str
) -> User:
    name = normalize_full_name(full_name)
    user = await lock_profile_user(session, actor)
    await apply_full_name(session, user, name)
    await commit_or_conflict(session)
    return user
