"""Управляющие компании, дома и квартиры."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = {"schema": "housing"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    registration_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access.company_registration_requests.id"), unique=True
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text)
    inn: Mapped[str | None] = mapped_column(Text, unique=True)
    ogrn: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class House(Base):
    __tablename__ = "houses"
    __table_args__ = (
        CheckConstraint("entrance_count IS NULL OR entrance_count > 0", name="entrance_count_positive"),
        CheckConstraint("apartment_count IS NULL OR apartment_count > 0", name="apartment_count_positive"),
        Index("ix_houses_company_id", "company_id"),
        {"schema": "housing"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.companies.id"))
    address_display: Mapped[str] = mapped_column(Text, nullable=False)
    address_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    entrance_count: Mapped[int | None] = mapped_column(Integer)
    apartment_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Apartment(Base):
    __tablename__ = "apartments"
    __table_args__ = (
        UniqueConstraint("house_id", "apartment_number", name="uq_apartment_location"),
        CheckConstraint("entrance_number IS NULL OR entrance_number > 0", name="entrance_number_positive"),
        CheckConstraint("apartment_number > 0", name="apartment_number_positive"),
        {"schema": "housing"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    house_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("housing.houses.id"))
    entrance_number: Mapped[int | None] = mapped_column(Integer)
    apartment_number: Mapped[int] = mapped_column(Integer, nullable=False)
