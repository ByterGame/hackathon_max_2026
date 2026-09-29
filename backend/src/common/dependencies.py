"""Shared FastAPI dependencies for database-backed handlers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session


DBSession = Annotated[AsyncSession, Depends(get_session)]
