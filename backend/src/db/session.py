"""Настройки пула соединений с PostgreSQL."""

import asyncpg

from src.core.config import DatabaseConfig


def create_database_pool(config: DatabaseConfig) -> asyncpg.Pool:
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
