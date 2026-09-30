"""Button and dialog paths for access scenarios in the MAX bot."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers.access_ui import (
    _request_detail,
    _save_apply_draft,
    handle_action,
    handle_text,
)


class AccessUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = SimpleNamespace()
        self.actor = SimpleNamespace(
            id=uuid4(), kind="resident", full_name=None,
            full_name_is_manual=False, full_name_confirmed_at=None,
        )

    async def test_profile_button_and_confirmation_use_one_account_name(self) -> None:
        with patch("src.bot.handlers.access_ui.set_dialog", new_callable=AsyncMock) as start:
            reply = await handle_action(self.session, self.actor, "a:profile")
        self.assertIn("Общее ФИО", reply.text)
        self.assertEqual(start.await_args.kwargs["flow_kind"], "access_profile")

        dialog = SimpleNamespace(flow_kind="access_profile", step="name", data={})
        with patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update:
            reply = await handle_text(self.session, self.actor, dialog, "  Иван  Иванов  ")
        self.assertEqual(update.await_args.kwargs["data"]["full_name"], "Иван Иванов")
        self.assertEqual(reply.buttons[0][0].payload, "a:profile_save")

        dialog = SimpleNamespace(flow_kind="access_profile", step="review", data={"full_name": "Иван Иванов"})
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock, return_value=dialog),
            patch("src.bot.handlers.access_ui.update_profile_name", new_callable=AsyncMock) as save,
            patch("src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock),
        ):
            save.return_value = SimpleNamespace(full_name="Иван Иванов")
            reply = await handle_action(self.session, self.actor, "a:profile_save")
        save.assert_awaited_once_with(self.session, self.actor, full_name="Иван Иванов")
        self.assertIn("подтверждено", reply.text)

    async def test_confirmed_name_skips_name_step_in_new_access_request(self) -> None:
        self.actor.full_name = "Иван Иванов"
        self.actor.full_name_is_manual = True
        self.actor.full_name_confirmed_at = object()
        dialog = SimpleNamespace(
            flow_kind="access_apply", step="confirm", draft_id=None,
            data={"house_id": str(uuid4()), "address": "Пушкина, 5", "entrance_number": 2, "apartment_number": 12},
        )
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock, return_value=dialog),
            patch("src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
        ):
            reply = await handle_action(self.session, self.actor, "a:confirm_house")
        self.assertEqual(update.await_args.kwargs["step"], "review")
        self.assertIn("Иван Иванов", reply.text)

    async def test_old_access_draft_without_entrance_requires_it_before_review(self) -> None:
        self.actor.full_name = "Иван Иванов"
        self.actor.full_name_is_manual = True
        self.actor.full_name_confirmed_at = object()
        dialog = SimpleNamespace(
            flow_kind="access_apply", step="confirm", draft_id=uuid4(),
            data={"house_id": str(uuid4()), "address": "Пушкина, 5", "apartment_number": 12},
        )
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock, return_value=dialog),
            patch("src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
        ):
            reply = await handle_action(self.session, self.actor, "a:confirm_house")
        self.assertEqual(update.await_args.kwargs["step"], "entrance")
        self.assertIn("номер подъезда", reply.text)

    async def test_unconfirmed_name_is_not_implicitly_reused_from_profile_draft(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_apply", step="review", draft_id=None,
            data={
                "house_id": str(uuid4()), "full_name": "Имя из MAX",
                "name_from_profile": True, "apartment_number": 12,
            },
        )
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock, return_value=dialog),
            patch("src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
            patch("src.bot.handlers.access_ui.resident.create_resident_request", new_callable=AsyncMock) as create,
        ):
            reply = await handle_action(self.session, self.actor, "a:submit_apply")
        self.assertEqual(update.await_args.kwargs["step"], "name")
        self.assertIn("Введите ваше ФИО заново", reply.text)
        create.assert_not_awaited()

    async def test_other_callbacks_and_dialogs_are_left_to_other_handlers(self) -> None:
        self.assertIsNone(await handle_action(self.session, self.actor, "i:list"))
        dialog = SimpleNamespace(flow_kind="issue_create", step="description")
        self.assertIsNone(await handle_text(self.session, self.actor, dialog, "лифт"))

    async def test_admin_can_open_saved_resident_buttons_without_staff_assignment(self) -> None:
        self.actor.kind = "admin"
        company_id = uuid4()
        with (
            patch("src.bot.handlers.access_ui.require_staff", new_callable=AsyncMock) as require,
            patch("src.bot.handlers.access_ui.queries.list_residents", new_callable=AsyncMock) as residents,
            patch("src.bot.handlers.access_ui.queries.list_company_offers", new_callable=AsyncMock) as offers,
        ):
            require.return_value = None
            residents.return_value = []
            offers.return_value = []
            people = await handle_action(self.session, self.actor, f"a:people:{company_id}")
            pending = await handle_action(self.session, self.actor, f"a:company_offers:{company_id}")

        for reply in (people, pending):
            self.assertIsNotNone(reply)
            self.assertIn("Предложить доступ", [button.text for row in reply.buttons for button in row])

    async def test_house_search_returns_address_buttons_without_uuid_in_text(
        self,
    ) -> None:
        house_id = uuid4()
        dialog = SimpleNamespace(flow_kind="access_apply", step="search", data={})
        with patch(
            "src.bot.handlers.access_ui.queries.search_houses", new_callable=AsyncMock
        ) as search:
            search.return_value = [
                {"id": house_id, "address_display": "Владивосток, Пушкина, 5"}
            ]
            reply = await handle_text(self.session, self.actor, dialog, "Пушкина")
        search.assert_awaited_once_with(self.session, "Пушкина")
        self.assertIn("Выберите", reply.text)
        self.assertNotIn(str(house_id), reply.text)
        self.assertEqual(reply.buttons[0][0].payload, f"a:house:{house_id}")

    async def test_resuming_shared_draft_requires_address_confirmation_again(
        self,
    ) -> None:
        draft_id, house_id = uuid4(), uuid4()
        draft = SimpleNamespace(
            id=draft_id,
            flow_kind="resident_request",
            submitted_at=None,
            revision=3,
            payload={"house_id": str(house_id), "full_name": "Иван Иванов"},
        )
        with (
            patch(
                "src.bot.handlers.access_ui.get_draft", new_callable=AsyncMock
            ) as get,
            patch(
                "src.bot.handlers.access_ui.set_dialog", new_callable=AsyncMock
            ) as set_state,
        ):
            get.return_value = draft
            reply = await handle_action(
                self.session, self.actor, f"a:resume:{draft_id}"
            )
        self.assertIn("подтвердите адрес", reply.text)
        self.assertEqual(set_state.await_args.kwargs["step"], "search")
        self.assertEqual(set_state.await_args.kwargs["draft_id"], draft_id)

    async def test_complete_resumed_draft_keeps_fields_after_address_confirmation(
        self,
    ) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_apply",
            step="confirm",
            draft_id=uuid4(),
            data={
                "house_id": str(uuid4()),
                "address": "Пушкина, 5",
                "full_name": "Иван Иванов",
                "entrance_number": 2,
                "apartment_number": 24,
                "draft_revision": 3,
            },
        )
        with (
            patch(
                "src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock
            ) as get,
            patch(
                "src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock
            ),
            patch(
                "src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock
            ) as update,
        ):
            get.return_value = dialog
            reply = await handle_action(self.session, self.actor, "a:confirm_house")
        self.assertEqual(update.await_args.kwargs["step"], "review")
        self.assertIn("Пушкина, 5", reply.text)
        self.assertIn("Иван Иванов", reply.text)
        self.assertIn("подъезд 2", reply.text)
        self.assertEqual(reply.buttons[0][0].payload, "a:submit_apply")

    async def test_resident_draft_is_created_with_partial_data(self) -> None:
        dialog = SimpleNamespace(draft_id=None, data={})
        draft_id = uuid4()
        with (
            patch(
                "src.bot.handlers.access_ui.save_draft", new_callable=AsyncMock
            ) as save,
            patch(
                "src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock
            ) as update,
        ):
            save.return_value = SimpleNamespace(id=draft_id, revision=1)
            data = {"house_id": str(uuid4()), "address": "Пушкина, 5", "entrance_number": 2}
            await _save_apply_draft(self.session, self.actor, dialog, data)
        self.assertEqual(dialog.draft_id, draft_id)
        self.assertEqual(data["draft_revision"], 1)
        self.assertEqual(
            save.await_args.kwargs["payload"], {"house_id": data["house_id"], "entrance_number": 2}
        )
        update.assert_awaited_once()

    async def test_apply_name_step_asks_for_entrance(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_apply", step="name", data={"house_id": str(uuid4())}
        )
        with (
            patch("src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
        ):
            reply = await handle_text(self.session, self.actor, dialog, "Иван Иванов")
        self.assertEqual(update.await_args.kwargs["step"], "entrance")
        self.assertIn("номер подъезда", reply.text)

    async def test_apply_entrance_step_persists_to_shared_draft(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_apply", step="entrance", data={"house_id": str(uuid4())}
        )
        with (
            patch("src.bot.handlers.access_ui._save_apply_draft", new_callable=AsyncMock) as save,
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
        ):
            reply = await handle_text(self.session, self.actor, dialog, "2")
        self.assertEqual(save.await_args.args[3]["entrance_number"], 2)
        self.assertEqual(update.await_args.kwargs["step"], "apartment")
        save.assert_awaited_once()
        self.assertIn("номер квартиры", reply.text)

    async def test_offer_phone_step_asks_for_entrance(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_offer_new", step="phone", data={"house_id": str(uuid4())}
        )
        with patch(
            "src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock
        ) as update:
            reply = await handle_text(self.session, self.actor, dialog, "+79990000000")
        self.assertEqual(update.await_args.kwargs["step"], "entrance")
        self.assertIn("номер подъезда", reply.text.lower())

    async def test_offer_entrance_step_precedes_apartment(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_offer_new", step="entrance", data={"house_id": str(uuid4()), "phone": "79990000000"}
        )
        with patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update:
            reply = await handle_text(self.session, self.actor, dialog, "3")
        self.assertEqual(update.await_args.kwargs["step"], "apartment")
        self.assertEqual(update.await_args.kwargs["data"]["entrance_number"], 3)
        self.assertIn("квартиры", reply.text)

    async def test_resident_draft_update_uses_current_revision(self) -> None:
        draft_id = uuid4()
        dialog = SimpleNamespace(draft_id=draft_id, data={})
        with (
            patch(
                "src.bot.handlers.access_ui.save_draft", new_callable=AsyncMock
            ) as save,
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock),
        ):
            save.return_value = SimpleNamespace(id=draft_id, revision=5)
            data = {"draft_revision": 4, "full_name": "Иван Иванов"}
            await _save_apply_draft(self.session, self.actor, dialog, data)
        self.assertEqual(save.await_args.kwargs["revision"], 4)
        self.assertEqual(data["draft_revision"], 5)

    async def test_submit_resident_request_marks_shared_draft_and_calls_domain(
        self,
    ) -> None:
        draft_id, house_id, request_id = uuid4(), uuid4(), uuid4()
        dialog = SimpleNamespace(
            flow_kind="access_apply",
            step="review",
            draft_id=draft_id,
            data={
                "house_id": str(house_id),
                "full_name": "Иван Иванов",
                "entrance_number": 2,
                "apartment_number": 24,
                "draft_revision": 3,
            },
        )
        with (
            patch(
                "src.bot.handlers.access_ui.get_dialog", new_callable=AsyncMock
            ) as get,
            patch(
                "src.bot.handlers.access_ui.mark_submitted", new_callable=AsyncMock
            ) as mark,
            patch(
                "src.bot.handlers.access_ui.resident.create_resident_request",
                new_callable=AsyncMock,
            ) as create,
            patch(
                "src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock
            ) as clear,
        ):
            get.return_value = dialog
            create.return_value = SimpleNamespace(id=request_id)
            reply = await handle_action(self.session, self.actor, "a:submit_apply")
        mark.assert_awaited_once_with(
            self.session, self.actor, draft_id=draft_id, revision=3
        )
        create.assert_awaited_once_with(
            self.session,
            self.actor,
            house_id=house_id,
            full_name="Иван Иванов",
            entrance_number=2,
            apartment_number=24,
        )
        clear.assert_awaited_once_with(self.session, self.actor.id)
        self.assertEqual(
            reply.buttons[0][0].payload, f"a:request:resident:{request_id}"
        )

    async def test_request_detail_gives_applicant_discussion_and_edit(self) -> None:
        request_id = uuid4()
        house_id = uuid4()
        row = {
            "id": str(request_id),
            "kind": "resident",
            "status": "open",
            "applicant_user_id": str(self.actor.id),
            "house_id": str(house_id),
            "address_display": "Пушкина, 5",
            "submitted_full_name": "Иван Иванов",
            "submitted_entrance_number": 2,
            "submitted_apartment_number": 24,
            "outcome": None,
            "decision_note": None,
            "discussion": [],
            "cancel_requested_by": None,
        }
        with patch(
            "src.bot.handlers.access_ui.queries.get_request", new_callable=AsyncMock
        ) as get:
            get.return_value = row
            reply = await _request_detail(
                self.session, self.actor, "resident", request_id
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"a:req_edit:{request_id}", payloads)
        self.assertIn(f"a:req_message:resident:{request_id}", payloads)
        self.assertIn(f"f:access:{request_id}", payloads)
        self.assertIn(f"a:req_file:{request_id}", payloads)
        self.assertNotIn(f"a:decision:resident:{request_id}:granted", payloads)

    async def test_add_resident_file_button_checks_write_access(self) -> None:
        request_id = uuid4()
        with (
            patch("src.bot.handlers.access_ui.require_file_parent", new_callable=AsyncMock) as parent,
            patch("src.bot.handlers.access_ui.set_dialog", new_callable=AsyncMock) as dialog,
        ):
            reply = await handle_action(
                self.session, self.actor, f"a:req_file:{request_id}"
            )
        parent.assert_awaited_once_with(
            self.session, self.actor, "resident", request_id, writing=True
        )
        self.assertEqual(dialog.await_args.kwargs["flow_kind"], "access_file")
        self.assertIn("без подписи", reply.text)

    async def test_read_only_employee_does_not_get_resident_mutations(self) -> None:
        request_id, house_id, company_id = uuid4(), uuid4(), uuid4()
        self.actor.kind = "employee"
        row = {
            "id": str(request_id),
            "kind": "resident",
            "status": "open",
            "applicant_user_id": str(uuid4()),
            "house_id": str(house_id),
            "address_display": "Пушкина, 5",
            "submitted_full_name": "Иван Иванов",
            "submitted_entrance_number": 2,
            "submitted_apartment_number": 24,
            "outcome": None,
            "decision_note": None,
            "discussion": [],
            "cancel_requested_by": None,
        }
        self.session.get = AsyncMock(
            return_value=SimpleNamespace(company_id=company_id)
        )
        with (
            patch(
                "src.bot.handlers.access_ui.queries.get_request", new_callable=AsyncMock
            ) as get,
            patch(
                "src.bot.handlers.access_ui.require_staff", new_callable=AsyncMock
            ) as require,
        ):
            get.return_value = row
            require.return_value = SimpleNamespace(can_manage_residents=False)
            reply = await _request_detail(
                self.session, self.actor, "resident", request_id
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertNotIn(f"a:decision:resident:{request_id}:granted", payloads)
        self.assertNotIn(f"a:req_message:resident:{request_id}", payloads)
        self.assertNotIn(f"a:req_file:{request_id}", payloads)

    async def test_discussion_buttons_reach_all_messages_and_recheck_access(self) -> None:
        request_id = uuid4()
        messages = [
            {"author_user_id": str(self.actor.id), "text": f"MARKER-{number} " + "т" * 620}
            for number in range(13)
        ]
        row = {
            "id": str(request_id),
            "kind": "company_registration",
            "status": "reviewing",
            "applicant_user_id": str(self.actor.id),
            "phone_number": "+79990000000",
            "proposed_company_name": "Тестовая УК",
            "free_text": "Заявка",
            "outcome": None,
            "decision_note": None,
            "cancel_requested_by": None,
            "discussion": messages,
        }
        with patch(
            "src.bot.handlers.access_ui.queries.get_request", new_callable=AsyncMock
        ) as get:
            get.return_value = row
            detail = await _request_detail(self.session, self.actor, "company_registration", request_id)
            self.assertIn(
                f"a:req_discussion:company_registration:{request_id}:1",
                [button.payload for buttons in detail.buttons for button in buttons],
            )
            pages = []
            page = 1
            while True:
                reply = await handle_action(
                    self.session,
                    self.actor,
                    f"a:req_discussion:company_registration:{request_id}:{page}",
                )
                self.assertLess(len(reply.text), 3900)
                pages.append(reply.text)
                next_payload = f"a:req_discussion:company_registration:{request_id}:{page + 1}"
                if next_payload not in [button.payload for buttons in reply.buttons for button in buttons]:
                    break
                page += 1
        self.assertGreater(len(pages), 1)
        self.assertEqual(get.await_count, len(pages) + 1)
        full_text = "\n".join(pages)
        for number in range(13):
            self.assertIn(f"MARKER-{number} ", full_text)

    async def test_discussion_splits_one_long_message_without_losing_ending(self) -> None:
        request_id = uuid4()
        row = {
            "id": str(request_id),
            "kind": "house_addition",
            "applicant_user_id": str(self.actor.id),
            "discussion": [
                {"author_user_id": str(self.actor.id), "text": "А" * 7000 + "КОНЕЦ"}
            ],
        }
        with patch(
            "src.bot.handlers.access_ui.queries.get_request", new_callable=AsyncMock
        ) as get:
            get.return_value = row
            pages = [
                await handle_action(
                    self.session,
                    self.actor,
                    f"a:req_discussion:house_addition:{request_id}:{page}",
                )
                for page in (1, 2, 3)
            ]
        self.assertTrue(all(len(reply.text) < 3900 for reply in pages))
        self.assertIn("КОНЕЦ", pages[-1].text)

    async def test_company_wizard_asks_for_phone_after_name(self) -> None:
        dialog = SimpleNamespace(flow_kind="access_company", step="name", data={})
        with patch(
            "src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock
        ) as update:
            reply = await handle_text(self.session, self.actor, dialog, "УК Пример")
        self.assertIn("номер", reply.text.lower())
        self.assertEqual(update.await_args.kwargs["step"], "phone")
        self.assertEqual(update.await_args.kwargs["data"]["name"], "УК Пример")

    async def test_house_wizard_asks_for_both_counts_before_note(self) -> None:
        dialog = SimpleNamespace(flow_kind="access_house", step="address", data={})

        async def update(current, *, step=None, data=None):
            current.step = step or current.step
            current.data = data if data is not None else current.data

        with patch("src.bot.handlers.access_ui.update_dialog", side_effect=update):
            address = await handle_text(self.session, self.actor, dialog, "Пушкина 5")
            self.assertEqual(dialog.step, "entrance_count")
            self.assertIn("подъездов", address.text)
            entrances = await handle_text(self.session, self.actor, dialog, "3")
            self.assertEqual(dialog.step, "apartment_count")
            self.assertIn("квартир", entrances.text)
            apartments = await handle_text(self.session, self.actor, dialog, "70")
            self.assertEqual(dialog.step, "note")
            self.assertIn("пояснение", apartments.text)
            review = await handle_text(self.session, self.actor, dialog, "нет")
            self.assertEqual(dialog.step, "review")
            self.assertIn("подъездов 3, квартир 70", review.text)

    async def test_house_submit_passes_counts_to_domain(self) -> None:
        company_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="access_house",
            step="review",
            data={
                "source": "company",
                "source_id": str(company_id),
                "address": "Пушкина 5",
                "entrance_count": 3,
                "apartment_count": 70,
                "note": "",
            },
        )
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new=AsyncMock(return_value=dialog)),
            patch("src.bot.handlers.access_ui.company.create_house_request", new_callable=AsyncMock) as create,
            patch("src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock),
        ):
            create.return_value = SimpleNamespace(id=uuid4())
            await handle_action(self.session, self.actor, "a:house_submit")
        create.assert_awaited_once_with(
            self.session,
            self.actor,
            registration_request_id=None,
            company_id=company_id,
            entered_address="Пушкина 5",
            entrance_count=3,
            apartment_count=70,
            free_text=None,
        )

    async def test_old_house_review_asks_for_missing_counts_before_submit(self) -> None:
        dialog = SimpleNamespace(
            flow_kind="access_house",
            step="review",
            data={"source": "company", "source_id": str(uuid4()), "address": "Пушкина 5"},
        )
        with (
            patch("src.bot.handlers.access_ui.get_dialog", new=AsyncMock(return_value=dialog)),
            patch("src.bot.handlers.access_ui.update_dialog", new_callable=AsyncMock) as update,
            patch("src.bot.handlers.access_ui.company.create_house_request", new_callable=AsyncMock) as create,
        ):
            reply = await handle_action(self.session, self.actor, "a:house_submit")
        self.assertIn("количество подъездов", reply.text)
        self.assertEqual(update.await_args.kwargs["step"], "entrance_count")
        create.assert_not_awaited()

    async def test_support_decision_can_keep_counts_from_request(self) -> None:
        request_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="access_decision",
            step="note",
            data={
                "kind": "house_addition",
                "request_id": str(request_id),
                "outcome": "approved",
                "proposed_entrance_count": 3,
                "proposed_apartment_count": 70,
            },
        )

        async def update(current, *, step=None, data=None):
            current.step = step or current.step
            current.data = data if data is not None else current.data

        with (
            patch("src.bot.handlers.access_ui.get_dialog", new=AsyncMock(return_value=dialog)),
            patch("src.bot.handlers.access_ui.update_dialog", side_effect=update),
            patch("src.bot.handlers.access_ui.company.decide_house_request", new_callable=AsyncMock) as decide,
            patch("src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui._request_detail", new_callable=AsyncMock),
        ):
            await handle_text(self.session, self.actor, dialog, "Проверено")
            self.assertEqual(dialog.step, "entrance_count")
            await handle_action(self.session, self.actor, "a:decision_keep_entrances")
            self.assertEqual(dialog.step, "apartment_count")
            await handle_action(self.session, self.actor, "a:decision_keep_apartments")
            self.assertEqual(dialog.step, "review")
            await handle_action(self.session, self.actor, "a:decision_submit")
        decide.assert_awaited_once_with(
            self.session,
            self.actor,
            request_id=request_id,
            outcome="approved",
            decision_note="Проверено",
            proposed_address_key=None,
            entrance_count=None,
            apartment_count=None,
        )

    async def test_support_decision_can_correct_both_counts(self) -> None:
        request_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="access_decision",
            step="entrance_count",
            data={
                "kind": "house_addition",
                "request_id": str(request_id),
                "outcome": "approved",
                "note": "Исправлено",
                "proposed_entrance_count": 3,
                "proposed_apartment_count": 70,
            },
        )

        async def update(current, *, step=None, data=None):
            current.step = step or current.step
            current.data = data if data is not None else current.data

        with (
            patch("src.bot.handlers.access_ui.get_dialog", new=AsyncMock(return_value=dialog)),
            patch("src.bot.handlers.access_ui.update_dialog", side_effect=update),
            patch("src.bot.handlers.access_ui.company.decide_house_request", new_callable=AsyncMock) as decide,
            patch("src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock),
            patch("src.bot.handlers.access_ui._request_detail", new_callable=AsyncMock),
        ):
            await handle_text(self.session, self.actor, dialog, "4")
            self.assertEqual(dialog.step, "apartment_count")
            await handle_text(self.session, self.actor, dialog, "80")
            self.assertEqual(dialog.step, "review")
            await handle_action(self.session, self.actor, "a:decision_submit")
        decide.assert_awaited_once_with(
            self.session,
            self.actor,
            request_id=request_id,
            outcome="approved",
            decision_note="Исправлено",
            proposed_address_key=None,
            entrance_count=4,
            apartment_count=80,
        )

    async def test_support_can_correct_connected_house_from_button(self) -> None:
        self.actor.kind = "support"
        house_id = uuid4()
        house = SimpleNamespace(
            id=house_id,
            address_display="Пушкина 5",
            archived_at=None,
            entrance_count=3,
            apartment_count=70,
        )
        self.session.get = AsyncMock(return_value=house)
        dialog = SimpleNamespace(
            flow_kind="access_house_details",
            step="entrance_count",
            data={
                "house_id": str(house_id),
                "address": house.address_display,
                "old_entrance_count": 3,
                "old_apartment_count": 70,
            },
        )

        async def update(current, *, step=None, data=None):
            current.step = step or current.step
            current.data = data if data is not None else current.data

        with (
            patch("src.bot.handlers.access_ui.set_dialog", new_callable=AsyncMock) as start,
            patch("src.bot.handlers.access_ui.get_dialog", new=AsyncMock(return_value=dialog)),
            patch("src.bot.handlers.access_ui.update_dialog", side_effect=update),
            patch("src.bot.handlers.access_ui.company.update_house_details", new_callable=AsyncMock) as save,
            patch("src.bot.handlers.access_ui.clear_dialog", new_callable=AsyncMock),
        ):
            reply = await handle_action(
                self.session, self.actor, f"a:house_details:{house_id}"
            )
            self.assertIn("сейчас подъездов 3, квартир 70", reply.text)
            self.assertEqual(start.await_args.kwargs["step"], "entrance_count")
            await handle_text(self.session, self.actor, dialog, "4")
            self.assertEqual(dialog.step, "apartment_count")
            review = await handle_text(self.session, self.actor, dialog, "80")
            self.assertIn("подъездов 4, квартир 80", review.text)
            self.assertEqual(dialog.step, "review")
            save.return_value = SimpleNamespace(
                id=house_id,
                address_display="Пушкина 5",
                entrance_count=4,
                apartment_count=80,
            )
            final = await handle_action(
                self.session, self.actor, "a:house_details_submit"
            )
        save.assert_awaited_once_with(
            self.session,
            self.actor,
            house_id=house_id,
            entrance_count=4,
            apartment_count=80,
        )
        self.assertIn("квартир 80", final.text)

    async def test_offer_response_uses_domain_service(self) -> None:
        offer_id = uuid4()
        with patch(
            "src.bot.handlers.access_ui.resident.respond_resident_offer",
            new_callable=AsyncMock,
        ) as respond:
            respond.return_value = (
                SimpleNamespace(status="accepted"),
                SimpleNamespace(id=uuid4()),
            )
            reply = await handle_action(
                self.session, self.actor, f"a:offer_answer:{offer_id}:yes"
            )
        respond.assert_awaited_once_with(
            self.session, self.actor, offer_id=offer_id, accept=True
        )
        self.assertIn("доступ", reply.text)

    async def test_resident_request_notification_toggle_uses_shared_service(
        self,
    ) -> None:
        request_id = uuid4()
        with (
            patch(
                "src.bot.handlers.access_ui.set_mute", new_callable=AsyncMock
            ) as mute,
            patch(
                "src.bot.handlers.access_ui.is_muted", new_callable=AsyncMock
            ) as status,
            patch(
                "src.bot.handlers.access_ui.queries.get_request", new_callable=AsyncMock
            ),
        ):
            status.return_value = True
            reply = await handle_action(
                self.session, self.actor, f"a:req_mute:{request_id}:on"
            )
        mute.assert_awaited_once_with(
            self.session,
            self.actor,
            subject_kind="resident_request",
            subject_id=request_id,
            is_muted=True,
        )
        self.assertIn("отключены", reply.text)


if __name__ == "__main__":
    unittest.main()
