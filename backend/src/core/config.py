import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_bot_token() -> str:
    # Чтение настроек только при запуске бота, не при импорте модулей.
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    return token
