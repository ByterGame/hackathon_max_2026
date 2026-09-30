"""Text issue commands keep every accessible card and history entry reachable."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers import issues_text
from src.domain.issues import service as issue_service
from src.domain.issues.service import IssueError


class IssueTextPaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="resident")
        self.session = SimpleNamespace(rollback=AsyncMock())

    def test_text_chunks_keep_every_character(self) -> None:
        body = "Первая запись\n" + "а" * 4500 + "\nПоследняя запись"
        chunks = issues_text._text_chunks(body)
        self.assertEqual("".join(chunks), body)
        self.assertTrue(
            all(len(chunk) <= issues_text.ISSUE_TEXT_PAGE_CHARS for chunk in chunks)
        )

    async def test_suggest_command_labels_gigachat_response(self) -> None:
        house_id = uuid4()
        suggestion = SimpleNamespace(
            suggested_title="Не работает лифт",
            summary_description="Лифт в доме не реагирует на вызов.",
            source="gigachat",
            description_check="warning",
            description_warning="Укажите номер подъезда.",
            similar_card_ids=[],
            candidates=[],
        )
        with patch.object(
            issues_text, "suggest_issue", new=AsyncMock(return_value=suggestion)
        ):
            reply = await issues_text.handle_issue_text(
                self.session, self.actor, f"/suggest {house_id} | - | дом | Лифт сломан"
            )
        self.assertIn("GigaChat обработал описание", reply)
        self.assertIn("Лифт в доме не реагирует на вызов", reply)
        self.assertIn("Укажите номер подъезда", reply)

    async def test_issues_command_reaches_last_page_and_rechecks_access(self) -> None:
        house_id = uuid4()
        cards = [
            SimpleNamespace(id=uuid4(), title=f"Проблема {number}", status="open")
            for number in range(40)
        ]
        with patch.object(
            issues_text, "list_visible_cards", new=AsyncMock(return_value=cards)
        ) as visible:
            first = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issues {house_id}"
            )
            second = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issues {house_id} 2"
            )
            last = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issues {house_id} 999"
            )
        self.assertIn("страница 1/3", first)
        self.assertIn(f"Далее: /issues {house_id} 2", first)
        self.assertIn(str(cards[18].id), second)
        self.assertNotIn(str(cards[0].id), second)
        self.assertIn("страница 3/3", last)
        self.assertIn(str(cards[-1].id), last)
        self.assertEqual(visible.await_count, 3)
        self.assertEqual(visible.await_args.args, (self.session, self.actor, house_id))
        self.assertEqual(visible.await_args.kwargs, {"include_closed": True})

    async def test_invalid_page_never_reads_cards(self) -> None:
        house_id = uuid4()
        with patch.object(
            issues_text, "list_visible_cards", new=AsyncMock()
        ) as visible:
            reply = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issues {house_id} 0"
            )
        self.assertIn("Номер страницы", reply)
        visible.assert_not_awaited()

    async def test_issue_detail_does_not_truncate_long_discussion(self) -> None:
        card_id = uuid4()
        card = SimpleNamespace(
            id=card_id,
            title="Лифт",
            status="open",
            category_id=uuid4(),
            scope_all_house=True,
            current_note=None,
            close_result=None,
            version=1,
        )
        report = SimpleNamespace(
            author_user_id=self.actor.id, raw_description="Первое описание"
        )
        message = SimpleNamespace(
            kind="resident_comment",
            author_user_id=self.actor.id,
            body="длинный комментарий " * 220 + "ПОСЛЕДНИЙ СИМВОЛ",
        )
        self.session.get = AsyncMock(return_value=SimpleNamespace(name="Лифты"))
        self.session.scalar = AsyncMock(return_value=1)
        self.session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        self.session.scalars = AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: [report]),
                SimpleNamespace(all=lambda: [message]),
                SimpleNamespace(all=lambda: [report]),
                SimpleNamespace(all=lambda: [message]),
            ]
        )
        with patch.object(
            issues_text, "get_visible_card", new=AsyncMock(return_value=card)
        ) as visible:
            first = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issue {card_id}"
            )
            last = await issues_text.handle_issue_text(
                self.session, self.actor, f"/issue {card_id} 999"
            )
        self.assertIn("Первое описание", first)
        self.assertIn(f"Далее: /issue {card_id} 2", first)
        self.assertIn("ПОСЛЕДНИЙ СИМВОЛ", last)
        self.assertNotIn(str(self.actor.id), first + last)
        self.assertEqual(visible.await_count, 2)

    async def test_history_command_reaches_last_audit_event(self) -> None:
        card_id = uuid4()
        stamp = datetime.now(UTC)
        events = [
            SimpleNamespace(
                id=uuid4(),
                created_at=stamp,
                action=f"event-{number}-" + "x" * 90,
                actor_user_id=self.actor.id,
            )
            for number in range(40)
        ]
        events.insert(0, SimpleNamespace(id=uuid4(), created_at=stamp, action="comment_added", actor_user_id=self.actor.id))
        self.session.scalars = AsyncMock(
            return_value=SimpleNamespace(all=lambda: events)
        )
        with (
            patch.object(
                issues_text,
                "get_visible_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id)),
            ),
            patch.object(
                issues_text, "merged_card_ids", new=AsyncMock(return_value={card_id})
            ),
        ):
            first = await issues_text.handle_issue_text(
                self.session, self.actor, f"/history {card_id}"
            )
            last = await issues_text.handle_issue_text(
                self.session, self.actor, f"/history {card_id} 999"
            )
        self.assertIn("Далее: /history", first)
        self.assertIn("event-39", last)
        self.assertNotIn("comment_added", first + last)
        self.assertNotIn(str(self.actor.id), first + last)

    def test_discussion_participant_labels_do_not_include_user_ids(self) -> None:
        another_user_id = uuid4()
        self.assertEqual(
            issues_text._participant_label(self.actor.id, self.actor.id), "Вы"
        )
        self.assertEqual(
            issues_text._participant_label(self.actor.id, another_user_id), "Жилец"
        )
        self.assertEqual(
            issues_text._participant_label(
                self.actor.id, another_user_id, official=True
            ),
            "УК",
        )

    async def test_history_denies_card_after_access_revocation(self) -> None:
        card_id = uuid4()
        self.session.scalars = AsyncMock()
        with patch.object(
            issues_text,
            "get_visible_card",
            new=AsyncMock(
                side_effect=IssueError("issue_not_found", "Проблема не найдена", 404)
            ),
        ):
            reply = await issues_text.handle_issue_text(
                self.session, self.actor, f"/history {card_id} 2"
            )
        self.assertIn("Проблема не найдена", reply)
        self.session.scalars.assert_not_awaited()

    async def test_visible_list_has_stable_secondary_order(self) -> None:
        house_id = uuid4()
        self.session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        with (
            patch.object(
                issue_service,
                "_house",
                new=AsyncMock(return_value=SimpleNamespace(id=house_id)),
            ),
            patch.object(
                issue_service, "_staff_assignment", new=AsyncMock(return_value=object())
            ),
        ):
            await issue_service.list_visible_cards(
                self.session, self.actor, house_id, include_closed=True
            )
        query = str(self.session.scalars.await_args.args[0])
        self.assertIn("created_at DESC", query)
        self.assertIn("id DESC", query)


if __name__ == "__main__":
    unittest.main()
