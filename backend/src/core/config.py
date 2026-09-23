import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PORT = 8000


def load_port() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    raw_port = os.getenv("PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


def load_bot_token() -> str:
    # Чтение настроек только при запуске бота, не при импорте модулей.
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    return token
