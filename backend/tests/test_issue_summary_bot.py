"""Bot entry points keep the formalized summary separate from the resident text."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.bot.handlers import issues_text


class IssueSummaryTextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = SimpleNamespace(id=uuid4(), kind="resident")
        self.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    async def test_suggest_shows_formalized_summary(self) -> None:
        house_id = uuid4()
        suggestion = SimpleNamespace(
            suggested_title="Лифт не работает",
            summary_description="Лифт не работает в доме.",
            source="gigachat",
            description_check="ok",
            description_warning=None,
            similar_card_ids=[],
            candidates=[],
        )
        with patch.object(
            issues_text, "suggest_issue", new=AsyncMock(return_value=suggestion)
        ):
            reply = await issues_text.handle_issue_text(
                self.session, self.actor, f"/suggest {house_id} | - | Лифт сломан"
            )
        self.assertIn("Краткое формализованное описание: Лифт не работает в доме.", reply)
        self.assertIn("GigaChat обработал описание", reply)

    async def test_direct_create_passes_raw_text_as_summary_fallback(self) -> None:
        house_id, category_id, card_id = uuid4(), uuid4(), uuid4()
        with (
            patch.object(
                issues_text,
                "_category",
                new=AsyncMock(return_value=SimpleNamespace(id=category_id)),
            ),
            patch.object(
                issues_text,
                "create_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id, title="Лифт")),
            ) as create,
        ):
            reply = await issues_text.handle_issue_text(
                self.session,
                self.actor,
                f"/newissue {house_id} | elevator | all | Лифт | Не работает",
            )
        self.assertEqual(create.await_args.kwargs["description"], "Не работает")
        self.assertEqual(create.await_args.kwargs["summary_description"], "Не работает")
        self.assertIn(str(card_id), reply)


if __name__ == "__main__":
    unittest.main()
