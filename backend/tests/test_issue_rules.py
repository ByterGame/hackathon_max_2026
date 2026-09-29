import unittest
from datetime import UTC, datetime
from uuid import UUID

from src.domain.issues.rules import can_merge_issues, can_view_issue, primary_issue_id


class IssueRulesTests(unittest.TestCase):
    def test_visibility_requires_current_house_access(self) -> None:
        apartment = UUID(int=1)
        base = dict(
            is_author=True,
            scope_all_house=False,
            granted_apartment_ids={apartment},
            granted_entrance_numbers={2},
            target_apartment_ids=set(),
            target_entrance_numbers={3},
        )
        self.assertFalse(can_view_issue(has_house_access=False, **base))
        self.assertTrue(can_view_issue(has_house_access=True, **base))

    def test_visibility_matches_apartment_or_entrance(self) -> None:
        apartment = UUID(int=1)
        base = dict(
            has_house_access=True,
            is_author=False,
            scope_all_house=False,
            granted_apartment_ids={apartment},
            granted_entrance_numbers={2},
        )
        self.assertTrue(
            can_view_issue(
                target_apartment_ids={apartment}, target_entrance_numbers=set(), **base
            )
        )
        self.assertTrue(
            can_view_issue(
                target_apartment_ids=set(), target_entrance_numbers={2}, **base
            )
        )
        self.assertFalse(
            can_view_issue(
                target_apartment_ids=set(), target_entrance_numbers={3}, **base
            )
        )

    def test_merge_requires_same_house_and_two_active_cards(self) -> None:
        args = dict(
            left_id=UUID(int=1),
            right_id=UUID(int=2),
            left_house_id=UUID(int=3),
            right_house_id=UUID(int=3),
            left_status="open",
            right_status="in_progress",
            left_merged=False,
            right_merged=False,
        )
        self.assertTrue(can_merge_issues(**args))
        self.assertFalse(can_merge_issues(**(args | {"right_status": "closed"})))
        self.assertFalse(can_merge_issues(**(args | {"right_house_id": UUID(int=4)})))

    def test_primary_card_is_earliest(self) -> None:
        first = UUID(int=1)
        second = UUID(int=2)
        moment = datetime(2026, 9, 27, tzinfo=UTC)
        self.assertEqual(primary_issue_id(first, moment, second, moment), first)


if __name__ == "__main__":
    unittest.main()
