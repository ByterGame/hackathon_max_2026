import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


def load_port() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    raw_port = os.getenv("PORT")
    if not raw_port:
        raise RuntimeError("PORT is not set")
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


def load_bot_token() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    return token


def test_mode_enabled() -> bool:
    """Only an explicit server-side TEST_MODE=1 enables test-only mutations."""
    return os.getenv("TEST_MODE") == "1"


def load_database_config() -> DatabaseConfig:
    load_dotenv(PROJECT_ROOT / ".env")
    raw_port = os.getenv("DB_PORT")
    if not raw_port:
        raise RuntimeError("DB_PORT is not set")
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("DB_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("DB_PORT must be between 1 and 65535")

    host = os.getenv("DB_HOST")
    database = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    if not host or not database or not user or not password:
        raise RuntimeError("DB_HOST, DB_NAME, DB_USER and DB_PASSWORD must be set")
    return DatabaseConfig(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )
