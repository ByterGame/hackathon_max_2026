"""Заявки на подключение и действующие доступы жителей."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, column, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID, ExcludeConstraint
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


REQUEST_STATUS_CHECK = "status IN ('open', 'reviewing', 'needs_info', 'closed', 'cancelled')"


class CompanyRegistrationRequest(Base):
    __tablename__ = "company_registration_requests"
    __table_args__ = (
        CheckConstraint(REQUEST_STATUS_CHECK, name="status_allowed"),
        CheckConstraint("outcome IS NULL OR outcome IN ('approved', 'rejected')", name="outcome_allowed"),
        Index("ix_company_registration_requests_status_created", "status", "created_at"),
        {"schema": "access"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    applicant_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    phone_number: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_company_name: Mapped[str | None] = mapped_column(Text)
    free_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    outcome: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.users.id")
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discussion: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSONB), nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class HouseAdditionRequest(Base):
    __tablename__ = "house_addition_requests"
    __table_args__ = (
        CheckConstraint(REQUEST_STATUS_CHECK, name="status_allowed"),
        CheckConstraint("outcome IS NULL OR outcome IN ('approved', 'rejected')", name="outcome_allowed"),
        CheckConstraint(
            "registration_request_id IS NOT NULL OR company_id IS NOT NULL",
            name="request_has_company_source",
        ),
        Index("ix_house_addition_requests_company_status", "company_id", "status"),
        Index("ix_house_addition_requests_registration", "registration_request_id"),
        {"schema": "access"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    applicant_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    registration_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.company_registration_requests.id")
    )
    company_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.companies.id"))
    entered_address: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_house_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    free_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    outcome: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.users.id")
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discussion: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSONB), nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class ResidentRequest(Base):
    __tablename__ = "resident_requests"
    __table_args__ = (
        CheckConstraint(REQUEST_STATUS_CHECK, name="status_allowed"),
        CheckConstraint("submitted_entrance_number > 0", name="entrance_number_positive"),
        CheckConstraint("submitted_apartment_number > 0", name="apartment_number_positive"),
        CheckConstraint("outcome IS NULL OR outcome IN ('granted', 'denied')", name="outcome_allowed"),
        CheckConstraint(
            "status <> 'closed' OR (outcome IS NOT NULL AND NULLIF(BTRIM(decision_note), '') IS NOT NULL "
            "AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="closed_has_decision",
        ),
        CheckConstraint("status <> 'cancelled' OR outcome IS NULL", name="cancelled_without_outcome"),
        Index("ix_resident_requests_house_status", "house_id", "status"),
        Index("ix_resident_requests_applicant", "applicant_user_id", "created_at"),
        Index(
            "uq_resident_requests_active_location",
            "applicant_user_id",
            "house_id",
            "submitted_entrance_number",
            "submitted_apartment_number",
            unique=True,
            postgresql_where=text("status IN ('open', 'reviewing', 'needs_info')"),
        ),
        {"schema": "access"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    applicant_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    house_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    submitted_full_name: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_entrance_number: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_apartment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_apartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("housing.apartments.id")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    outcome: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.users.id")
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discussion: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSONB), nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class ResidentOffer(Base):
    __tablename__ = "resident_offers"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled')", name="status_allowed"),
        Index("ix_resident_offers_phone_status", "phone_number", "status"),
        {"schema": "access"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    house_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    company_id_at_offer: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.companies.id"))
    apartment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.apartments.id"))
    phone_number: Mapped[str] = mapped_column(Text, nullable=False)
    offered_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    accepted_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposed_access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResidentGrant(Base):
    __tablename__ = "resident_grants"
    __table_args__ = (
        CheckConstraint(
            "(source_request_id IS NOT NULL) <> (source_offer_id IS NOT NULL)",
            name="one_source",
        ),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_period"),
        UniqueConstraint("source_request_id", name="uq_resident_grants_source_request"),
        UniqueConstraint("source_offer_id", name="uq_resident_grants_source_offer"),
        ExcludeConstraint(
            (column("user_id"), "="),
            (column("apartment_id"), "="),
            (
                func.tstzrange(
                    column("valid_from"),
                    func.coalesce(column("valid_to"), text("'infinity'::timestamptz")),
                    text("'[)'"),
                ),
                "&&",
            ),
            name="ex_resident_grants_user_apartment_period",
            using="gist",
        ),
        Index("ix_resident_grants_user_apartment", "user_id", "apartment_id"),
        {"schema": "access"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    apartment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.apartments.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.resident_requests.id")
    )
    source_offer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.resident_offers.id")
    )
    granted_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
