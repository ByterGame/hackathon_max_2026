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
from .identity import StaffAssignment, SupportInvitation, User
from .issues import IssueCard, IssueCategory, IssueMessage, IssueReport, IssueSupport, IssueTarget
from .system import AdminOperation, AuditEvent, BotMute, CommandReceipt, Draft, File, Notification, OutboxEvent

__all__ = [
    "Apartment",
    "AdminOperation",
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
    "SupportInvitation",
    "User",
]
