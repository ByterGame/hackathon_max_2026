"""Pure access and lifecycle checks for shared issue cards."""

from datetime import datetime
from uuid import UUID


ACTIVE_STATUSES = frozenset({"open", "reviewing", "needs_info", "in_progress"})
ALL_STATUSES = ACTIVE_STATUSES | {"closed"}
CLOSE_RESULTS = frozenset({"solved", "invalid"})


def can_view_issue(
    *,
    has_house_access: bool,
    is_author: bool,
    scope_all_house: bool,
    granted_apartment_ids: set[UUID],
    granted_entrance_numbers: set[int],
    target_apartment_ids: set[UUID],
    target_entrance_numbers: set[int],
) -> bool:
    """Check resident visibility using current grants and current card scope."""
    if not has_house_access:
        return False
    if scope_all_house or is_author:
        return True
    return bool(
        granted_apartment_ids & target_apartment_ids
        or granted_entrance_numbers & target_entrance_numbers
    )


def can_merge_issues(
    *,
    left_id: UUID,
    right_id: UUID,
    left_house_id: UUID,
    right_house_id: UUID,
    left_status: str,
    right_status: str,
    left_merged: bool,
    right_merged: bool,
) -> bool:
    return (
        left_id != right_id
        and left_house_id == right_house_id
        and left_status in ACTIVE_STATUSES
        and right_status in ACTIVE_STATUSES
        and not left_merged
        and not right_merged
    )


def primary_issue_id(
    left_id: UUID,
    left_created_at: datetime,
    right_id: UUID,
    right_created_at: datetime,
) -> UUID:
    """Choose the earliest original card, using UUID as a stable tie breaker."""
    return left_id if (left_created_at, left_id) <= (right_created_at, right_id) else right_id
