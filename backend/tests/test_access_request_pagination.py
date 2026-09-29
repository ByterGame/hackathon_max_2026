"""Очередь заявок не обрывается на первой сотне и проверяет права поддержки."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic import ValidationError

from src.domain.access.queries import list_requests, list_requests_page
from src.domain.access.rules import AccessRuleError
from src.gen.access.api.list_requests import QueryParams


def registration() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        applicant_user_id=uuid4(),
        status="open",
        outcome=None,
        decision_note=None,
        decided_by=None,
        decided_at=None,
        cancel_requested_by=None,
        discussion=[],
        created_at=now,
        updated_at=now,
        phone_number="79991234567",
        proposed_company_name="УК Тест",
        free_text="Регистрация компании",
    )


class AccessRequestPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_support_page_has_count_and_database_offset(self) -> None:
        row = registration()
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=41),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])),
        )
        actor = SimpleNamespace(id=uuid4(), kind="support")

        page = await list_requests_page(
            session,
            actor,
            kind="company_registration",
            limit=20,
            offset=20,
            support_view=True,
        )

        self.assertEqual((page["total"], page["limit"], page["offset"]), (41, 20, 20))
        self.assertEqual(page["items"][0]["id"], str(row.id))
        statement = session.scalars.await_args.args[0]
        self.assertIn("LIMIT", str(statement))
        self.assertIn("OFFSET", str(statement))
        self.assertIn(20, statement.compile().params.values())

    async def test_support_queue_rejects_an_applicant_role(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock(), scalars=AsyncMock())
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        with self.assertRaises(AccessRuleError) as raised:
            await list_requests_page(
                session,
                actor,
                kind="house_addition",
                limit=20,
                support_view=True,
            )
        self.assertEqual(raised.exception.status_code, 403)
        session.scalar.assert_not_awaited()

    async def test_existing_list_keeps_hundred_item_default(self) -> None:
        row = registration()
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=1),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])),
        )
        items = await list_requests(
            session,
            SimpleNamespace(id=uuid4(), kind="support"),
            kind="company_registration",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(session.scalars.await_args.args[0].compile().params["param_1"], 100)

    async def test_my_requests_page_combines_kinds_without_other_users(self) -> None:
        row = registration()
        actor = SimpleNamespace(id=row.applicant_user_id, kind="resident")
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=101),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("company_registration", row.id)])),
            get=AsyncMock(return_value=row),
        )
        page = await list_requests_page(
            session, actor, kind="mine", limit=20, offset=100
        )
        self.assertEqual((page["total"], page["limit"], page["offset"]), (101, 20, 100))
        self.assertEqual(page["items"][0]["kind"], "company_registration")
        statement = session.execute.await_args.args[0]
        sql = str(statement)
        self.assertIn("UNION ALL", sql)
        self.assertIn("applicant_user_id", sql)
        self.assertIn("OFFSET", sql)
        self.assertIn(100, statement.compile().params.values())

    async def test_my_requests_cannot_be_opened_as_support_queue(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock())
        with self.assertRaises(AccessRuleError) as raised:
            await list_requests_page(
                session,
                SimpleNamespace(id=uuid4(), kind="support"),
                kind="mine", support_view=True,
            )
        self.assertEqual(raised.exception.code, "invalid_filter")
        session.scalar.assert_not_awaited()

    def test_query_rejects_more_than_hundred_or_negative_offset(self) -> None:
        self.assertEqual(QueryParams(request_kind="mine").request_kind.value, "mine")
        with self.assertRaises(ValidationError):
            QueryParams(request_kind="company_registration", limit=101)
        with self.assertRaises(ValidationError):
            QueryParams(request_kind="house_addition", offset=-1)


if __name__ == "__main__":
    unittest.main()
