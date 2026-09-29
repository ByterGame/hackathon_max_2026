"""Общие карточки проблем, первичные сообщения и голоса жителей."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class IssueCategory(Base):
    __tablename__ = "categories"
    __table_args__ = (Index("ix_categories_active_order", "is_active", "sort_order"), {"schema": "issues"})

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class IssueCard(Base):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'reviewing', 'needs_info', 'in_progress', 'closed')",
            name="status_allowed",
        ),
        CheckConstraint("close_result IS NULL OR close_result IN ('solved', 'invalid')", name="close_result_allowed"),
        CheckConstraint(
            "status <> 'closed' OR (close_result IS NOT NULL AND "
            "NULLIF(BTRIM(current_note), '') IS NOT NULL AND closed_at IS NOT NULL)",
            name="closed_has_result",
        ),
        CheckConstraint(
            "status = 'closed' OR (close_result IS NULL AND closed_at IS NULL)",
            name="open_has_no_close_result",
        ),
        CheckConstraint("merged_into_id IS NULL OR merged_into_id <> id", name="not_merged_into_self"),
        Index("ix_cards_house_status_created", "house_id", "status", "created_at"),
        {"schema": "issues"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    house_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    author_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    category_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.categories.id"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    close_result: Mapped[str | None] = mapped_column(Text)
    current_note: Mapped[str | None] = mapped_column(Text)
    scope_all_house: Mapped[bool] = mapped_column(Boolean, nullable=False)
    merged_into_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class IssueTarget(Base):
    __tablename__ = "targets"
    __table_args__ = (
        CheckConstraint(
            "(entrance_number IS NOT NULL) <> (apartment_id IS NOT NULL)",
            name="one_target_kind",
        ),
        CheckConstraint("entrance_number IS NULL OR entrance_number > 0", name="entrance_number_positive"),
        Index("ix_targets_house", "house_id"),
        Index(
            "uq_targets_card_entrance",
            "card_id",
            "entrance_number",
            unique=True,
            postgresql_where=text("entrance_number IS NOT NULL"),
        ),
        Index(
            "uq_targets_card_apartment",
            "card_id",
            "apartment_id",
            unique=True,
            postgresql_where=text("apartment_id IS NOT NULL"),
        ),
        {"schema": "issues"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    house_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    entrance_number: Mapped[int | None] = mapped_column(Integer)
    apartment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.apartments.id"))


class IssueReport(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_card_created", "card_id", "created_at"),
        {"schema": "issues"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    origin_card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    author_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueSupport(Base):
    __tablename__ = "supports"
    __table_args__ = (Index("ix_supports_user", "user_id"), {"schema": "issues"})

    card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"), primary_key=True)
    supported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("kind IN ('resident_comment', 'official_uk')", name="kind_allowed"),
        Index("ix_messages_card_created", "card_id", "created_at"),
        {"schema": "issues"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    origin_card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("issues.cards.id"))
    author_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
