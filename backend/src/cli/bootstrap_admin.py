"""Create an administrator from a trusted MAX user ID.

Run only from the server after migrations:
    python -m src.cli.bootstrap_admin <max_user_id>
"""

import argparse
import asyncio
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import load_database_config
from src.db.models import AuditEvent, User
from src.db.session import create_database_engine, create_session_factory


async def bootstrap_in_session(session: AsyncSession, max_user_id: str) -> User:
    """Promote only a neutral or support account; never cross resident/staff roles."""
    if (
        not isinstance(max_user_id, str)
        or re.fullmatch(r"[1-9][0-9]*", max_user_id) is None
    ):
        raise ValueError("MAX user ID must be a positive decimal integer")
    user = await session.scalar(
        select(User)
        .where(User.max_user_id == max_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        user = User(id=uuid4(), max_user_id=max_user_id, kind="admin", version=1)
        session.add(user)
        before_kind = None
    elif user.kind in {"unassigned", "support"}:
        before_kind = user.kind
        user.kind = "admin"
        user.version += 1
    elif user.kind == "admin":
        return user
    else:
        raise ValueError(
            "Resident and employee accounts require an explicit role transition"
        )
    session.add(
        AuditEvent(
            id=uuid4(),
            entity_kind="user",
            entity_id=user.id,
            action="admin_bootstrapped",
            actor_user_id=None,
            before_data={"kind": before_kind} if before_kind else None,
            after_data={"kind": "admin"},
        )
    )
    await session.commit()
    return user


async def bootstrap(max_user_id: str) -> str:
    engine = create_database_engine(load_database_config())
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            user = await bootstrap_in_session(session, max_user_id)
            return str(user.id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("max_user_id", help="Trusted administrator MAX ID")
    args = parser.parse_args()
    admin_id = asyncio.run(bootstrap(args.max_user_id))
    print(f"Administrator ready: {admin_id}")


if __name__ == "__main__":
    main()
