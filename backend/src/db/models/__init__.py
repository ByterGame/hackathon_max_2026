"""Публичные ORM-модели предметной базы."""

from .access import (
    CompanyRegistrationRequest,
    HouseAdditionRequest,
    ResidentGrant,
    ResidentOffer,
    ResidentRequest,
)
from .base import Base
from .bot_dialog import BotDialog
from .housing import Apartment, Company, House
from .identity import StaffAssignment, User
from .issues import IssueCard, IssueCategory, IssueMessage, IssueReport, IssueSupport, IssueTarget
from .system import AuditEvent, BotMute, CommandReceipt, Draft, File, Notification, OutboxEvent

__all__ = [
    "Apartment",
    "AuditEvent",
    "Base",
    "BotDialog",
    "BotMute",
    "CommandReceipt",
    "Company",
    "CompanyRegistrationRequest",
    "Draft",
    "File",
    "House",
    "HouseAdditionRequest",
    "IssueCard",
    "IssueCategory",
    "IssueMessage",
    "IssueReport",
    "IssueSupport",
    "IssueTarget",
    "Notification",
    "OutboxEvent",
    "ResidentGrant",
    "ResidentOffer",
    "ResidentRequest",
    "StaffAssignment",
    "User",
]
