"""A new resident report may target only the resident's active home location."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from src.db.models import IssueCard, IssueTarget
from src.domain.issues.service import IssueError, create_card


class IssueCreationScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="resident")
        self.house = SimpleNamespace(id=uuid4())
        self.category = SimpleNamespace(id=uuid4(), is_active=True)
        self.apartment = SimpleNamespace(id=uuid4(), entrance_number=2)
        self.session = SimpleNamespace(
            get=AsyncMock(return_value=self.category),
            add=Mock(),
            flush=AsyncMock(),
        )

    async def _create(self, scope: str, *, apartment_id=None, grants=None):
        if grants is None:
            grants = {self.apartment.id: self.apartment}
        with (
            patch("src.domain.issues.service._house", new=AsyncMock(return_value=self.house)),
            patch(
                "src.domain.issues.service._active_resident_apartments",
                new=AsyncMock(return_value=grants),
            ),
        ):
            return await create_card(
                self.session,
                self.actor,
                house_id=self.house.id,
                category_id=self.category.id,
                title="Лифт",
                description="Лифт не работает",
                scope=scope,
                apartment_id=apartment_id,
            )

    def _targets(self):
        return [
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], IssueTarget)
        ]

    async def test_apartment_scope_uses_approved_apartment_id(self) -> None:
        card = await self._create("apartment")
        self.assertIsInstance(card, IssueCard)
        self.assertFalse(card.scope_all_house)
        self.assertEqual(len(self._targets()), 1)
        self.assertEqual(self._targets()[0].apartment_id, self.apartment.id)
        self.assertIsNone(self._targets()[0].entrance_number)

    async def test_entrance_scope_uses_approved_apartment_entrance(self) -> None:
        await self._create("entrance")
        self.assertEqual(len(self._targets()), 1)
        self.assertEqual(self._targets()[0].entrance_number, 2)
        self.assertIsNone(self._targets()[0].apartment_id)

    async def test_house_scope_needs_approved_access_but_has_no_targets(self) -> None:
        card = await self._create("house")
        self.assertTrue(card.scope_all_house)
        self.assertEqual(self._targets(), [])

    async def test_missing_or_foreign_apartment_is_rejected_before_write(self) -> None:
        for grants, apartment_id, expected in (
            ({}, None, "house_access_denied"),
            ({self.apartment.id: self.apartment}, uuid4(), "apartment_access_denied"),
        ):
            with self.subTest(expected=expected):
                self.session.add.reset_mock()
                with self.assertRaises(IssueError) as raised:
                    await self._create("apartment", apartment_id=apartment_id, grants=grants)
                self.assertEqual(raised.exception.code, expected)
                self.session.add.assert_not_called()

    async def test_multiple_grants_require_explicit_owned_apartment(self) -> None:
        second = SimpleNamespace(id=uuid4(), entrance_number=4)
        grants = {self.apartment.id: self.apartment, second.id: second}
        with self.assertRaises(IssueError) as raised:
            await self._create("entrance", grants=grants)
        self.assertEqual(raised.exception.code, "apartment_selection_required")
        await self._create("entrance", apartment_id=second.id, grants=grants)
        self.assertEqual(self._targets()[0].entrance_number, 4)

    async def test_old_grant_without_entrance_cannot_target_entrance(self) -> None:
        self.apartment.entrance_number = None
        with self.assertRaises(IssueError) as raised:
            await self._create("entrance")
        self.assertEqual(raised.exception.code, "entrance_unknown")
        self.session.add.assert_not_called()

    async def test_house_scope_rejects_superfluous_apartment_selection(self) -> None:
        with self.assertRaises(IssueError) as raised:
            await self._create("house", apartment_id=self.apartment.id)
        self.assertEqual(raised.exception.code, "invalid_scope")
        self.session.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
