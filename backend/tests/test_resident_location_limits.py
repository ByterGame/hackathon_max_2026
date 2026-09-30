"""Apartment numbers are bounded by the house-wide count, not each entrance."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch
from uuid import uuid4

from src.domain.access.resident import (
    _get_or_create_apartment,
    _validate_apartment_location,
    create_resident_offer,
    create_resident_request,
    update_resident_request,
)
from src.domain.access.rules import AccessRuleError


class ResidentLocationLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.house = SimpleNamespace(
            id=uuid4(), company_id=uuid4(), archived_at=None,
            entrance_count=4, apartment_count=80,
        )
        self.actor = SimpleNamespace(
            id=uuid4(), kind="unassigned", phone_number="79990000001",
            phone_verified_at=datetime.now(UTC),
        )
        self.session = SimpleNamespace()

    def test_last_apartment_in_any_entrance_is_valid(self) -> None:
        _validate_apartment_location(self.house, 80, 4)

    def test_unknown_total_does_not_block_apartment(self) -> None:
        self.house.apartment_count = None
        _validate_apartment_location(self.house, 800, 4)

    async def test_create_request_rejects_apartment_above_total(self) -> None:
        with patch(
            "src.domain.access.resident.require_row",
            new_callable=AsyncMock,
            return_value=self.house,
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await create_resident_request(
                    self.session, self.actor, house_id=self.house.id,
                    entrance_number=4, apartment_number=81,
                )
        self.assertEqual(caught.exception.code, "invalid_apartment")

    async def test_update_request_rejects_apartment_above_total(self) -> None:
        request = SimpleNamespace(
            id=uuid4(), applicant_user_id=self.actor.id,
            house_id=self.house.id, status="open",
        )
        with patch(
            "src.domain.access.resident.require_row",
            new_callable=AsyncMock,
            side_effect=[request, self.house],
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await update_resident_request(
                    self.session, self.actor, request_id=request.id,
                    entrance_number=4, apartment_number=81,
                )
        self.assertEqual(caught.exception.code, "invalid_apartment")

    async def test_offer_rejects_apartment_above_total(self) -> None:
        self.actor.kind = "employee"
        with (
            patch(
                "src.domain.access.resident.require_row",
                new_callable=AsyncMock,
                return_value=self.house,
            ),
            patch("src.domain.access.resident.require_staff", new_callable=AsyncMock),
        ):
            with self.assertRaises(AccessRuleError) as caught:
                await create_resident_offer(
                    self.session, self.actor, house_id=self.house.id,
                    entrance_number=4, apartment_number=81,
                    phone_number="79990000002", valid_to=None,
                )
        self.assertEqual(caught.exception.code, "invalid_apartment")

    async def test_approval_rechecks_current_house_total(self) -> None:
        with self.assertRaises(AccessRuleError) as caught:
            await _get_or_create_apartment(self.session, self.house, 81, 4)
        self.assertEqual(caught.exception.code, "invalid_apartment")


if __name__ == "__main__":
    unittest.main()
