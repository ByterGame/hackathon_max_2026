"""Create the first support operator from a known MAX user ID.

Run only by a trusted server administrator after the initial migration:
    python -m src.cli.bootstrap_support <max_user_id>
"""

import argparse
import asyncio
from uuid import uuid4

from sqlalchemy import select

from src.core.config import load_database_config
from src.db.models import User
from src.db.session import create_database_engine, create_session_factory


async def bootstrap(max_user_id: str) -> str:
    if not max_user_id.isdecimal() or int(max_user_id) <= 0:
        raise ValueError("MAX user ID must be a positive integer")
    engine = create_database_engine(load_database_config())
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            user = await session.scalar(
                select(User)
                .where(User.max_user_id == max_user_id)
                .with_for_update()
            )
            if user is None:
                user = User(
                    id=uuid4(), max_user_id=max_user_id, kind="support"
                )
                session.add(user)
            elif user.kind == "unassigned":
                user.kind = "support"
            elif user.kind != "support":
                raise ValueError(
                    "This MAX account already has a resident or employee role"
                )
            await session.commit()
            return str(user.id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("max_user_id", help="Trusted support operator MAX ID")
    args = parser.parse_args()
    support_id = asyncio.run(bootstrap(args.max_user_id))
    print(f"Support operator ready: {support_id}")


if __name__ == "__main__":
    main()
