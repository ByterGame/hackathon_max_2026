"""Настройки пула соединений с PostgreSQL."""

from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import DatabaseConfig


def create_database_pool(config: DatabaseConfig) -> asyncpg.Pool:
    """Прямой пул старых демонстрационных ручек; будет удалён после перехода API."""
    return asyncpg.create_pool(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.user,
        password=config.password,
        min_size=1,
        max_size=3,
        timeout=5,
        command_timeout=5,
    )


def create_database_url(config: DatabaseConfig) -> URL:
    """Собрать URL без ручного экранирования пароля."""
    return URL.create(
        "postgresql+asyncpg",
        username=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
        database=config.database,
    )


def create_database_engine(config: DatabaseConfig) -> AsyncEngine:
    """Движок на процесс; закрывать через ``await engine.dispose()``."""
    return create_async_engine(
        create_database_url(config),
        pool_size=2,
        max_overflow=1,
        pool_timeout=5,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Каждый HTTP-запрос или команда бота получает свою сессию."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI Depends: жизненным циклом engine управляет lifespan приложения."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory
    async with factory() as session:
        yield session
