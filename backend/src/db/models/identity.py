"""Люди, операторы поддержки и назначения сотрудников УК."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('unassigned', 'resident', 'employee', 'support')",
            name="user_kind",
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    max_user_id: Mapped[str | None] = mapped_column(Text, unique=True)
    phone_number: Mapped[str | None] = mapped_column(Text, unique=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unassigned'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class StaffAssignment(Base):
    __tablename__ = "staff_assignments"
    __table_args__ = (
        Index(
            "uq_staff_active_company_phone",
            "company_id",
            "phone_number",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_staff_active_company_user",
            "company_id",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND user_id IS NOT NULL"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.companies.id"))
    phone_number: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    can_manage_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    can_manage_residents: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    can_manage_issues: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    granted_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("identity.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
