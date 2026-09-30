"""Issue buttons and persisted dialog steps use shared domain operations."""

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers import issues_ui
from src.bot.ui import UiReply
from src.domain.issues.service import IssueError
from src.domain.issues.staff_suggest import StaffMergeSuggestion


class IssueUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="resident")
        self.session = SimpleNamespace(
            rollback=AsyncMock(),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        )

    async def test_other_callback_namespace_is_ignored(self) -> None:
        self.assertIsNone(
            await issues_ui.handle_action(self.session, self.actor, "a:houses")
        )

    async def test_unknown_entrance_gives_only_apartment_button(self) -> None:
        apartment_id = uuid4()
        self.session.get = AsyncMock(return_value=SimpleNamespace(archived_at=None))
        with patch.object(
            issues_ui, "_active_resident_apartments",
            new=AsyncMock(return_value={apartment_id: SimpleNamespace(id=apartment_id, entrance_number=None, apartment_number=14)}),
        ):
            reply = await issues_ui._scope_options(
                self.session, self.actor, SimpleNamespace(data={"house_id": str(uuid4())}),
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn("i:scope:apartment", payloads)
        self.assertNotIn("i:scope:entrance", payloads)

    async def test_multiple_grants_require_own_location_choice(self) -> None:
        first_id, second_id = uuid4(), uuid4()
        dialog = SimpleNamespace(
            flow_kind="issue_new", step="scope", data={"house_id": str(uuid4())},
        )
        self.session.get = AsyncMock(return_value=SimpleNamespace(archived_at=None))
        apartments = {
            first_id: SimpleNamespace(id=first_id, entrance_number=2, apartment_number=14),
            second_id: SimpleNamespace(id=second_id, entrance_number=3, apartment_number=41),
        }
        with (
            patch.object(issues_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(issues_ui, "_active_resident_apartments", new=AsyncMock(return_value=apartments)),
            patch.object(issues_ui, "update_dialog", new=AsyncMock()) as update,
        ):
            reply = await issues_ui.handle_action(self.session, self.actor, "i:scope:apartment")
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"i:loc:{first_id}", payloads)
        self.assertIn(f"i:loc:{second_id}", payloads)
        update.assert_awaited_once()
        self.assertEqual(update.await_args.kwargs["step"], "scope_location")

    async def test_forged_location_button_does_not_create_draft(self) -> None:
        own_id, foreign_id = uuid4(), uuid4()
        dialog = SimpleNamespace(
            flow_kind="issue_new", step="scope_location",
            data={"house_id": str(uuid4()), "category_id": str(uuid4()),
                  "description": "Нет света", "selected_scope": "apartment"},
        )
        self.session.get = AsyncMock(return_value=SimpleNamespace(archived_at=None))
        with (
            patch.object(issues_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(issues_ui, "_active_resident_apartments", new=AsyncMock(return_value={
                own_id: SimpleNamespace(id=own_id, entrance_number=2, apartment_number=14),
            })),
            patch.object(issues_ui, "save_draft", new=AsyncMock()) as save,
        ):
            reply = await issues_ui.handle_action(self.session, self.actor, f"i:loc:{foreign_id}")
        self.assertIn("подтверждённую квартиру", reply.text)
        save.assert_not_awaited()

    async def test_old_number_callback_cannot_select_any_apartment(self) -> None:
        dialog = SimpleNamespace(flow_kind="issue_new", step="scope", data={"house_id": str(uuid4())})
        with (
            patch.object(issues_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(issues_ui, "save_draft", new=AsyncMock()) as save,
        ):
            reply = await issues_ui.handle_action(self.session, self.actor, "i:scope:apartment:99")
        self.assertIn("неактуален", reply.text)
        save.assert_not_awaited()

    async def test_legacy_draft_requires_new_scope_selection(self) -> None:
        house_id, draft_id = uuid4(), uuid4()
        draft = SimpleNamespace(
            id=draft_id, flow_kind="issue_card", submitted_at=None, revision=3,
            payload={"house_id": str(house_id), "category_id": str(uuid4()),
                     "description": "Нет света", "title": "Нет света",
                     "scope_all_house": False, "target_apartments": [{"apartment_number": 99}]},
        )
        dialog = SimpleNamespace(flow_kind="issue_new", step="scope", data={}, draft_id=draft_id)
        with (
            patch.object(issues_ui, "get_draft", new=AsyncMock(return_value=draft)),
            patch.object(issues_ui, "list_visible_cards", new=AsyncMock(return_value=[])),
            patch.object(issues_ui, "set_dialog", new=AsyncMock(return_value=dialog)) as set_state,
            patch.object(issues_ui, "_scope_options", new=AsyncMock(return_value=UiReply("Выберите область"))) as options,
            patch.object(issues_ui, "save_draft", new=AsyncMock()) as save,
        ):
            reply = await issues_ui._resume(self.session, self.actor, draft_id)
        self.assertEqual(reply.text, "Выберите область")
        self.assertEqual(set_state.await_args.kwargs["step"], "scope")
        options.assert_awaited_once()
        save.assert_not_awaited()

    async def test_preview_offers_submission_only_after_attachment_step(self) -> None:
        data = {
            "title": "Нет света",
            "description": "В доме нет света",
            "summary_description": "Отсутствует электроснабжение в доме.",
            "scope": "entrance",
            "scope_label": "подъезд 2",
            "suggestions": [],
        }
        review = SimpleNamespace(step="review", data=data)
        attachments = SimpleNamespace(step="attachments", data=data)
        review_payloads = [
            button.payload
            for row in issues_ui._preview(review).buttons
            for button in row
        ]
        attachment_payloads = [
            button.payload
            for row in issues_ui._preview(attachments).buttons
            for button in row
        ]
        self.assertIn("i:issue:continue", review_payloads)
        self.assertNotIn("i:issue:submit", review_payloads)
        self.assertNotIn("i:issue:edit_summary", review_payloads)
        self.assertIn("i:issue:submit", attachment_payloads)
        self.assertIn("i:issue:edit_summary", attachment_payloads)
        self.assertIn(
            "Краткое формализованное описание: Отсутствует электроснабжение",
            issues_ui._preview(review).text,
        )
        self.assertIn(
            "Область: подъезд 2", issues_ui._preview(review).text
        )

    async def test_preview_explains_automatic_check_source_and_warning(self) -> None:
        base = {
            "title": "Нет света",
            "description": "Нет света",
            "scope": "house",
            "suggestions": [],
        }
        local = issues_ui._preview(
            SimpleNamespace(step="review", data={**base, "suggestion_source": "local"})
        )
        self.assertIn("Нейросеть не использовалась", local.text)
        warning = issues_ui._preview(
            SimpleNamespace(
                step="review",
                data={
                    **base,
                    "suggestion_source": "gigachat",
                    "description_check": "warning",
                    "description_warning": "Где именно нет света?",
                },
            )
        )
        self.assertIn("GigaChat обработал описание", warning.text)
        self.assertIn("Где именно нет света?", warning.text)
        self.assertIn("Автоматическая сводка недоступна", local.text)
        gigachat = issues_ui._preview(
            SimpleNamespace(
                step="review",
                data={**base, "suggestion_source": "gigachat", "description_check": "ok"},
            )
        )
        self.assertIn("GigaChat обработал описание", gigachat.text)

    async def test_card_shows_affected_entrance_and_apartment(self) -> None:
        card_id, house_id, category_id = uuid4(), uuid4(), uuid4()
        card = SimpleNamespace(
            id=card_id,
            house_id=house_id,
            category_id=category_id,
            title="Нет света",
            status="open",
            scope_all_house=False,
            current_note=None,
            close_result=None,
            author_user_id=self.actor.id,
        )
        target_entrance = SimpleNamespace(entrance_number=2, apartment_id=None)
        target_apartment = SimpleNamespace(entrance_number=None, apartment_id=uuid4())
        apartment = SimpleNamespace(entrance_number=3, apartment_number=14)
        self.session.get = AsyncMock(return_value=SimpleNamespace(name="Электричество"))
        self.session.execute = AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(target_entrance, None), (target_apartment, apartment)]
            )
        )
        self.session.scalar = AsyncMock(return_value=1)
        with (
            patch.object(
                issues_ui, "get_visible_card", new=AsyncMock(return_value=card)
            ),
            patch.object(issues_ui, "is_muted", new=AsyncMock(return_value=False)),
            patch.object(
                issues_ui, "_staff_can_manage", new=AsyncMock(return_value=False)
            ),
        ):
            reply = await issues_ui._card(self.session, self.actor, card_id)
        self.assertIn("Область: подъезды 2; квартиры 14", reply.text)
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"f:card:{card_id}", payloads)
        self.assertIn(f"i:discussion:{card_id}", payloads)
        self.assertNotIn(f"i:merge_suggest:{card_id}", payloads)
        staff = SimpleNamespace(id=uuid4(), kind="employee")
        with (
            patch.object(issues_ui, "get_visible_card", new=AsyncMock(return_value=card)),
            patch.object(issues_ui, "is_muted", new=AsyncMock(return_value=False)),
            patch.object(issues_ui, "_staff_can_manage", new=AsyncMock(return_value=True)),
        ):
            staff_reply = await issues_ui._card(self.session, staff, card_id)
        staff_payloads = [button.payload for row in staff_reply.buttons for button in row]
        self.assertIn(f"i:merge_suggest:{card_id}", staff_payloads)

    async def test_staff_suggested_merge_buttons_reuse_manual_confirmation(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="employee")
        card_id, candidate_id = uuid4(), uuid4()
        with (
            patch.object(
                issues_ui, "_staff_merge_candidates",
                new=AsyncMock(return_value=(
                    StaffMergeSuggestion([candidate_id], "local"),
                    [SimpleNamespace(id=candidate_id, title="Тот же лифт")],
                )),
            ) as suggest,
            patch.object(issues_ui, "set_dialog", new=AsyncMock()) as set_state,
        ):
            reply = await issues_ui.handle_action(
                self.session, actor, f"i:merge_suggest:{card_id}"
            )
        suggest.assert_awaited_once_with(self.session, actor, card_id)
        self.assertIn("без нейросети", reply.text)
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"i:merge_with:{candidate_id}", payloads)
        self.assertIn(f"i:merge:{card_id}", payloads)
        self.assertNotIn("i:merge_confirm", payloads)
        self.assertEqual(set_state.await_args.kwargs["flow_kind"], "issue_merge")
        self.assertEqual(set_state.await_args.kwargs["step"], "pick")

    async def test_resident_forged_merge_suggestion_button_is_denied(self) -> None:
        card_id = uuid4()
        with (
            patch.object(
                issues_ui, "_staff_merge_candidates",
                new=AsyncMock(side_effect=IssueError("issue_permission_denied", "Нет права", 403)),
            ),
            patch.object(issues_ui, "set_dialog", new=AsyncMock()) as set_state,
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:merge_suggest:{card_id}"
            )
        self.assertIn("Нет права", reply.text)
        set_state.assert_not_awaited()

    async def test_house_cards_have_pages_and_restore_list_page(self) -> None:
        house_id = uuid4()
        cards = [
            SimpleNamespace(id=uuid4(), title=f"Проблема {number}", status="open")
            for number in range(40)
        ]
        self.session.get = AsyncMock(
            return_value=SimpleNamespace(address_display="Дом 1")
        )
        with patch.object(
            issues_ui, "list_visible_cards", new=AsyncMock(return_value=cards)
        ) as visible:
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:house:{house_id}:2"
            )
        visible.assert_awaited_once_with(
            self.session, self.actor, house_id, include_closed=True
        )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"i:card:{cards[18].id}:2", payloads)
        self.assertNotIn(f"i:card:{cards[0].id}:2", payloads)
        self.assertIn(f"i:house:{house_id}:1", payloads)
        self.assertIn(f"i:house:{house_id}:3", payloads)
        self.assertIn("страница 2/3", reply.text)

        with patch.object(
            issues_ui, "list_visible_cards", new=AsyncMock(return_value=cards)
        ):
            final = await issues_ui.handle_action(
                self.session, self.actor, f"i:house:{house_id}:999"
            )
        self.assertIn("страница 3/3", final.text)
        final_payloads = [button.payload for row in final.buttons for button in row]
        self.assertIn(f"i:card:{cards[36].id}:3", final_payloads)
        self.assertNotIn(f"i:house:{house_id}:4", final_payloads)

    async def test_house_page_rechecks_access_before_showing_cards(self) -> None:
        house_id = uuid4()
        with patch.object(
            issues_ui,
            "list_visible_cards",
            new=AsyncMock(
                side_effect=IssueError("house_access_denied", "Нет доступа", 403)
            ),
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:house:{house_id}:2"
            )
        self.assertIn("Нет доступа", reply.text)
        self.session.rollback.assert_awaited_once()

    async def test_house_selector_is_paginated_too(self) -> None:
        house = SimpleNamespace(id=uuid4(), address_display="Последний дом")
        self.session.scalar = AsyncMock(return_value=41)
        self.session.scalars = AsyncMock(
            return_value=SimpleNamespace(all=lambda: [house])
        )
        reply = await issues_ui.handle_action(self.session, self.actor, "i:houses:3")
        self.assertIn("страница 3/3", reply.text)
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"i:house:{house.id}", payloads)
        self.assertIn("i:houses:2", payloads)
        self.assertNotIn("i:houses:4", payloads)
        self.assertEqual(
            self.session.scalars.await_args.args[0]._offset_clause.value, 40
        )

    async def test_card_back_button_returns_to_previous_list_page(self) -> None:
        card_id, house_id, category_id = uuid4(), uuid4(), uuid4()
        card = SimpleNamespace(
            id=card_id,
            house_id=house_id,
            category_id=category_id,
            title="Лифт",
            status="open",
            scope_all_house=True,
            current_note=None,
            close_result=None,
            author_user_id=self.actor.id,
        )
        self.session.get = AsyncMock(return_value=SimpleNamespace(name="Лифты"))
        self.session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        self.session.scalar = AsyncMock(return_value=1)
        with (
            patch.object(
                issues_ui, "get_visible_card", new=AsyncMock(return_value=card)
            ),
            patch.object(issues_ui, "is_muted", new=AsyncMock(return_value=False)),
            patch.object(
                issues_ui, "_staff_can_manage", new=AsyncMock(return_value=False)
            ),
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:card:{card_id}:3"
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"i:house:{house_id}:3", payloads)

    async def test_discussion_pages_include_all_messages_and_recheck_visibility(
        self,
    ) -> None:
        card_id = uuid4()
        card = SimpleNamespace(id=card_id)
        stamp = datetime.now(UTC)
        report = SimpleNamespace(
            id=uuid4(),
            created_at=stamp,
            author_user_id=self.actor.id,
            raw_description="Исходное описание",
        )
        message = SimpleNamespace(
            id=uuid4(),
            created_at=stamp + timedelta(seconds=1),
            author_user_id=self.actor.id,
            kind="resident_comment",
            body="длинный текст " * 260 + "КОНЕЦ",
        )
        staff_id = uuid4()
        staff_message = SimpleNamespace(
            id=uuid4(),
            created_at=stamp + timedelta(seconds=2),
            author_user_id=staff_id,
            kind="official_uk",
            body="Ответ сотрудника",
        )

        async def scalars(statement):
            entity = statement.column_descriptions[0]["entity"]
            rows = (
                [report]
                if entity is issues_ui.IssueReport
                else [message, staff_message]
            )
            return SimpleNamespace(all=lambda: rows)

        self.session.scalars = AsyncMock(side_effect=scalars)
        with patch.object(
            issues_ui, "get_visible_card", new=AsyncMock(return_value=card)
        ) as visible:
            first = await issues_ui.handle_action(
                self.session, self.actor, f"i:discussion:{card_id}"
            )
            second = await issues_ui.handle_action(
                self.session, self.actor, f"i:discussion:{card_id}:2"
            )
        self.assertIn("Исходное описание", first.text)
        self.assertIn("КОНЕЦ", second.text)
        self.assertIn("Ваше дополнение", first.text)
        self.assertIn("УК: Ответ сотрудника", second.text)
        self.assertNotIn(str(self.actor.id), first.text + second.text)
        self.assertNotIn(str(staff_id), first.text + second.text)
        self.assertIn(
            f"i:discussion:{card_id}:2",
            [button.payload for row in first.buttons for button in row],
        )
        self.assertEqual(visible.await_count, 2)

    async def test_discussion_denies_stale_access(self) -> None:
        card_id = uuid4()
        with patch.object(
            issues_ui,
            "get_visible_card",
            new=AsyncMock(
                side_effect=IssueError("issue_not_found", "Проблема не найдена", 404)
            ),
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:discussion:{card_id}:2"
            )
        self.assertIn("Проблема не найдена", reply.text)
        self.session.scalars.assert_not_awaited()

    async def test_audit_history_has_next_page(self) -> None:
        card_id = uuid4()
        stamp = datetime.now(UTC)
        rows = [
            SimpleNamespace(
                id=uuid4(),
                created_at=stamp,
                action=f"event-{number}-" + "x" * 90,
                actor_user_id=self.actor.id,
            )
            for number in range(40)
        ]
        rows.insert(0, SimpleNamespace(id=uuid4(), created_at=stamp, action="comment_added", actor_user_id=self.actor.id))
        self.session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
        with (
            patch.object(
                issues_ui,
                "get_visible_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id)),
            ),
            patch.object(
                issues_ui, "merged_card_ids", new=AsyncMock(return_value={card_id})
            ),
        ):
            first = await issues_ui.handle_action(
                self.session, self.actor, f"i:history:{card_id}"
            )
            last = await issues_ui.handle_action(
                self.session, self.actor, f"i:history:{card_id}:999"
            )
        self.assertIn(
            f"i:history:{card_id}:2",
            [button.payload for row in first.buttons for button in row],
        )
        self.assertIn("event-39", last.text)
        self.assertNotIn("comment_added", first.text + last.text)
        self.assertNotIn(str(self.actor.id), first.text + last.text)

    async def test_scope_creates_shared_draft_before_review(self) -> None:
        house_id, category_id, draft_id, apartment_id = uuid4(), uuid4(), uuid4(), uuid4()
        self.session.get = AsyncMock(return_value=SimpleNamespace(archived_at=None))
        dialog = SimpleNamespace(
            data={
                "house_id": str(house_id),
                "category_id": str(category_id),
                "description": "В подъезде не работает лифт",
            },
            draft_id=None,
            step="scope",
            updated_at=None,
        )
        suggestion = SimpleNamespace(
            suggested_title="Не работает лифт",
            similar_card_ids=[],
            candidates=[],
            source="gigachat",
            description_check="ok",
            description_warning=None,
            summary_description="Не работает лифт в подъезде.",
        )
        with (
            patch.object(
                issues_ui, "suggest_issue", new=AsyncMock(return_value=suggestion)
            ),
            patch.object(
                issues_ui,
                "save_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=draft_id, revision=1)),
            ) as save,
            patch.object(issues_ui, "_active_resident_apartments", new=AsyncMock(return_value={
                apartment_id: SimpleNamespace(id=apartment_id, entrance_number=2, apartment_number=14),
            })),
        ):
            result = await issues_ui._prepare_preview(
                self.session, self.actor, dialog, "entrance", apartment_id
            )
        self.assertIsInstance(result, UiReply)
        self.assertEqual(dialog.step, "review")
        self.assertEqual(dialog.draft_id, draft_id)
        self.assertEqual(save.await_args.kwargs["payload"]["scope"], "entrance")
        self.assertEqual(save.await_args.kwargs["payload"]["apartment_id"], str(apartment_id))
        self.assertEqual(
            save.await_args.kwargs["payload"]["summary_description"],
            "Не работает лифт в подъезде.",
        )
        self.assertIn("GigaChat обработал описание", result.text)

    async def test_local_preview_keeps_raw_and_summary_in_separate_fields(self) -> None:
        house_id, category_id = uuid4(), uuid4()
        apartment_id = uuid4()
        self.session.get = AsyncMock(return_value=SimpleNamespace(archived_at=None))
        dialog = SimpleNamespace(
            data={
                "house_id": str(house_id),
                "category_id": str(category_id),
                "description": "Лифт не работает с утра",
            },
            draft_id=None,
            step="scope",
        )
        suggestion = SimpleNamespace(
            suggested_title="Лифт не работает",
            similar_card_ids=[],
            candidates=[],
            source="local",
            description_check="not_checked",
            description_warning=None,
            summary_description=None,
        )
        with (
            patch.object(issues_ui, "suggest_issue", new=AsyncMock(return_value=suggestion)),
            patch.object(issues_ui, "_active_resident_apartments", new=AsyncMock(return_value={
                apartment_id: SimpleNamespace(id=apartment_id, entrance_number=2, apartment_number=14),
            })),
            patch.object(
                issues_ui, "save_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=uuid4(), revision=1)),
            ) as save,
        ):
            reply = await issues_ui._prepare_preview(
                self.session, self.actor, dialog, "house"
            )
        payload = save.await_args.kwargs["payload"]
        self.assertEqual(payload["description"], "Лифт не работает с утра")
        self.assertEqual(payload["summary_description"], "Лифт не работает с утра")
        self.assertIn("Автоматическая сводка недоступна", reply.text)

    async def test_summary_can_be_changed_only_on_final_screen(self) -> None:
        draft_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="issue_new",
            step="review",
            draft_id=draft_id,
            data={
                "title": "Лифт",
                "description": "Лифт не работает",
                "summary_description": "Лифт неисправен.",
                "scope": "house",
                "draft_revision": 2,
                "suggestions": [],
            },
        )
        with patch.object(issues_ui, "get_dialog", new=AsyncMock(return_value=dialog)):
            blocked = await issues_ui.handle_action(
                self.session, self.actor, "i:issue:edit_summary"
            )
            self.assertIn("уже неактуален", blocked.text)
            dialog.step = "attachments"
            opened = await issues_ui.handle_action(
                self.session, self.actor, "i:issue:edit_summary"
            )
        self.assertEqual(dialog.step, "edit_summary")
        self.assertIn("формализованное описание", opened.text)

        draft = SimpleNamespace(
            id=draft_id,
            revision=2,
            payload={
                "description": "Лифт не работает",
                "summary_description": "Лифт неисправен.",
            },
        )
        with (
            patch.object(issues_ui, "get_draft", new=AsyncMock(return_value=draft)),
            patch.object(
                issues_ui,
                "save_draft",
                new=AsyncMock(return_value=SimpleNamespace(revision=3)),
            ) as save,
        ):
            reply = await issues_ui.handle_text(
                self.session, self.actor, dialog, "  Лифт остановился  "
            )
        self.assertEqual(dialog.step, "attachments")
        self.assertEqual(dialog.data["draft_revision"], 3)
        self.assertEqual(
            save.await_args.kwargs["payload"],
            {
                "description": "Лифт не работает",
                "summary_description": "Лифт остановился",
            },
        )
        self.assertIn("Краткое формализованное описание: Лифт остановился", reply.text)

    async def test_duplicate_button_adds_resident_report_to_existing_card(self) -> None:
        card_id, draft_id = uuid4(), uuid4()
        dialog = SimpleNamespace(
            data={
                "description": "Лифт не работает на моём этаже",
                "suggestions": [{"id": str(card_id), "title": "Лифт не работает"}],
                "draft_revision": 2,
            },
            draft_id=draft_id,
        )
        with (
            patch.object(issues_ui, "_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(
                issues_ui,
                "support_card",
                new=AsyncMock(return_value=(SimpleNamespace(id=card_id), uuid4())),
            ) as support,
            patch.object(
                issues_ui,
                "get_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=draft_id, revision=2)),
            ),
            patch.object(issues_ui, "mark_submitted", new=AsyncMock()) as submitted,
            patch.object(issues_ui, "clear_dialog", new=AsyncMock()) as cleared,
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:issue:duplicate:{card_id}"
            )
        support.assert_awaited_once_with(
            self.session, self.actor, card_id, "Лифт не работает на моём этаже"
        )
        submitted.assert_awaited_once_with(
            self.session, self.actor, draft_id=draft_id, revision=2
        )
        cleared.assert_awaited_once_with(self.session, self.actor.id)
        self.assertIn("существующей проблеме", reply.text)

    async def test_duplicate_after_upload_attaches_staged_files_to_new_report(
        self,
    ) -> None:
        card_id, draft_id, report_id, file_id = (uuid4() for _ in range(4))
        dialog = SimpleNamespace(
            flow_kind="issue_new",
            step="attachments",
            draft_id=draft_id,
            data={
                "description": "Лифт не работает",
                "suggestions": [{"id": str(card_id), "title": "Лифт сломан"}],
                "draft_revision": 2,
            },
        )
        self.session.scalars.return_value = SimpleNamespace(all=lambda: [file_id])
        calls = []

        async def submitted(*_args, **_kwargs):
            calls.append("submitted")

        async def supported(*_args):
            calls.append("supported")
            return SimpleNamespace(id=card_id), report_id

        async def attached(*_args, **_kwargs):
            calls.append("attached")

        with (
            patch.object(issues_ui, "get_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(
                issues_ui,
                "get_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=draft_id, revision=2)),
            ),
            patch.object(
                issues_ui, "mark_submitted", new=AsyncMock(side_effect=submitted)
            ),
            patch.object(
                issues_ui, "support_card", new=AsyncMock(side_effect=supported)
            ),
            patch.object(
                issues_ui, "attach", new=AsyncMock(side_effect=attached)
            ) as attach,
            patch.object(issues_ui, "clear_dialog", new=AsyncMock()),
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, f"i:issue:duplicate:{card_id}"
            )
        self.assertIn("существующей проблеме", reply.text)
        self.assertEqual(calls, ["submitted", "supported", "attached"])
        attach.assert_awaited_once_with(
            self.session,
            self.actor,
            file_id=file_id,
            parent_kind="issue_report",
            parent_id=report_id,
        )

    async def test_submission_rejects_stale_shared_draft(self) -> None:
        draft_id = uuid4()
        dialog = SimpleNamespace(
            data={"draft_revision": 2, "house_id": str(uuid4())}, draft_id=draft_id
        )
        with (
            patch.object(issues_ui, "_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(
                issues_ui,
                "get_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=draft_id, revision=3)),
            ),
            patch.object(issues_ui, "_send_text", new=AsyncMock()) as send,
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, "i:issue:submit"
            )
        self.assertIn("Черновик изменился", reply.text)
        send.assert_not_awaited()
        self.session.rollback.assert_awaited_once()

    async def test_submission_uses_existing_draft_send_with_files(self) -> None:
        draft_id, house_id = uuid4(), uuid4()
        dialog = SimpleNamespace(
            data={"draft_revision": 2, "house_id": str(house_id)}, draft_id=draft_id
        )
        with (
            patch.object(issues_ui, "_dialog", new=AsyncMock(return_value=dialog)),
            patch.object(
                issues_ui,
                "get_draft",
                new=AsyncMock(return_value=SimpleNamespace(id=draft_id, revision=2)),
            ),
            patch.object(
                issues_ui,
                "_send_text",
                new=AsyncMock(return_value="Обращение отправлено"),
            ) as send,
            patch.object(issues_ui, "clear_dialog", new=AsyncMock()) as cleared,
        ):
            reply = await issues_ui.handle_action(
                self.session, self.actor, "i:issue:submit"
            )
        send.assert_awaited_once_with(self.session, self.actor, draft_id, 2)
        cleared.assert_awaited_once_with(self.session, self.actor.id)
        self.assertEqual(reply.buttons[0][0].payload, f"i:house:{house_id}")

    async def test_status_note_calls_domain_service(self) -> None:
        card_id = uuid4()
        dialog = SimpleNamespace(
            flow_kind="issue_status",
            step="note",
            data={"card_id": str(card_id), "status": "in_progress"},
        )
        with (
            patch.object(issues_ui, "set_status", new=AsyncMock()) as status,
            patch.object(issues_ui, "clear_dialog", new=AsyncMock()),
        ):
            reply = await issues_ui.handle_text(
                self.session, self.actor, dialog, "Мастер выехал"
            )
        status.assert_awaited_once_with(
            self.session,
            self.actor,
            card_id,
            status="in_progress",
            note="Мастер выехал",
            close_result=None,
        )
        self.assertIn("обновлён", reply.text)


if __name__ == "__main__":
    unittest.main()
