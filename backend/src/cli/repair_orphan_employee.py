"""Repair one employee account left without an active staff assignment.

Inspect first, then run with --apply after deploying the fixed revoke_staff code:
    python -m src.cli.repair_orphan_employee <max_user_id>
    python -m src.cli.repair_orphan_employee <max_user_id> --apply
"""

import argparse
import asyncio
import re
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import load_database_config
from src.db.models import AuditEvent, StaffAssignment, User
from src.db.session import create_database_engine, create_session_factory
from src.domain.access.common import lock_phone_role, utcnow


async def repair_in_session(
    session: AsyncSession, max_user_id: str, *, apply: bool = False
) -> str:
    if re.fullmatch(r"[1-9][0-9]*", max_user_id) is None:
        raise ValueError("MAX user ID must be a positive decimal integer")
    preliminary = await session.scalar(select(User).where(User.max_user_id == max_user_id))
    if preliminary is None:
        raise ValueError("MAX account not found")
    known_phone = preliminary.phone_number
    known_verified_at = preliminary.phone_verified_at
    if known_phone and known_verified_at is not None:
        await lock_phone_role(session, known_phone)
    user = await session.scalar(
        select(User)
        .where(User.max_user_id == max_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise ValueError("MAX account not found")
    if user.phone_number != known_phone or user.phone_verified_at != known_verified_at:
        raise ValueError("Phone changed during repair; retry the inspection")
    if user.kind != "employee":
        raise ValueError(f"Account is not an employee (current role: {user.kind})")
    identities = [StaffAssignment.user_id == user.id]
    if user.phone_number and user.phone_verified_at is not None:
        identities.append(
            and_(
                StaffAssignment.user_id.is_(None),
                StaffAssignment.phone_number == user.phone_number,
            )
        )
    active_assignment = await session.scalar(
        select(StaffAssignment.id).where(
            StaffAssignment.revoked_at.is_(None), or_(*identities)
        )
    )
    if active_assignment is not None:
        raise ValueError("Account still has an active staff assignment; role was not changed")
    if not apply:
        return f"Ready to change user {user.id} from employee to unassigned; rerun with --apply"
    user.kind = "unassigned"
    user.version += 1
    user.updated_at = utcnow()
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind="user",
            entity_id=user.id,
            action="orphan_employee_repaired",
            actor_user_id=None,
            before_data={"kind": "employee"},
            after_data={"kind": "unassigned", "reason": "No active staff assignment"},
        )
    )
    await session.commit()
    return f"Changed user {user.id} from employee to unassigned"


async def repair(max_user_id: str, *, apply: bool = False) -> str:
    engine = create_database_engine(load_database_config())
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            return await repair_in_session(session, max_user_id, apply=apply)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("max_user_id", help="MAX ID of the affected person")
    parser.add_argument("--apply", action="store_true", help="Apply the checked role correction")
    args = parser.parse_args()
    print(asyncio.run(repair(args.max_user_id, apply=args.apply)))


if __name__ == "__main__":
    main()
