"""Staff suggestions must not expose apartment issues to residents."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from src.domain.issues.service import IssueError
from src.domain.issues.staff_suggest import (
    _rank_candidates,
    _validated_ids,
    suggest_staff_merges,
)


def card(number: int, house_id: UUID, **overrides: object) -> SimpleNamespace:
    values = dict(
        id=UUID(int=number),
        house_id=house_id,
        category_id=UUID(int=1),
        title="Не работает отопление",
        summary_description="В квартире нет тепла в батареях",
        status="open",
        merged_into_id=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class StaffMergeSuggestionTests(unittest.TestCase):
    def test_candidates_include_other_apartments_but_not_other_houses_or_closed_cards(self) -> None:
        house_id = UUID(int=20)
        source = card(1, house_id)
        other_apartment = card(2, house_id)
        same_house_closed = card(3, house_id, status="closed")
        other_house = card(4, UUID(int=21))
        merged = card(5, house_id, merged_into_id=source.id)
        result = _rank_candidates(source, [source, other_apartment, same_house_closed, other_house, merged])
        self.assertEqual([item.id for item in result], [other_apartment.id])

    def test_provider_cannot_return_card_outside_authorized_candidate_set(self) -> None:
        allowed_id = UUID(int=2)
        self.assertEqual(
            _validated_ids({"similar_card_ids": [str(allowed_id)]}, {allowed_id}),
            [allowed_id],
        )
        self.assertIsNone(
            _validated_ids({"similar_card_ids": [str(UUID(int=3))]}, {allowed_id})
        )
        self.assertIsNone(
            _validated_ids({"similar_card_ids": [str(allowed_id), str(allowed_id)]}, {allowed_id})
        )
        self.assertIsNone(
            _validated_ids({"similar_card_ids": [str(allowed_id)], "extra": True}, {allowed_id})
        )


class StaffMergeSuggestionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_is_rejected_before_card_lookup(self) -> None:
        session = SimpleNamespace(get=AsyncMock())
        with self.assertRaises(IssueError) as caught:
            await suggest_staff_merges(session, SimpleNamespace(kind="resident"), UUID(int=1))
        self.assertEqual(caught.exception.status_code, 403)
        session.get.assert_not_awaited()

    async def test_employee_without_issue_permission_is_rejected_before_candidate_query(self) -> None:
        house_id = UUID(int=20)
        source = card(1, house_id)
        session = SimpleNamespace(get=AsyncMock(return_value=source), scalars=AsyncMock())
        with (
            patch("src.domain.issues.staff_suggest._house", new=AsyncMock(return_value=SimpleNamespace(id=house_id))),
            patch("src.domain.issues.staff_suggest._staff_assignment", new=AsyncMock(return_value=SimpleNamespace(can_manage_issues=False))),
        ):
            with self.assertRaises(IssueError) as caught:
                await suggest_staff_merges(session, SimpleNamespace(kind="employee"), source.id)
        self.assertEqual(caught.exception.status_code, 403)
        session.scalars.assert_not_awaited()

    async def test_employee_of_another_company_cannot_probe_issue_existence(self) -> None:
        house_id = UUID(int=20)
        source = card(1, house_id)
        session = SimpleNamespace(get=AsyncMock(return_value=source), scalars=AsyncMock())
        with (
            patch("src.domain.issues.staff_suggest._house", new=AsyncMock(return_value=SimpleNamespace(id=house_id))),
            patch("src.domain.issues.staff_suggest._staff_assignment", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(IssueError) as caught:
                await suggest_staff_merges(session, SimpleNamespace(kind="employee"), source.id)
        self.assertEqual(caught.exception.status_code, 404)
        session.scalars.assert_not_awaited()

    async def test_provider_can_suggest_private_card_for_employee_but_does_not_merge(self) -> None:
        house_id = UUID(int=20)
        source = card(1, house_id)
        other_apartment = card(2, house_id, title="В квартире холодные радиаторы")
        session = SimpleNamespace(
            get=AsyncMock(return_value=source),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [other_apartment])),
        )
        with (
            patch("src.domain.issues.staff_suggest._house", new=AsyncMock(return_value=SimpleNamespace(id=house_id))),
            patch("src.domain.issues.staff_suggest._staff_assignment", new=AsyncMock(return_value=SimpleNamespace(can_manage_issues=True))),
            patch("src.domain.issues.staff_suggest.gigachat_suggestion", new_callable=AsyncMock) as provider,
            patch.dict("os.environ", {"ISSUE_AI_PROVIDER": "gigachat", "GIGACHAT_AUTH_KEY": "secret", "GIGACHAT_SEND_REAL_DATA": "1"}, clear=True),
        ):
            provider.return_value = [other_apartment.id]
            result = await suggest_staff_merges(session, SimpleNamespace(kind="employee"), source.id)
        self.assertEqual(result.similar_card_ids, [other_apartment.id])
        self.assertEqual(result.source, "gigachat")
        sent = json.loads(provider.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(sent["candidates"][0]["id"], str(other_apartment.id))
        self.assertEqual(source.merged_into_id, None)
        self.assertEqual(other_apartment.merged_into_id, None)

    async def test_provider_disabled_result_is_labeled_local(self) -> None:
        house_id = UUID(int=20)
        source = card(1, house_id)
        other_apartment = card(2, house_id)
        session = SimpleNamespace(
            get=AsyncMock(return_value=source),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [other_apartment])),
        )
        with (
            patch("src.domain.issues.staff_suggest._house", new=AsyncMock(return_value=SimpleNamespace(id=house_id))),
            patch("src.domain.issues.staff_suggest._staff_assignment", new=AsyncMock(return_value=SimpleNamespace(can_manage_issues=True))),
            patch("src.domain.issues.staff_suggest.gigachat_suggestion", new_callable=AsyncMock) as provider,
            patch.dict("os.environ", {"ISSUE_AI_PROVIDER": "gigachat"}, clear=True),
        ):
            result = await suggest_staff_merges(session, SimpleNamespace(kind="employee"), source.id)
        provider.assert_not_awaited()
        self.assertEqual(result.source, "local")
        self.assertEqual(result.similar_card_ids, [other_apartment.id])


if __name__ == "__main__":
    unittest.main()
