"""Page splitting must never silently drop discussion text."""

import re
import unittest

from src.bot.handlers.access_discussion import discussion_pages, page_number


class AccessDiscussionTests(unittest.TestCase):
    def test_long_message_can_be_reassembled_exactly(self) -> None:
        body = "начало\n" + ("0123456789" * 750) + " конец"
        pages = discussion_pages(
            [{"author_user_id": "user-1", "text": body}],
            actor_id="user-1",
            applicant_id="user-1",
            max_chars=500,
        )
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 500 for page in pages))
        fragments = []
        for page in pages:
            for piece in page.split("\n\n"):
                fragments.append(re.sub(r"^1\. Вы(?: \(продолжение\))?: ", "", piece))
        self.assertEqual("".join(fragments), body)

    def test_page_number_is_one_based(self) -> None:
        self.assertEqual(page_number("2"), 2)
        for raw in ("0", "-1", "не число"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                page_number(raw)

    def test_author_kind_labels_only_other_people_and_keeps_legacy_fallback(
        self,
    ) -> None:
        messages = [
            {"author_user_id": "me", "author_kind": "admin", "text": "своё"},
            {
                "author_user_id": "applicant",
                "author_kind": "resident",
                "text": "заявка",
            },
            {"author_user_id": "admin", "author_kind": "admin", "text": "решение"},
            {"author_user_id": "support", "author_kind": "support", "text": "проверка"},
            {"author_user_id": "staff", "author_kind": "employee", "text": "ответ"},
            {"author_user_id": "old", "text": "старое"},
        ]
        pages = discussion_pages(
            messages, actor_id="me", applicant_id="applicant", max_chars=500
        )
        self.assertEqual(
            pages,
            [
                "1. Вы: своё\n\n2. Заявитель: заявка\n\n"
                "3. Администратор: решение\n\n4. Поддержка: проверка\n\n"
                "5. Сотрудник УК: ответ\n\n6. Другая сторона: старое"
            ],
        )


if __name__ == "__main__":
    unittest.main()
