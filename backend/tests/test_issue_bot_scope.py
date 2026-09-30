import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers.issues_text import (
    _resident_scope,
    _scope,
    _staff_merge_candidates,
    handle_issue_text,
)
from src.domain.issues.service import IssueError
from src.domain.issues.staff_suggest import StaffMergeSuggestion


class IssueBotScopeTests(unittest.TestCase):
    def test_house_scope(self) -> None:
        self.assertEqual(_scope("all"), (True, [], []))

    def test_mixed_scope(self) -> None:
        self.assertEqual(
            _scope("e:2,1+a:3:18,2:7"),
            (False, [1, 2], [(2, 7), (3, 18)]),
        )

    def test_apartment_scope_needs_only_apartment_number(self) -> None:
        self.assertEqual(
            _scope("e:2+a:18,7"),
            (False, [2], [(None, 7), (None, 18)]),
        )

    def test_rejects_empty_and_invalid_scope(self) -> None:
        for value in ("e:", "a:", "e:0", "a:1:0", "a:1:2,3:2", "e:1+e:2", "all+a:1:2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _scope(value)


class ResidentScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_scope_only_resolves_owned_locations(self) -> None:
        house_id, first_id, second_id = uuid4(), uuid4(), uuid4()
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(archived_at=None)))
        apartments = {
            first_id: SimpleNamespace(id=first_id, entrance_number=2, apartment_number=17),
            second_id: SimpleNamespace(id=second_id, entrance_number=3, apartment_number=45),
        }
        with patch(
            "src.bot.handlers.issues_text._active_resident_apartments",
            new=AsyncMock(return_value=apartments),
        ):
            self.assertEqual(
                await _resident_scope(session, actor, house_id, "квартира:17"),
                ("apartment", first_id),
            )
            self.assertEqual(
                await _resident_scope(session, actor, house_id, "подъезд:3"),
                ("entrance", second_id),
            )
            with self.assertRaises(IssueError):
                await _resident_scope(session, actor, house_id, "квартира:99")
            with self.assertRaises(ValueError):
                await _resident_scope(session, actor, house_id, "квартира")
            with self.assertRaises(ValueError):
                await _resident_scope(session, actor, house_id, "e:2+a:17")

    async def test_newissue_passes_only_derived_scope_to_domain(self) -> None:
        house_id, category_id, apartment_id, card_id = uuid4(), uuid4(), uuid4(), uuid4()
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        with (
            patch("src.bot.handlers.issues_text._resident_scope", new=AsyncMock(return_value=("apartment", apartment_id))),
            patch("src.bot.handlers.issues_text._category", new=AsyncMock(return_value=SimpleNamespace(id=category_id))),
            patch("src.bot.handlers.issues_text.create_card", new=AsyncMock(return_value=SimpleNamespace(id=card_id, title="Лифт"))) as create,
        ):
            reply = await handle_issue_text(
                session, actor, f"/newissue {house_id} | lift | квартира:17 | Лифт | Сломался лифт"
            )
        self.assertIn(str(card_id), reply)
        create.assert_awaited_once_with(
            session, actor, house_id=house_id, category_id=category_id, title="Лифт",
            description="Сломался лифт", summary_description="Сломался лифт",
            scope="apartment", apartment_id=apartment_id,
        )

    async def test_text_suggest_passes_selected_apartment_to_domain(self) -> None:
        house_id, apartment_id = uuid4(), uuid4()
        session = SimpleNamespace()
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        suggestion = SimpleNamespace(
            suggested_title="Сломался лифт",
            summary_description=None,
            source="local",
            similar_card_ids=[],
            candidates=[],
        )
        with (
            patch("src.bot.handlers.issues_text._resident_scope", new=AsyncMock(return_value=("apartment", apartment_id))),
            patch("src.bot.handlers.issues_text.suggest_issue", new=AsyncMock(return_value=suggestion)) as suggest,
        ):
            reply = await handle_issue_text(
                session, actor, f"/suggest {house_id} | - | квартира:17 | Сломался лифт"
            )
        self.assertIn("Сломался лифт", reply)
        suggest.assert_awaited_once_with(
            session, actor, house_id=house_id, description="Сломался лифт",
            category_id=None, scope="apartment", apartment_id=apartment_id,
        )


class StaffMergeBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_cannot_read_suggested_card_details(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="resident")
        session = SimpleNamespace(get=AsyncMock(), scalars=AsyncMock())
        with patch(
            "src.bot.handlers.issues_text.suggest_staff_merges",
            new=AsyncMock(side_effect=IssueError("issue_permission_denied", "Нет права", 403)),
        ):
            with self.assertRaises(IssueError):
                await _staff_merge_candidates(session, actor, uuid4())
        session.get.assert_not_awaited()
        session.scalars.assert_not_awaited()

    async def test_text_command_labels_ai_and_requires_manual_merge(self) -> None:
        card_id, other_id = uuid4(), uuid4()
        actor = SimpleNamespace(id=uuid4(), kind="employee")
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch(
            "src.bot.handlers.issues_text._staff_merge_candidates",
            new=AsyncMock(return_value=(
                StaffMergeSuggestion([other_id], "gigachat"),
                [SimpleNamespace(id=other_id, title="Не работает лифт")],
            )),
        ) as suggest:
            reply = await handle_issue_text(session, actor, f"/merge_suggest {card_id}")
        suggest.assert_awaited_once_with(session, actor, card_id)
        self.assertIn("GigaChat", reply)
        self.assertIn(str(other_id), reply)
        self.assertIn("/merge", reply)
        session.commit.assert_not_awaited()

    async def test_text_command_explains_local_fallback(self) -> None:
        actor = SimpleNamespace(id=uuid4(), kind="employee")
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch(
            "src.bot.handlers.issues_text._staff_merge_candidates",
            new=AsyncMock(return_value=(StaffMergeSuggestion([], "local"), [])),
        ):
            reply = await handle_issue_text(session, actor, f"/merge_suggest {uuid4()}")
        self.assertIn("без нейросети", reply)
        self.assertIn("не найдено", reply)


if __name__ == "__main__":
    unittest.main()
