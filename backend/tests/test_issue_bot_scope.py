import unittest

from src.bot.handlers.issues_text import _scope


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


if __name__ == "__main__":
    unittest.main()
