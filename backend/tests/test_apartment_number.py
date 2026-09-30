"""Apartment numbers identify a home; new access requests also name an entrance."""

import unittest
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.db.models import Apartment, Base
from src.domain.access.resident import _get_or_create_apartment as access_apartment
from src.domain.access.rules import AccessRuleError
from src.domain.issues.rules import can_view_issue
from src.domain.issues.service import _get_or_create_apartment as issue_apartment
from src.domain.issues.service import _resident_locations
from src.gen.access.api.create_resident_offer import Request as OfferRequest
from src.gen.access.api.create_resident_request import Request as ResidentRequest
from src.gen.access.api.list_grants import GrantItem
from src.gen.issues.api.create_card import Request as CreateIssueRequest


class ApartmentContractTests(unittest.TestCase):
    def test_new_access_requests_require_entrance_and_issue_has_no_free_form_target(self):
        house_id = UUID(int=1)
        with self.assertRaises(ValidationError):
            ResidentRequest.model_validate(
                {"house_id": str(house_id), "full_name": "Иван Иванов", "apartment_number": 17}
            )
        with self.assertRaises(ValidationError):
            OfferRequest.model_validate(
                {"house_id": str(house_id), "phone_number": "79990000000", "apartment_number": 17}
            )
        resident = ResidentRequest.model_validate(
            {"house_id": str(house_id), "full_name": "Иван Иванов", "entrance_number": 2, "apartment_number": 17}
        )
        offer = OfferRequest.model_validate(
            {"house_id": str(house_id), "phone_number": "79990000000", "entrance_number": 2, "apartment_number": 17}
        )
        self.assertEqual(resident.entrance_number, 2)
        self.assertEqual(offer.entrance_number, 2)
        issue = CreateIssueRequest.model_validate({
            "house_id": str(house_id),
            "category_id": str(UUID(int=4)),
            "title": "Лифт",
            "description": "Не работает лифт",
            "scope": "apartment",
        })
        self.assertFalse(hasattr(issue, "target_apartments"))
        self.assertFalse(hasattr(issue, "target_entrances"))

    def test_grant_response_allows_unknown_entrance(self):
        grant = GrantItem.model_validate(
            {
                "id": str(UUID(int=2)),
                "apartment_id": str(UUID(int=3)),
                "house_id": str(UUID(int=1)),
                "address_display": "Дом 1",
                "entrance_number": None,
                "apartment_number": 17,
                "valid_from": "2026-09-30T00:00:00Z",
                "status": "active",
            }
        )
        self.assertIsNone(grant.entrance_number)

    def test_unknown_entrance_does_not_expose_entrance_issue(self):
        apartment_id = UUID(int=3)
        common = dict(
            has_house_access=True,
            is_author=False,
            scope_all_house=False,
            granted_apartment_ids={apartment_id},
            granted_entrance_numbers=set(),
        )
        self.assertFalse(
            can_view_issue(
                target_apartment_ids=set(), target_entrance_numbers={1}, **common
            )
        )
        self.assertTrue(
            can_view_issue(
                target_apartment_ids={apartment_id}, target_entrance_numbers=set(), **common
            )
        )

    def test_migration_refuses_existing_conflicts_before_changing_constraints(self):
        migration = import_module(
            "src.db.migrations.versions.20260930_006_apartment_numbers"
        )
        apartment_conflict = SimpleNamespace(house_id=UUID(int=1), apartment_number=17)
        request_conflict = SimpleNamespace(
            applicant_user_id=UUID(int=2),
            house_id=UUID(int=1),
            submitted_apartment_number=17,
        )
        for first_result, second_result in (
            (apartment_conflict, None),
            (None, request_conflict),
        ):
            with self.subTest(conflict=first_result or second_result):
                bind = MagicMock()
                bind.execute.side_effect = [
                    SimpleNamespace(first=lambda: first_result),
                    SimpleNamespace(first=lambda: second_result),
                ]
                with (
                    patch.object(migration.op, "get_bind", return_value=bind),
                    patch.object(migration.op, "execute"),
                    patch.object(migration.op, "drop_constraint") as drop_constraint,
                ):
                    with self.assertRaisesRegex(RuntimeError, "multiple"):
                        migration.upgrade()
                    drop_constraint.assert_not_called()

    def test_migration_uses_existing_check_constraint_names(self):
        migration = import_module(
            "src.db.migrations.versions.20260930_006_apartment_numbers"
        )
        for direction in ("upgrade", "downgrade"):
            with self.subTest(direction=direction):
                output = StringIO()
                context = MigrationContext.configure(
                    dialect_name="postgresql",
                    opts={
                        "as_sql": True,
                        "output_buffer": output,
                        "target_metadata": Base.metadata,
                    },
                )
                operations = Operations(context)
                bind = MagicMock()
                bind.execute.return_value.first.return_value = None
                bind.execute.return_value.scalar_one_or_none.return_value = None
                with (
                    patch.object(migration, "op", operations),
                    patch.object(operations, "get_bind", return_value=bind),
                ):
                    getattr(migration, direction)()
                sql = output.getvalue()
                for schema, table, name in (
                    ("housing", "apartments", "ck_apartments_entrance_number_positive"),
                    ("access", "resident_requests", "ck_resident_requests_entrance_number_positive"),
                ):
                    self.assertIn(
                        f"ALTER TABLE {schema}.{table} DROP CONSTRAINT {name};", sql
                    )
                    self.assertIn(
                        f"ALTER TABLE {schema}.{table} ADD CONSTRAINT {name} CHECK", sql
                    )
                self.assertNotIn("ck_apartments_ck_apartments", sql)
                self.assertNotIn("ck_resident_requests_ck_resident_requests", sql)


class ApartmentLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_staff_access_approval_can_fill_unknown_entrance(self):
        house_id = UUID(int=1)
        apartment = Apartment(
            id=UUID(int=2), house_id=house_id, apartment_number=17, entrance_number=None
        )
        session = SimpleNamespace(scalar=AsyncMock(side_effect=[None, apartment]))
        found = await access_apartment(
            session, SimpleNamespace(id=house_id), 17, 2
        )
        self.assertIs(found, apartment)
        self.assertEqual(apartment.entrance_number, 2)

    async def test_conflicting_known_entrance_is_not_overwritten(self):
        house_id = UUID(int=1)
        apartment = Apartment(
            id=UUID(int=2), house_id=house_id, apartment_number=17, entrance_number=1
        )
        session = SimpleNamespace(scalar=AsyncMock(side_effect=[None, apartment]))
        with self.assertRaises(AccessRuleError) as error:
            await access_apartment(session, SimpleNamespace(id=house_id), 17, 2)
        self.assertEqual(error.exception.code, "apartment_entrance_conflict")
        self.assertEqual(apartment.entrance_number, 1)

    async def test_issue_creation_never_assigns_an_entrance_to_an_apartment(self):
        apartment_id = UUID(int=3)
        session = SimpleNamespace(scalar=AsyncMock(return_value=apartment_id))
        found_id = await issue_apartment(
            session, SimpleNamespace(id=UUID(int=1), entrance_count=5), 2, 17
        )
        self.assertEqual(found_id, apartment_id)
        statement = session.scalar.call_args.args[0]
        parameters = statement.compile(dialect=postgresql.dialect()).params
        self.assertIsNone(parameters["entrance_number"])

    async def test_unknown_entrance_is_excluded_from_resident_scope(self):
        apartment_id = UUID(int=3)
        session = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    all=lambda: [SimpleNamespace(id=apartment_id, entrance_number=None)]
                )
            )
        )
        apartments, entrances = await _resident_locations(
            session,
            SimpleNamespace(id=UUID(int=4), kind="resident"),
            SimpleNamespace(id=UUID(int=1)),
        )
        self.assertEqual(apartments, {apartment_id})
        self.assertEqual(entrances, set())
