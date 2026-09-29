"""Guarded, schema-driven editor for exceptional administrator corrections.

This is intentionally separate from ordinary domain operations. Every change
requires a reason, a current row fingerprint, and a journal entry.
"""

import asyncio
import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AdminOperation,
    AuditEvent,
    Base,
    BotDialog,
    CommandReceipt,
    File,
    Notification,
    OutboxEvent,
    ResidentGrant,
    StaffAssignment,
    User,
)
from src.domain.access.rules import AccessRuleError, normalize_phone
from src.domain.admin.service import require_admin
from src.domain.files.storage import FileError, path_for_key, storage_root


MAX_LIST_LIMIT = 100
MAX_LIST_OFFSET = 10_000
MAX_REASON_LENGTH = 2000
MAX_TEXT_LENGTH = 250_000
MAX_CASCADE_ROWS = 2_000
LAST_ADMIN_LOCK_KEY = 734_233_817
SYSTEM_EDITOR_LOCK_KEY = 734_233_818
PROTECTED_FILE_FIELDS = {"storage_key", "size_bytes", "sha256"}
SOFT_ENTITIES = {
    "housing.companies",
    "housing.houses",
    "issues.categories",
    "identity.staff_assignments",
    "identity.support_invitations",
    "access.resident_grants",
    "access.resident_offers",
    "system.files",
    "system.bot_mutes",
}

# Only pairs actually used by domain events may create a polymorphic edge.
# Matching by UUID alone would delete unrelated records with the same value.
POLYMORPHIC_KINDS = {
    "identity.users": "user",
    "identity.staff_assignments": "staff_assignment",
    "identity.support_invitations": "support_invitation",
    "housing.companies": "company",
    "housing.houses": "house",
    "access.company_registration_requests": "company_registration_request",
    "access.house_addition_requests": "house_addition_request",
    "access.resident_requests": "resident_request",
    "access.resident_offers": "resident_offer",
    "access.resident_grants": "resident_grant",
    "issues.cards": "issue_card",
    "system.files": "file",
}

NodeKey = tuple[str, str]


@dataclass
class CascadeNode:
    entity: str
    row_id: str
    row: Any
    via: list[dict[str, str]] = field(default_factory=list)
    children: set[NodeKey] = field(default_factory=set)


@dataclass
class CascadePlan:
    nodes: dict[NodeKey, CascadeNode]
    delete_order: list[NodeKey]
    preview: dict[str, Any]
    role_updates: list[tuple[User, str]]
    private_files: list[dict[str, str]]


def _registry() -> dict[str, type[Base]]:
    result: dict[str, type[Base]] = {}
    for mapper in Base.registry.mappers:
        model = mapper.class_
        table = model.__table__
        result[f"{table.schema}.{table.name}"] = model
    return dict(sorted(result.items()))


def _model(entity: str) -> type[Base]:
    model = _registry().get(entity)
    if model is None:
        raise AccessRuleError(400, "invalid_entity", "Таблица недоступна системному редактору")
    return model


def _key_columns(model: type[Base]) -> list[Any]:
    return list(model.__table__.primary_key.columns)


def _parse_row_key(model: type[Base], row_id: str) -> tuple[UUID, ...]:
    columns = _key_columns(model)
    parts = row_id.split(":")
    if len(parts) != len(columns):
        raise AccessRuleError(400, "invalid_row_id", "Некорректный ключ записи")
    try:
        return tuple(UUID(part) for part in parts)
    except (TypeError, ValueError) as error:
        raise AccessRuleError(400, "invalid_row_id", "Некорректный ключ записи") from error


def _row_id(row: Any) -> str:
    return ":".join(str(getattr(row, column.key)) for column in _key_columns(type(row)))


def _primitive(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def _row_data(row: Any) -> dict[str, Any]:
    data = {
        column.key: _primitive(getattr(row, column.key))
        for column in row.__table__.columns
        if not (isinstance(row, File) and column.key == "storage_key")
    }
    if isinstance(row, AdminOperation) and isinstance(data.get("after_data"), dict):
        after = dict(data["after_data"])
        cleanup = after.get("_file_cleanup")
        if isinstance(cleanup, dict):
            safe_cleanup = dict(cleanup)
            safe_cleanup["pending"] = [
                {"id": item.get("id")}
                for item in cleanup.get("pending", [])
                if isinstance(item, dict)
            ]
            after["_file_cleanup"] = safe_cleanup
            data["after_data"] = after
    return data


def _etag(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _item(row: Any) -> dict[str, Any]:
    data = _row_data(row)
    return {"id": _row_id(row), "etag": _etag(data), "data": data}


def _field_type(column: Any) -> str:
    if isinstance(column.type, Uuid):
        return "uuid"
    if isinstance(column.type, DateTime):
        return "datetime"
    if isinstance(column.type, Boolean):
        return "bool"
    if isinstance(column.type, Integer):
        return "int"
    if isinstance(column.type, JSONB):
        return "json"
    if isinstance(column.type, (Text, String)):
        return "text"
    raise RuntimeError(f"Unsupported system-editor column type: {column.type!r}")


def _editable(model: type[Base], column: Any) -> bool:
    if column.primary_key:
        return False
    if model is File and column.key in PROTECTED_FILE_FIELDS:
        return False
    return True


def _delete_modes(entity: str) -> list[str]:
    if entity in SOFT_ENTITIES:
        return ["soft", "hard"]
    return ["hard"]


def _schema_entry(entity: str, model: type[Base]) -> dict[str, Any]:
    return {
        "key": entity,
        "label": entity,
        "key_fields": [column.key for column in _key_columns(model)],
        "delete_mode": "soft" if entity in SOFT_ENTITIES else "hard",
        "delete_modes": _delete_modes(entity),
        "fields": [
            {
                "name": column.key,
                "type": _field_type(column),
                "nullable": column.nullable,
                "editable": _editable(model, column),
                "clear_only": model is User and column.key == "phone_verified_at",
                "foreign_key": (
                    next(iter(column.foreign_keys)).target_fullname
                    if column.foreign_keys else None
                ),
            }
            for column in model.__table__.columns
        ],
    }


async def system_schema(session: AsyncSession, actor: User) -> dict[str, Any]:
    await require_admin(session, actor)
    return {
        "entities": [
            _schema_entry(entity, model) for entity, model in _registry().items()
        ]
    }


def _search_condition(model: type[Base], q: str) -> Any:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    term = f"%{escaped}%"
    columns = [
        column for column in model.__table__.columns
        if isinstance(column.type, (Text, String)) or column.primary_key
    ]
    return or_(*(cast(column, Text).ilike(term, escape="\\") for column in columns))


async def system_list(
    session: AsyncSession,
    actor: User,
    entity: str,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    await require_admin(session, actor)
    model = _model(entity)
    if limit < 1 or limit > MAX_LIST_LIMIT or offset < 0 or offset > MAX_LIST_OFFSET:
        raise AccessRuleError(400, "invalid_page", "Недопустимая страница")
    query = q.strip() if q else ""
    if len(query) > 100:
        raise AccessRuleError(400, "query_too_long", "Поисковый запрос слишком длинный")
    condition = _search_condition(model, query) if query else None
    count_query = select(func.count()).select_from(model)
    rows_query = select(model)
    if condition is not None:
        count_query = count_query.where(condition)
        rows_query = rows_query.where(condition)
    total = int(await session.scalar(count_query) or 0)
    key_columns = _key_columns(model)
    rows = (await session.scalars(rows_query.order_by(*(column.desc() for column in key_columns)).limit(limit).offset(offset))).all()
    return {
        "items": [_item(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def _load_row(
    session: AsyncSession, model: type[Base], row_id: str, *, for_update: bool
) -> Any:
    values = _parse_row_key(model, row_id)
    statement = select(model).where(
        and_(*(column == value for column, value in zip(_key_columns(model), values)))
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(statement)
    if row is None:
        raise AccessRuleError(404, "not_found", "Запись не найдена")
    return row


async def _dependencies(session: AsyncSession, row: Any) -> list[dict[str, Any]]:
    target = row.__table__
    matches: list[dict[str, Any]] = []
    for entity, model in _registry().items():
        table = model.__table__
        for constraint in table.foreign_key_constraints:
            if constraint.referred_table is not target:
                continue
            predicates = [
                element.parent == getattr(row, element.column.key)
                for element in constraint.elements
            ]
            count = int(
                await session.scalar(
                    select(func.count()).select_from(table).where(and_(*predicates))
                ) or 0
            )
            if count:
                matches.append(
                    {
                        "entity": entity,
                        "field": ",".join(element.parent.key for element in constraint.elements),
                        "count": count,
                    }
                )
    return matches


async def _related_rows(
    session: AsyncSession,
    node: CascadeNode,
    incoming: dict[Any, list[tuple[str, type[Base], Any]]],
    *,
    for_update: bool,
) -> list[tuple[str, Any, str, str]]:
    """Read only children of this exact row; never select an entire table."""
    related: list[tuple[str, Any, str, str]] = []
    for child_entity, child_model, constraint in incoming.get(node.row.__table__, []):
        predicates = [
            element.parent == getattr(node.row, element.column.key)
            for element in constraint.elements
        ]
        statement = select(child_model).where(and_(*predicates))
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        rows = (await session.scalars(statement)).all()
        field_name = ",".join(element.parent.key for element in constraint.elements)
        related.extend((child_entity, row, "foreign_key", field_name) for row in rows)

    kind = POLYMORPHIC_KINDS.get(node.entity)
    if kind is not None:
        subject_id = UUID(node.row_id)
        for child_entity, model, kind_column, id_column, field_name in (
            ("system.audit_events", AuditEvent, AuditEvent.entity_kind, AuditEvent.entity_id, "entity_kind/entity_id"),
            ("system.outbox_events", OutboxEvent, OutboxEvent.subject_kind, OutboxEvent.subject_id, "subject_kind/subject_id"),
            ("system.notifications", Notification, Notification.subject_kind, Notification.subject_id, "subject_kind/subject_id"),
            ("system.command_receipts", CommandReceipt, CommandReceipt.result_kind, CommandReceipt.result_id, "result_kind/result_id"),
        ):
            statement = select(model).where(kind_column == kind, id_column == subject_id)
            if for_update:
                statement = statement.with_for_update().execution_options(populate_existing=True)
            related.extend(
                (child_entity, row, "polymorphic", field_name)
                for row in (await session.scalars(statement)).all()
            )

    # Domain events store the initiating user in a documented JSON field;
    # unlike incidental IDs in free-form JSON, this is a typed relationship
    # used by the notification worker and must not survive deletion of a user.
    if node.entity == "identity.users":
        statement = select(OutboxEvent).where(
            OutboxEvent.payload["actor_user_id"].astext == node.row_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        related.extend(
            ("system.outbox_events", row, "polymorphic", "payload.actor_user_id")
            for row in (await session.scalars(statement)).all()
        )

    # The journal uses a textual entity/row key instead of a database FK.
    statement = select(AdminOperation).where(
        AdminOperation.entity_key == node.entity,
        AdminOperation.row_key == node.row_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    related.extend(
        ("system.admin_operations", row, "polymorphic", "entity_key/row_key")
        for row in (await session.scalars(statement)).all()
    )
    return related


async def _planned_role_updates(
    session: AsyncSession,
    nodes: dict[NodeKey, CascadeNode],
    *,
    for_update: bool,
) -> list[tuple[User, str]]:
    """Keep surviving users' roles consistent after deleting their grants."""
    staff_ids = [node.row.id for node in nodes.values() if isinstance(node.row, StaffAssignment)]
    grant_ids = [node.row.id for node in nodes.values() if isinstance(node.row, ResidentGrant)]
    affected: set[UUID] = {
        node.row.user_id
        for node in nodes.values()
        if isinstance(node.row, (StaffAssignment, ResidentGrant)) and node.row.user_id is not None
    }
    unclaimed_staff_phones = {
        node.row.phone_number
        for node in nodes.values()
        if isinstance(node.row, StaffAssignment) and node.row.user_id is None
    }
    if unclaimed_staff_phones:
        statement = select(User).where(
            User.phone_number.in_(unclaimed_staff_phones),
            User.phone_verified_at.is_not(None),
            User.kind == "employee",
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        affected.update(user.id for user in (await session.scalars(statement)).all())
    updates: list[tuple[User, str]] = []
    for user_id in sorted(affected):
        if ("identity.users", str(user_id)) in nodes:
            continue
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        user = await session.scalar(statement)
        if user is None:
            continue
        if user.kind == "employee":
            identities = [StaffAssignment.user_id == user.id]
            if user.phone_number and user.phone_verified_at is not None:
                identities.append(
                    and_(
                        StaffAssignment.user_id.is_(None),
                        StaffAssignment.phone_number == normalize_phone(user.phone_number),
                    )
                )
            remaining = await session.scalar(
                select(StaffAssignment.id).where(
                    StaffAssignment.revoked_at.is_(None),
                    StaffAssignment.id.not_in(staff_ids),
                    or_(*identities),
                ).limit(1)
            )
            if remaining is None:
                updates.append((user, "unassigned"))
        elif user.kind == "resident":
            now = datetime.now(timezone.utc)
            remaining = await session.scalar(
                select(ResidentGrant.id).where(
                    ResidentGrant.user_id == user.id,
                    ResidentGrant.id.not_in(grant_ids),
                    ResidentGrant.revoked_at.is_(None),
                    ResidentGrant.valid_from <= now,
                    or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
                ).limit(1)
            )
            if remaining is None:
                updates.append((user, "unassigned"))
    return updates


def _delete_order(nodes: dict[NodeKey, CascadeNode], root: NodeKey) -> tuple[list[NodeKey], bool]:
    order: list[NodeKey] = []
    active: set[NodeKey] = {root}
    finished: set[NodeKey] = set()
    cycle = False
    stack = [(root, iter(sorted(nodes[root].children)))]
    while stack:
        key, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            active.remove(key)
            finished.add(key)
            order.append(key)
            continue
        if child in active:
            cycle = True
        elif child not in finished:
            active.add(child)
            stack.append((child, iter(sorted(nodes[child].children))))
    return order, cycle


async def _cascade_plan(
    session: AsyncSession,
    actor: User,
    entity: str,
    root_row: Any,
    *,
    for_update: bool,
) -> CascadePlan:
    registry = _registry()
    tables = {model.__table__: (key, model) for key, model in registry.items()}
    incoming: dict[Any, list[tuple[str, type[Base], Any]]] = defaultdict(list)
    for child_entity, child_model in registry.items():
        for constraint in child_model.__table__.foreign_key_constraints:
            if constraint.referred_table in tables:
                incoming[constraint.referred_table].append((child_entity, child_model, constraint))

    root_key = (entity, _row_id(root_row))
    nodes = {root_key: CascadeNode(entity, root_key[1], root_row)}
    queue = deque([root_key])
    while queue:
        parent_key = queue.popleft()
        parent = nodes[parent_key]
        for child_entity, child_row, link_kind, field_name in await _related_rows(
            session, parent, incoming, for_update=for_update
        ):
            child_key = (child_entity, _row_id(child_row))
            if child_key not in nodes:
                if len(nodes) >= MAX_CASCADE_ROWS:
                    raise AccessRuleError(
                        413, "cascade_too_large",
                        f"Удаление затрагивает более {MAX_CASCADE_ROWS} записей; автоматический каскад недоступен",
                    )
                nodes[child_key] = CascadeNode(child_entity, child_key[1], child_row)
                queue.append(child_key)
            parent.children.add(child_key)
            via = {
                "kind": link_kind,
                "from_entity": parent.entity,
                "from_id": parent.row_id,
                "field": field_name,
            }
            if via not in nodes[child_key].via:
                nodes[child_key].via.append(via)

    order, has_cycle = _delete_order(nodes, root_key)
    blockers: list[str] = []
    if has_cycle:
        blockers.append("Обнаружен цикл связей; автоматическое удаление этих записей недоступно")
    if ("identity.users", str(actor.id)) in nodes:
        blockers.append("Нельзя удалить собственный аккаунт администратора")
    planned_admins = sum(
        isinstance(node.row, User) and node.row.kind == "admin"
        for node in nodes.values()
    )
    if planned_admins:
        total_admins = int(
            await session.scalar(select(func.count()).select_from(User).where(User.kind == "admin")) or 0
        )
        if total_admins <= planned_admins:
            blockers.append("Нельзя удалить последнего администратора")
    for node in nodes.values():
        if isinstance(node.row, AdminOperation):
            cleanup = (node.row.after_data or {}).get("_file_cleanup")
            if isinstance(cleanup, dict) and cleanup.get("pending"):
                blockers.append("Сначала завершите очистку файлов по связанному действию журнала")
                break

    role_updates = await _planned_role_updates(session, nodes, for_update=for_update)
    counts_map: dict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    fingerprint_rows: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    private_files: list[dict[str, str]] = []
    for key in sorted(nodes):
        node = nodes[key]
        counts_map[node.entity] += 1
        via = sorted(node.via, key=lambda link: (link["kind"], link["from_entity"], link["from_id"], link["field"]))
        rows.append({"entity": node.entity, "id": node.row_id, "via": via})
        fingerprint = {"entity": node.entity, "id": node.row_id, "etag": _item(node.row)["etag"], "via": via}
        if isinstance(node.row, File):
            fingerprint["file_key_hash"] = hashlib.sha256(node.row.storage_key.encode()).hexdigest()
            files.append({
                "id": node.row_id,
                "original_name": node.row.original_name,
                "size_bytes": node.row.size_bytes,
            })
            private_files.append({"id": node.row_id, "key": node.row.storage_key})
        fingerprint_rows.append(fingerprint)
    updates = [
        {"entity": "identity.users", "id": str(user.id), "field": "kind", "before": user.kind, "after": next_kind}
        for user, next_kind in role_updates
    ]
    update_fingerprints = [
        {**update, "etag": _item(user)["etag"]}
        for update, (user, _) in zip(updates, role_updates)
    ]
    fingerprint = json.dumps(
        {"rows": fingerprint_rows, "updates": update_fingerprints},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    preview = {
        "hash": hashlib.sha256(fingerprint).hexdigest(),
        "total": len(nodes),
        "rows": rows,
        "counts": [{"entity": key, "count": counts_map[key]} for key in sorted(counts_map)],
        "files": files,
        "updates": updates,
        "blockers": blockers,
    }
    return CascadePlan(nodes, order, preview, role_updates, private_files)


async def system_get(
    session: AsyncSession, actor: User, entity: str, row_id: str
) -> dict[str, Any]:
    await require_admin(session, actor)
    model = _model(entity)
    row = await _load_row(session, model, row_id, for_update=False)
    try:
        plan = await _cascade_plan(session, actor, entity, row, for_update=False)
    except AccessRuleError as error:
        if error.code != "cascade_too_large":
            raise
        preview = {
            "hash": "", "total": 0, "rows": [], "counts": [], "files": [],
            "updates": [], "blockers": [error.message],
        }
    else:
        preview = plan.preview
    return {
        "item": _item(row),
        "dependencies": await _dependencies(session, row),
        "delete_mode": "soft" if entity in SOFT_ENTITIES else "hard",
        "delete_modes": _delete_modes(entity),
        "cascade_preview": preview,
    }


async def system_operations(
    session: AsyncSession,
    actor: User,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Return journal entries in reverse chronological order."""
    await require_admin(session, actor)
    if limit < 1 or limit > MAX_LIST_LIMIT or offset < 0 or offset > MAX_LIST_OFFSET:
        raise AccessRuleError(400, "invalid_page", "Недопустимая страница")
    query = q.strip() if q else ""
    if len(query) > 100:
        raise AccessRuleError(400, "query_too_long", "Поисковый запрос слишком длинный")
    condition = None
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        condition = or_(
            AdminOperation.entity_key.ilike(term, escape="\\"),
            AdminOperation.row_key.ilike(term, escape="\\"),
            AdminOperation.reason.ilike(term, escape="\\"),
            cast(AdminOperation.id, Text).ilike(term, escape="\\"),
        )
    count_query = select(func.count()).select_from(AdminOperation)
    rows_query = select(AdminOperation)
    if condition is not None:
        count_query = count_query.where(condition)
        rows_query = rows_query.where(condition)
    total = int(await session.scalar(count_query) or 0)
    rows = (
        await session.scalars(
            rows_query.order_by(AdminOperation.created_at.desc(), AdminOperation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [_row_data(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _reason(value: str) -> str:
    if not isinstance(value, str):
        raise AccessRuleError(400, "reason_required", "Укажите причину изменения")
    cleaned = value.strip()
    if len(cleaned) < 5 or len(cleaned) > MAX_REASON_LENGTH:
        raise AccessRuleError(400, "reason_required", "Причина должна содержать 5–2000 символов")
    return cleaned


def _check_etag(row: Any, expected_etag: str) -> dict[str, Any]:
    before = _row_data(row)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_etag or "") or _etag(before) != expected_etag:
        raise AccessRuleError(409, "stale_record", "Запись изменилась; обновите её перед сохранением")
    return before


def _parse_value(column: Any, value: Any) -> Any:
    if value is None:
        if not column.nullable:
            raise AccessRuleError(400, "invalid_field", f"Поле {column.key} не может быть пустым")
        return None
    kind = _field_type(column)
    try:
        if kind == "uuid":
            if not isinstance(value, str):
                raise ValueError
            return UUID(value)
        if kind == "datetime":
            if not isinstance(value, str):
                raise ValueError
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError
            return value
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError
            return value
        if kind == "text":
            if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH:
                raise ValueError
            return value
        if kind == "json":
            json.dumps(value)
            if column.key == "discussion" and not (
                isinstance(value, list) and all(isinstance(item, dict) for item in value)
            ):
                raise ValueError
            if column.key in {"payload", "data", "before_data", "after_data"} and not isinstance(value, dict):
                raise ValueError
            return value
    except (TypeError, ValueError) as error:
        raise AccessRuleError(400, "invalid_field", f"Некорректный тип поля {column.key}") from error
    raise AccessRuleError(400, "invalid_field", f"Поле {column.key} не поддерживается")


async def _protect_last_admin(session: AsyncSession, row: User, next_kind: str | None) -> None:
    if row.kind != "admin" or next_kind == "admin":
        return
    count = int(await session.scalar(select(func.count()).select_from(User).where(User.kind == "admin")) or 0)
    if count <= 1:
        raise AccessRuleError(409, "last_admin", "Нельзя лишить доступа последнего администратора")


async def _lock_admin_rows(session: AsyncSession) -> None:
    # The ordinary revoke_admin action locks current administrators in this
    # order. Match it before locking the actor/target to avoid a deadlock and
    # make the last-admin check safe across both mutation paths.
    (await session.scalars(
        select(User)
        .where(User.kind == "admin")
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).all()


async def _active_entitlements(session: AsyncSession, user: User) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    staff = int(
        await session.scalar(
            select(func.count()).select_from(StaffAssignment).where(
                StaffAssignment.user_id == user.id,
                StaffAssignment.revoked_at.is_(None),
            )
        ) or 0
    )
    grants = int(
        await session.scalar(
            select(func.count()).select_from(ResidentGrant).where(
                ResidentGrant.user_id == user.id,
                ResidentGrant.revoked_at.is_(None),
                ResidentGrant.valid_from <= now,
                or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
            )
        ) or 0
    )
    return staff, grants


async def _validate_user_change(
    session: AsyncSession, row: User, values: dict[str, Any], *, actor_id: UUID
) -> dict[str, Any]:
    changed_max = "max_user_id" in values and values["max_user_id"] != row.max_user_id
    changed_phone = "phone_number" in values and values["phone_number"] != row.phone_number
    if "max_user_id" in values and values["max_user_id"] is not None:
        if re.fullmatch(r"[1-9][0-9]*", values["max_user_id"]) is None:
            raise AccessRuleError(400, "invalid_max_id", "MAX ID должен быть положительным числом")
    if "phone_number" in values and values["phone_number"] is not None:
        values["phone_number"] = normalize_phone(values["phone_number"])
        changed_phone = values["phone_number"] != row.phone_number
    if changed_max and row.id == actor_id:
        raise AccessRuleError(
            409,
            "self_identity_change",
            "Нельзя менять собственный MAX ID: это может заблокировать вход администратора",
        )
    if "phone_verified_at" in values and values["phone_verified_at"] is not None:
        raise AccessRuleError(400, "verification_forbidden", "Подтвердить номер может только MAX; здесь можно лишь сбросить подтверждение")
    next_kind = values.get("kind", row.kind)
    if next_kind not in {"unassigned", "resident", "employee", "support", "admin"}:
        raise AccessRuleError(400, "invalid_role", "Неизвестная роль")
    if next_kind == "admin" and not values.get("max_user_id", row.max_user_id):
        raise AccessRuleError(409, "admin_login_required", "Администратору необходим MAX ID для входа")
    await _protect_last_admin(session, row, next_kind)
    staff, grants = await _active_entitlements(session, row)
    if (changed_max or changed_phone) and (staff or grants):
        raise AccessRuleError(
            409,
            "active_access",
            "Сначала отзовите действующие назначения сотрудника и доступы жильца",
        )
    if "phone_verified_at" in values and values["phone_verified_at"] is None and (staff or grants):
        raise AccessRuleError(
            409,
            "active_access",
            "Сначала отзовите действующие назначения сотрудника и доступы жильца",
        )
    if "kind" in values and next_kind != row.kind:
        if next_kind != "employee" and staff:
            raise AccessRuleError(409, "active_staff", "Сначала отзовите назначения сотрудника")
        if next_kind != "resident" and grants:
            raise AccessRuleError(409, "active_resident", "Сначала отзовите доступы жильца")
        if next_kind == "employee" and not staff:
            raise AccessRuleError(409, "staff_assignment_required", "Для роли сотрудника нужно действующее назначение")
        if next_kind == "resident" and not grants:
            raise AccessRuleError(409, "resident_grant_required", "Для роли жильца нужен действующий доступ")
        if next_kind in {"employee", "resident"} and (
            row.phone_number is None or row.phone_verified_at is None
        ):
            raise AccessRuleError(409, "phone_required", "Для этой роли нужен подтверждённый номер")
    if changed_max:
        values["phone_number"] = None
        values["phone_verified_at"] = None
    elif changed_phone:
        values["phone_verified_at"] = None
    return values


async def _validate_staff_change(session: AsyncSession, row: StaffAssignment) -> None:
    row.phone_number = normalize_phone(row.phone_number)
    if row.user_id is None or row.revoked_at is not None:
        return
    user = await session.get(User, row.user_id)
    if user is None or user.kind != "employee" or user.phone_verified_at is None or user.phone_number != row.phone_number:
        raise AccessRuleError(409, "staff_identity_conflict", "Сотрудник должен иметь роль и подтверждённый номер назначения")


async def _reconcile_staff_role(
    session: AsyncSession,
    row: StaffAssignment,
    *,
    user_id: UUID | None = None,
    force_inactive: bool = False,
) -> dict[str, Any] | None:
    selected_user_id = user_id if user_id is not None else row.user_id
    if selected_user_id is None:
        return None
    user = await session.scalar(
        select(User).where(User.id == selected_user_id).with_for_update().execution_options(populate_existing=True)
    )
    if user is None:
        raise AccessRuleError(409, "user_not_found", "Сотрудник не найден")
    before_kind = user.kind
    if row.revoked_at is None and not force_inactive:
        if user.phone_number != normalize_phone(row.phone_number) or user.phone_verified_at is None:
            raise AccessRuleError(409, "staff_identity_conflict", "Номер сотрудника должен быть подтверждён через MAX")
        if user.kind == "unassigned":
            _, active_grants = await _active_entitlements(session, user)
            if active_grants:
                raise AccessRuleError(409, "role_conflict", "У человека уже есть доступ жильца")
            user.kind = "employee"
        elif user.kind != "employee":
            raise AccessRuleError(409, "role_conflict", "У сотрудника уже есть другая роль")
    elif user.kind == "employee":
        identities = [StaffAssignment.user_id == user.id]
        if user.phone_number and user.phone_verified_at is not None:
            identities.append(
                and_(
                    StaffAssignment.user_id.is_(None),
                    StaffAssignment.phone_number == normalize_phone(user.phone_number),
                )
            )
        other = await session.scalar(
            select(StaffAssignment.id).where(
                StaffAssignment.id != row.id,
                StaffAssignment.revoked_at.is_(None),
                or_(*identities),
            )
        )
        if other is None:
            user.kind = "unassigned"
    if user.kind == before_kind:
        return None
    user.version += 1
    user.updated_at = datetime.now(timezone.utc)
    return {"user_id": str(user.id), "before_kind": before_kind, "after_kind": user.kind}


async def _reconcile_resident_role(
    session: AsyncSession,
    row: ResidentGrant,
    *,
    user_id: UUID | None = None,
    force_inactive: bool = False,
) -> dict[str, Any] | None:
    selected_user_id = user_id if user_id is not None else row.user_id
    user = await session.scalar(
        select(User).where(User.id == selected_user_id).with_for_update().execution_options(populate_existing=True)
    )
    if user is None:
        raise AccessRuleError(409, "user_not_found", "Житель не найден")
    before_kind = user.kind
    now = datetime.now(timezone.utc)
    current_active = (
        row.revoked_at is None
        and not force_inactive
        and row.valid_from <= now
        and (row.valid_to is None or row.valid_to > now)
    )
    if current_active:
        if user.phone_verified_at is None:
            raise AccessRuleError(409, "phone_required", "Номер жителя должен быть подтверждён через MAX")
        if user.kind == "unassigned":
            active_staff, _ = await _active_entitlements(session, user)
            if active_staff:
                raise AccessRuleError(409, "role_conflict", "У человека уже есть назначение сотрудника")
            user.kind = "resident"
        elif user.kind != "resident":
            raise AccessRuleError(409, "role_conflict", "У жителя уже есть другая роль")
    elif user.kind == "resident":
        other = await session.scalar(
            select(ResidentGrant.id).where(
                ResidentGrant.id != row.id,
                ResidentGrant.user_id == user.id,
                ResidentGrant.revoked_at.is_(None),
                ResidentGrant.valid_from <= now,
                or_(ResidentGrant.valid_to.is_(None), ResidentGrant.valid_to > now),
            )
        )
        if other is None:
            user.kind = "unassigned"
    if user.kind == before_kind:
        return None
    user.version += 1
    user.updated_at = now
    return {"user_id": str(user.id), "before_kind": before_kind, "after_kind": user.kind}


def _ledger(
    session: AsyncSession,
    *,
    entity: str,
    row_id: str,
    operation: str,
    actor: User,
    reason: str,
    expected_etag: str,
    before: dict[str, Any],
    after: dict[str, Any] | None,
) -> UUID:
    operation_id = uuid4()
    session.add(
        AdminOperation(
            id=operation_id,
            entity_key=entity,
            row_key=row_id,
            operation=operation,
            actor_user_id=actor.id,
            reason=reason,
            expected_etag=expected_etag,
            before_data=before,
            after_data=after,
        )
    )
    return operation_id


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise AccessRuleError(
            409, "constraint_conflict", "Изменение нарушает ограничения данных или внешние связи"
        ) from error


async def _allow_log_mutation(session: AsyncSession, model: type[Base]) -> None:
    """Open the database trigger only for this authorized transaction."""
    if model in {AuditEvent, AdminOperation}:
        await session.execute(
            select(func.set_config("app.system_editor_log_write", "on", True))
        )


async def _lock_cascade_tables(session: AsyncSession) -> None:
    """Freeze writers while comparing and applying a cross-table deletion plan.

    Row locks alone cannot prevent a new polymorphic reference appearing after
    its table was scanned. This exceptional operation therefore takes a short
    NOWAIT table lock. Plain reads remain available; active writers and row
    lockers yield a retryable conflict rather than an incomplete cascade.
    """
    names = []
    for model in _registry().values():
        table = model.__table__
        schema = table.schema.replace('"', '""')
        name = table.name.replace('"', '""')
        names.append(f'"{schema}"."{name}"')
    try:
        await session.execute(text(f"LOCK TABLE {', '.join(sorted(names))} IN EXCLUSIVE MODE NOWAIT"))
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        await session.rollback()
        raise AccessRuleError(
            409, "cascade_busy", "Данные сейчас меняются; повторите удаление через несколько секунд"
        ) from error


async def system_patch(
    session: AsyncSession,
    actor: User,
    entity: str,
    row_id: str,
    expected_etag: str,
    reason: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    model = _model(entity)
    reason = _reason(reason)
    if not isinstance(changes, dict) or not changes or len(changes) > len(model.__table__.columns):
        raise AccessRuleError(400, "invalid_changes", "Укажите изменяемые поля")
    await session.execute(select(func.pg_advisory_xact_lock(SYSTEM_EDITOR_LOCK_KEY)))
    if model is User:
        await session.execute(select(func.pg_advisory_xact_lock(LAST_ADMIN_LOCK_KEY)))
        await _lock_admin_rows(session)
    admin = await require_admin(session, actor)
    await _allow_log_mutation(session, model)
    row = await _load_row(session, model, row_id, for_update=True)
    before = _check_etag(row, expected_etag)
    if model is AdminOperation:
        cleanup = (row.after_data or {}).get("_file_cleanup")
        if isinstance(cleanup, dict) and cleanup.get("pending"):
            raise AccessRuleError(
                409, "cleanup_pending", "Сначала завершите очистку файлов по этой операции"
            )
    columns = {column.key: column for column in model.__table__.columns}
    values: dict[str, Any] = {}
    for field, value in changes.items():
        column = columns.get(field)
        if column is None or not _editable(model, column):
            raise AccessRuleError(400, "field_protected", f"Поле {field} нельзя менять")
        values[field] = _parse_value(column, value)
    if model is AdminOperation and "after_data" in values:
        current_after = row.after_data or {}
        next_after = values["after_data"] or {}
        if (
            ("_file_cleanup" in current_after) != ("_file_cleanup" in next_after)
            or current_after.get("_file_cleanup") != next_after.get("_file_cleanup")
        ):
            raise AccessRuleError(
                409, "cleanup_manifest_protected",
                "Служебный манифест очистки файлов нельзя менять через редактор",
            )
    if model is User:
        if "full_name" in values and "full_name_is_manual" not in values:
            values["full_name_is_manual"] = bool(values["full_name"])
        next_manual = values.get("full_name_is_manual", row.full_name_is_manual)
        if not next_manual and set(values) & {
            "full_name",
            "full_name_is_manual",
            "max_display_name",
            "max_username",
        }:
            max_name = values.get("max_display_name", row.max_display_name)
            max_username = values.get("max_username", row.max_username)
            values["full_name"] = max_name or (
                f"@{max_username}" if max_username else None
            )
        if "full_name_confirmed_at" not in values and (
            values.get("full_name", row.full_name) != row.full_name
            or values.get("full_name_is_manual", row.full_name_is_manual) != row.full_name_is_manual
        ):
            # An administrator may correct a name, but cannot silently confirm it for a resident.
            values["full_name_confirmed_at"] = None
        values = await _validate_user_change(session, row, values, actor_id=admin.id)
    if model is StaffAssignment and "user_id" in values and values["user_id"] is None and row.revoked_at is None:
        raise AccessRuleError(409, "staff_detach_forbidden", "Сначала отзовите назначение, затем меняйте привязку")
    for field, value in values.items():
        setattr(row, field, value)
    side_effects: list[dict[str, Any]] = []
    # Validate against the intended in-memory row before a SELECT can
    # autoflush it and turn a predictable constraint conflict into HTTP 500.
    with session.no_autoflush:
        if model is StaffAssignment and "user_id" in values and before["user_id"] is not None and before["user_id"] != str(row.user_id):
            previous_role = await _reconcile_staff_role(
                session, row, user_id=UUID(before["user_id"]), force_inactive=True
            )
            if previous_role is not None:
                side_effects.append(previous_role)
        if model is StaffAssignment and ("revoked_at" in values or "user_id" in values):
            changed_role = await _reconcile_staff_role(session, row)
            if changed_role is not None:
                side_effects.append(changed_role)
        if model is ResidentGrant and "user_id" in values and before["user_id"] != str(row.user_id):
            previous_role = await _reconcile_resident_role(
                session, row, user_id=UUID(before["user_id"]), force_inactive=True
            )
            if previous_role is not None:
                side_effects.append(previous_role)
        if model is ResidentGrant and ("revoked_at" in values or "valid_to" in values or "valid_from" in values or "user_id" in values):
            changed_role = await _reconcile_resident_role(session, row)
            if changed_role is not None:
                side_effects.append(changed_role)
        if model is StaffAssignment and set(values) & {"user_id", "phone_number", "revoked_at"}:
            await _validate_staff_change(session, row)
    if model is User:
        row.version += 1 if "version" not in values else 0
        if "max_user_id" in values and row.max_user_id != before["max_user_id"]:
            dialog = await session.get(BotDialog, row.id)
            if dialog is not None:
                await session.delete(dialog)
    elif hasattr(row, "version") and "version" not in values:
        row.version += 1
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise AccessRuleError(409, "constraint_conflict", "Значение нарушает ограничения данных") from error
    await session.refresh(row)
    item = _item(row)
    operation_id = _ledger(
        session,
        entity=entity,
        row_id=row_id,
        operation="patch",
        actor=admin,
        reason=reason,
        expected_etag=expected_etag,
        before=before,
        after=(item["data"] | {"_side_effects": side_effects}) if side_effects else item["data"],
    )
    await _commit(session)
    return {"item": item, "operation_id": str(operation_id)}


async def _soft_delete(session: AsyncSession, row: Any, entity: str, actor: User, reason: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    side_effects: list[dict[str, Any]] = []
    if entity in {"housing.companies", "housing.houses"}:
        if row.archived_at is not None:
            raise AccessRuleError(409, "already_disabled", "Запись уже архивирована")
        row.archived_at = now
    elif entity == "issues.categories":
        if not row.is_active:
            raise AccessRuleError(409, "already_disabled", "Категория уже отключена")
        row.is_active = False
    elif entity == "identity.staff_assignments":
        if row.revoked_at is not None:
            raise AccessRuleError(409, "already_disabled", "Назначение уже отозвано")
        row.revoked_at = now
        row.version += 1
        changed_role = await _reconcile_staff_role(session, row)
        if changed_role is not None:
            side_effects.append(changed_role)
    elif entity == "identity.support_invitations":
        if row.accepted_at is not None or row.revoked_at is not None:
            raise AccessRuleError(409, "invitation_closed", "Приглашение уже обработано")
        row.revoked_at = now
        row.revoked_by = actor.id
        row.version += 1
    elif entity == "access.resident_grants":
        if row.revoked_at is not None:
            raise AccessRuleError(409, "already_disabled", "Доступ уже отозван")
        row.revoked_at = now
        row.revoked_by = actor.id
        row.revoke_reason = reason
        if row.valid_to is None or row.valid_to > now:
            row.valid_to = now
        changed_role = await _reconcile_resident_role(session, row)
        if changed_role is not None:
            side_effects.append(changed_role)
    elif entity == "access.resident_offers":
        if row.status != "pending":
            raise AccessRuleError(409, "offer_closed", "Предложение уже обработано")
        row.status = "cancelled"
    elif entity == "system.files":
        if row.state == "rejected":
            raise AccessRuleError(409, "already_disabled", "Файл уже скрыт")
        row.state = "rejected"
    elif entity == "system.bot_mutes":
        if not row.is_muted:
            raise AccessRuleError(409, "already_disabled", "Уведомления уже включены")
        row.is_muted = False
    else:
        raise AccessRuleError(400, "soft_delete_unavailable", "Для таблицы нет обратимого отключения")
    return side_effects


def _preflight_private_files(files: list[dict[str, str]]) -> None:
    if not files:
        return
    try:
        root = storage_root()
        for item in files:
            path = path_for_key(root, item["key"])
            if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(root.resolve()):
                raise RuntimeError("unsafe file directory")
    except (RuntimeError, OSError, FileError) as error:
        raise AccessRuleError(
            503, "file_storage_unavailable",
            "Приватное хранилище файлов недоступно; удаление не начато",
        ) from error


async def cleanup_operation_files(
    session: AsyncSession, actor: User, operation_id: UUID
) -> dict[str, Any]:
    """Retryable post-commit cleanup; never accept a caller-supplied path/key."""
    await session.execute(select(func.pg_advisory_xact_lock(SYSTEM_EDITOR_LOCK_KEY)))
    await require_admin(session, actor)
    operation = await session.scalar(
        select(AdminOperation)
        .where(AdminOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None:
        raise AccessRuleError(404, "operation_not_found", "Операция не найдена")
    after = dict(operation.after_data or {})
    cleanup = after.get("_file_cleanup")
    if operation.operation != "hard_delete" or not isinstance(cleanup, dict):
        raise AccessRuleError(400, "cleanup_unavailable", "У операции нет файлов для очистки")
    pending = [item for item in cleanup.get("pending", []) if isinstance(item, dict)]
    if not pending:
        await _commit(session)
        return {
            "status": "done", "deleted": int(cleanup.get("deleted", 0)),
            "failed": 0, "failed_file_ids": [],
        }

    try:
        root = storage_root()
    except RuntimeError:
        root = None
    failed: list[dict[str, str]] = []
    removed = 0
    for item in pending:
        try:
            if root is None:
                raise OSError("storage unavailable")
            path = path_for_key(root, item["key"])
            if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(root.resolve()):
                raise OSError("unsafe storage directory")
            await asyncio.to_thread(path.unlink, missing_ok=True)
            removed += 1
        except (OSError, FileError, RuntimeError):
            failed.append(item)
    cleanup["pending"] = failed
    cleanup["status"] = "failed" if failed else "done"
    cleanup["deleted"] = int(cleanup.get("deleted", 0)) + removed
    cleanup["failed"] = len(failed)
    after["_file_cleanup"] = cleanup
    await _allow_log_mutation(session, AdminOperation)
    operation.after_data = after
    await _commit(session)
    return {
        "status": cleanup["status"], "deleted": cleanup["deleted"],
        "failed": cleanup["failed"],
        "failed_file_ids": [item["id"] for item in failed],
    }


async def system_delete(
    session: AsyncSession,
    actor: User,
    entity: str,
    row_id: str,
    expected_etag: str,
    reason: str,
    mode: str,
    expected_preview_hash: str | None = None,
) -> dict[str, Any]:
    model = _model(entity)
    reason = _reason(reason)
    if mode not in {"soft", "hard"}:
        raise AccessRuleError(400, "invalid_delete_mode", "Укажите soft или hard")
    if mode not in _delete_modes(entity):
        raise AccessRuleError(409, "delete_mode_unavailable", "Этот способ удаления недоступен для таблицы")
    await session.execute(select(func.pg_advisory_xact_lock(SYSTEM_EDITOR_LOCK_KEY)))
    if mode == "hard":
        await _lock_cascade_tables(session)
    if model is User:
        await session.execute(select(func.pg_advisory_xact_lock(LAST_ADMIN_LOCK_KEY)))
        await _lock_admin_rows(session)
    admin = await require_admin(session, actor)
    await _allow_log_mutation(session, model)
    row = await _load_row(session, model, row_id, for_update=True)
    before = _check_etag(row, expected_etag)
    if mode == "soft":
        with session.no_autoflush:
            side_effects = await _soft_delete(session, row, entity, admin, reason)
        try:
            await session.flush()
        except IntegrityError as error:
            await session.rollback()
            raise AccessRuleError(409, "constraint_conflict", "Изменение нарушает ограничения данных") from error
        await session.refresh(row)
        item = _item(row)
        operation_id = _ledger(
            session,
            entity=entity,
            row_id=row_id,
            operation="soft_delete",
            actor=admin,
            reason=reason,
            expected_etag=expected_etag,
            before=before,
            after=(item["data"] | {"_side_effects": side_effects}) if side_effects else item["data"],
        )
        await _commit(session)
        return {"item": item, "operation_id": str(operation_id), "mode": "soft"}
    if not expected_preview_hash or re.fullmatch(r"[0-9a-f]{64}", expected_preview_hash) is None:
        raise AccessRuleError(400, "preview_required", "Сначала откройте точный предпросмотр удаления")
    plan = await _cascade_plan(session, admin, entity, row, for_update=True)
    if plan.preview["blockers"]:
        raise AccessRuleError(409, "cascade_blocked", "; ".join(plan.preview["blockers"]))
    if plan.preview["hash"] != expected_preview_hash:
        raise AccessRuleError(409, "preview_stale", "Связанные записи изменились; обновите предпросмотр")
    _preflight_private_files(plan.private_files)
    # Both audit tables have a database guard; only this authorized transaction
    # may delete the exact rows listed in the plan.
    await _allow_log_mutation(session, AdminOperation)
    try:
        for key in plan.delete_order:
            await session.delete(plan.nodes[key].row)
            await session.flush()
        for affected_user, next_kind in plan.role_updates:
            affected_user.kind = next_kind
            affected_user.version += 1
            affected_user.updated_at = datetime.now(timezone.utc)
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise AccessRuleError(409, "preview_stale", "Связанные записи изменились; обновите предпросмотр") from error
    manifest = {
        "cascade": {
            "hash": plan.preview["hash"],
            "total": plan.preview["total"],
            "counts": plan.preview["counts"],
            "rows": plan.preview["rows"],
            "updates": plan.preview["updates"],
            "files": plan.preview["files"],
        },
        "_file_cleanup": {
            "status": "pending" if plan.private_files else "done",
            "pending": plan.private_files,
            "deleted": 0,
            "failed": 0,
        },
    }
    operation_id = _ledger(
        session,
        entity=entity,
        row_id=row_id,
        operation="hard_delete",
        actor=admin,
        reason=reason,
        expected_etag=expected_etag,
        before=before,
        after=manifest,
    )
    await _commit(session)
    if plan.private_files:
        try:
            cleanup_result = await cleanup_operation_files(session, admin, operation_id)
        except Exception:
            await session.rollback()
            cleanup_result = {
                "status": "pending", "deleted": 0, "failed": len(plan.private_files),
                "failed_file_ids": [item["id"] for item in plan.private_files],
            }
    else:
        cleanup_result = {"status": "done", "deleted": 0, "failed": 0, "failed_file_ids": []}
    return {
        "item": None, "operation_id": str(operation_id), "mode": "hard",
        "deleted": plan.preview["total"], "file_cleanup": cleanup_result,
    }
