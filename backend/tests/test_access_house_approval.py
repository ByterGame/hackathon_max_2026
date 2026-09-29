"""Regression tests for approving a house without breaking its foreign key."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import House
from src.domain.access.company import decide_house_request


class HouseApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_house_is_inserted_before_request_points_to_it(self) -> None:
        company_id = uuid4()
        request = SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            registration_request_id=None,
            entered_address="Владивосток, Морская, 7",
            entrance_count=2,
            apartment_count=40,
            resolved_house_id=None,
            status="open",
            outcome=None,
            decision_note=None,
            decided_by=None,
            decided_at=None,
            updated_at=None,
            version=1,
        )
        actor = SimpleNamespace(id=uuid4(), kind="support")
        inserted: list[House] = []

        async def flush_house(rows: list[House]) -> None:
            self.assertIsNone(request.resolved_house_id)
            inserted.extend(rows)

        session = SimpleNamespace(
            scalar=AsyncMock(return_value=None),
            add=Mock(),
            flush=AsyncMock(side_effect=flush_house),
        )
        with (
            patch(
                "src.domain.access.company.require_row", new_callable=AsyncMock
            ) as require_row,
            patch("src.domain.access.company.audit"),
            patch(
                "src.domain.access.company.commit_or_conflict",
                new_callable=AsyncMock,
            ),
        ):
            require_row.return_value = request
            closed, house = await decide_house_request(
                session,
                actor,
                request_id=request.id,
                outcome="approved",
                decision_note="Дом проверен",
                proposed_address_key=None,
                entrance_count=2,
                apartment_count=40,
            )

        self.assertIs(closed, request)
        self.assertIs(house, inserted[0])
        self.assertEqual(request.resolved_house_id, house.id)
        session.flush.assert_awaited_once_with([house])


if __name__ == "__main__":
    unittest.main()
