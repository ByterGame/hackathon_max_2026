"""Mark-all acts on the complete inbox and never on another user's rows."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.domain.notifications.service import mark_all_read
from src.main import create_app


class MarkAllNotificationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_marks_entire_actor_inbox_in_one_statement(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(rowcount=137)),
            commit=AsyncMock(),
        )

        count = await mark_all_read(session, actor)

        self.assertEqual(count, 137)
        session.commit.assert_awaited_once()
        statement = session.execute.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("UPDATE system.notifications", sql)
        self.assertIn("recipient_user_id", sql)
        self.assertIn("read_at IS NULL", sql)
        self.assertEqual(statement.compile().params["recipient_user_id_1"], actor.id)


class MarkAllNotificationsRouteTests(unittest.TestCase):
    def test_route_is_registered(self) -> None:
        methods = create_app().openapi()["paths"]["/notifications/mark_all_read"]
        self.assertIn("post", methods)
