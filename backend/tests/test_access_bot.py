"""Команды текстового доступа используют общие сценарии."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers.access_text import handle_access_text


class AccessBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = SimpleNamespace(rollback=AsyncMock())
        self.user = SimpleNamespace(id=uuid4())

    async def test_other_command_is_left_for_another_domain(self) -> None:
        result = await handle_access_text(self.session, self.user, "/issue help")
        self.assertIsNone(result)

    async def test_help_has_all_access_sections(self) -> None:
        result = await handle_access_text(self.session, self.user, "/access help")
        self.assertIn("profile", result)
        self.assertIn("resident", result)
        self.assertIn("company", result)
        self.assertIn("staff", result)
        self.assertIn("requests", result)

    async def test_house_search_calls_shared_query(self) -> None:
        house_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.queries.search_houses",
            new_callable=AsyncMock,
        ) as search:
            search.return_value = [
                {"id": house_id, "address_display": "Владивосток, дом 1"}
            ]
            result = await handle_access_text(
                self.session, self.user, "/access houses Владивосток дом 1"
            )
        search.assert_awaited_once_with(self.session, "Владивосток дом 1")
        self.assertIn(str(house_id), result)

    async def test_company_house_request_requires_both_counts(self) -> None:
        company_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.company.create_house_request",
            new_callable=AsyncMock,
        ) as create:
            create.return_value = SimpleNamespace(id=uuid4())
            await handle_access_text(
                self.session,
                self.user,
                f"/access company house company:{company_id} 3 70 Пушкина 5",
            )
        create.assert_awaited_once_with(
            self.session,
            self.user,
            registration_request_id=None,
            company_id=company_id,
            entered_address="Пушкина 5",
            entrance_count=3,
            apartment_count=70,
            free_text=None,
        )

    async def test_support_can_override_both_house_counts(self) -> None:
        request_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.company.decide_house_request",
            new_callable=AsyncMock,
        ) as decide:
            decide.return_value = (SimpleNamespace(id=request_id), SimpleNamespace(id=uuid4()))
            await handle_access_text(
                self.session,
                self.user,
                f"/access company house_decide {request_id} approve Проверено entrances=4 apartments=80",
            )
        decide.assert_awaited_once_with(
            self.session,
            self.user,
            request_id=request_id,
            outcome="approved",
            decision_note="Проверено",
            proposed_address_key=None,
            entrance_count=4,
            apartment_count=80,
        )

    async def test_support_can_correct_connected_house_by_id(self) -> None:
        house_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.company.update_house_details",
            new_callable=AsyncMock,
        ) as update:
            update.return_value = SimpleNamespace(
                id=house_id, entrance_count=4, apartment_count=80
            )
            result = await handle_access_text(
                self.session,
                self.user,
                f"/access company house_details {house_id} 4 80",
            )
        update.assert_awaited_once_with(
            self.session,
            self.user,
            house_id=house_id,
            entrance_count=4,
            apartment_count=80,
        )
        self.assertIn("квартир 80", result)

    async def test_apply_preserves_full_name(self) -> None:
        house_id = uuid4()
        request_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.resident.create_resident_request",
            new_callable=AsyncMock,
        ) as create:
            create.return_value = SimpleNamespace(id=request_id)
            result = await handle_access_text(
                self.session,
                self.user,
                f"/access resident apply {house_id} 2 15 Иван Иванов",
            )
        create.assert_awaited_once_with(
            self.session,
            self.user,
            house_id=house_id,
            entrance_number=2,
            apartment_number=15,
            full_name="Иван Иванов",
        )
        self.assertIn(str(request_id), result)

    async def test_apply_needs_only_apartment_number(self) -> None:
        house_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.resident.create_resident_request",
            new_callable=AsyncMock,
        ) as create:
            create.return_value = SimpleNamespace(id=uuid4())
            await handle_access_text(
                self.session,
                self.user,
                f"/access resident apply {house_id} 15 Иван Иванов",
            )
        create.assert_awaited_once_with(
            self.session,
            self.user,
            house_id=house_id,
            apartment_number=15,
            full_name="Иван Иванов",
        )

    async def test_next_application_omits_full_name(self) -> None:
        house_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.resident.create_resident_request",
            new_callable=AsyncMock,
        ) as create:
            create.return_value = SimpleNamespace(id=uuid4())
            await handle_access_text(
                self.session, self.user, f"/access resident apply {house_id} 15"
            )
        self.assertIsNone(create.await_args.kwargs["full_name"])

    async def test_profile_name_command_uses_explicit_profile_scenario(self) -> None:
        with patch(
            "src.bot.handlers.access_text.update_profile_name", new_callable=AsyncMock
        ) as update:
            update.return_value = SimpleNamespace(full_name="Иван Иванов")
            reply = await handle_access_text(
                self.session, self.user, "/access profile name Иван Иванов"
            )
        update.assert_awaited_once_with(
            self.session, self.user, full_name="Иван Иванов"
        )
        self.assertIn("Иван Иванов", reply)

    async def test_offer_needs_only_apartment_number(self) -> None:
        house_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.resident.create_resident_offer",
            new_callable=AsyncMock,
        ) as create:
            create.return_value = SimpleNamespace(id=uuid4())
            await handle_access_text(
                self.session,
                self.user,
                f"/access resident offer {house_id} 15 79990000000",
            )
        create.assert_awaited_once_with(
            self.session,
            self.user,
            house_id=house_id,
            apartment_number=15,
            phone_number="79990000000",
            valid_to=None,
        )

    async def test_invalid_id_gives_help_without_db_write(self) -> None:
        result = await handle_access_text(
            self.session, self.user, "/access requests show resident not-a-uuid"
        )
        self.assertIn("UUID", result)
        self.session.rollback.assert_awaited_once()

    async def test_show_command_paginates_full_discussion(self) -> None:
        request_id = uuid4()
        row = {
            "id": str(request_id),
            "kind": "resident",
            "status": "reviewing",
            "applicant_user_id": str(self.user.id),
            "house_id": str(uuid4()),
            "address_display": "Пушкина, 5",
            "discussion": [
                {"author_user_id": str(self.user.id), "text": f"ТЕКСТ-{number} " + "а" * 560}
                for number in range(14)
            ],
        }
        with patch(
            "src.bot.handlers.access_text.queries.get_request", new_callable=AsyncMock
        ) as get:
            get.return_value = row
            pages = []
            page = 1
            while True:
                reply = await handle_access_text(
                    self.session,
                    self.user,
                    f"/access requests show resident {request_id} {page}",
                )
                self.assertLess(len(reply), 3900)
                pages.append(reply)
                if f"/access requests show resident {request_id} {page + 1}" not in reply:
                    break
                page += 1
        self.assertGreater(len(pages), 1)
        self.assertEqual(get.await_count, len(pages))
        full_text = "\n".join(pages)
        for number in range(14):
            self.assertIn(f"ТЕКСТ-{number} ", full_text)

    async def test_show_rejects_zero_page(self) -> None:
        request_id = uuid4()
        with patch(
            "src.bot.handlers.access_text.queries.get_request", new_callable=AsyncMock
        ) as get:
            result = await handle_access_text(
                self.session,
                self.user,
                f"/access requests show resident {request_id} 0",
            )
        self.assertIn("Номер страницы", result)
        get.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
