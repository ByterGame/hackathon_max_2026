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


if __name__ == "__main__":
    unittest.main()
