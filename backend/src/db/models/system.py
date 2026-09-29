"""Файлы, черновики, аудит и надёжная доставка уведомлений."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("state IN ('staged', 'ready', 'rejected')", name="state_allowed"),
        CheckConstraint(
            "state <> 'staged' OR (draft_id IS NOT NULL AND issue_report_id IS NULL "
            "AND issue_message_id IS NULL AND company_registration_request_id IS NULL "
            "AND house_addition_request_id IS NULL)",
            name="staged_has_only_draft",
        ),
        CheckConstraint(
            "state <> 'ready' OR (draft_id IS NULL AND "
            "((issue_report_id IS NOT NULL)::integer + (issue_message_id IS NOT NULL)::integer + "
            "(company_registration_request_id IS NOT NULL)::integer + "
            "(house_addition_request_id IS NOT NULL)::integer) = 1)",
            name="ready_has_one_parent",
        ),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    uploader_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    draft_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("system.drafts.id"))
    issue_report_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.reports.id"))
    issue_message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.messages.id"))
    company_registration_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.company_registration_requests.id")
    )
    house_addition_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.house_addition_requests.id")
    )
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'staged'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        Index("ix_drafts_owner_flow", "owner_user_id", "flow_kind", "updated_at"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    flow_kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity_created", "entity_kind", "entity_id", "created_at"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    before_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminOperation(Base):
    """Append-only record of privileged system-editor mutations."""

    __tablename__ = "admin_operations"
    __table_args__ = (
        Index("ix_admin_operations_entity_created", "entity_key", "row_key", "created_at"),
        CheckConstraint("operation IN ('patch', 'soft_delete', 'hard_delete')", name="operation_allowed"),
        CheckConstraint("length(btrim(reason)) >= 5", name="reason_required"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)
    row_key: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expected_etag: Mapped[str] = mapped_column(Text, nullable=False)
    before_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommandReceipt(Base):
    __tablename__ = "command_receipts"
    __table_args__ = (
        UniqueConstraint("source", "actor_user_id", "external_key", name="uq_command_receipts_source_actor_key"),
        CheckConstraint("source IN ('max_bot', 'miniapp')", name="source_allowed"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_key: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result_kind: Mapped[str | None] = mapped_column(Text)
    result_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_pending", "processed_at", "next_attempt_at"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("outbox_event_id", "recipient_user_id", name="uq_notifications_event_recipient"),
        CheckConstraint(
            "bot_state IN ('pending', 'sent', 'muted', 'blocked', 'failed')",
            name="bot_state_allowed",
        ),
        Index("ix_notifications_recipient_unread", "recipient_user_id", "read_at"),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    outbox_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("system.outbox_events.id"))
    recipient_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    bot_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_bot_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class BotMute(Base):
    __tablename__ = "bot_mutes"
    __table_args__ = (
        CheckConstraint("(issue_id IS NOT NULL) <> (resident_request_id IS NOT NULL)", name="one_subject"),
        Index(
            "uq_bot_mutes_issue",
            "user_id",
            "issue_id",
            unique=True,
            postgresql_where=text("issue_id IS NOT NULL"),
        ),
        Index(
            "uq_bot_mutes_resident_request",
            "user_id",
            "resident_request_id",
            unique=True,
            postgresql_where=text("resident_request_id IS NOT NULL"),
        ),
        {"schema": "system"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    issue_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    resident_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.resident_requests.id")
    )
    is_muted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
