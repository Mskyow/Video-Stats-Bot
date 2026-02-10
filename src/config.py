"""
Загрузка настроек из переменных окружения.
Обязательные: TG_TOKEN, OPENROUTER_API_KEY, SUPABASE_URL, SUPABASE_KEY.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (рядом с src/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _get(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None or value == "":
        hint = (
            " На Railway: Project → Variables. Локально: создайте .env из .env.example."
        )
        raise ValueError(f"Не задана обязательная переменная окружения: {key}.{hint}")
    return value.strip()


def _get_optional(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    return value.strip() if value else None


# Обязательные
TG_TOKEN: str = _get("TG_TOKEN")
OPENROUTER_API_KEY: str = _get("OPENROUTER_API_KEY")
SUPABASE_URL: str = _get("SUPABASE_URL")
SUPABASE_KEY: str = _get("SUPABASE_KEY")

# Опциональные (OpenRouter: по умолчанию Gemini 3 Flash с thinking)
AUTH_SECRET: str | None = _get_optional("AUTH_SECRET")
OPENROUTER_MODEL: str | None = _get_optional("OPENROUTER_MODEL")
LOG_LEVEL: str = _get_optional("LOG_LEVEL") or "INFO"
WEBHOOK_URL: str | None = _get_optional("WEBHOOK_URL")
WEBHOOK_PATH: str = _get_optional("WEBHOOK_PATH") or "/webhook"
# Telegram ID администратора (для уведомлений о новых пользователях)
ADMIN_USER_ID: int | None = int(v) if (v := _get_optional("ADMIN_USER_ID")) else None

# Google Sheets (Service Account для экспорта аналитики хуков)
# Можно указать либо путь к файлу (локально), либо JSON-строку (Railway)
GOOGLE_SHEET_CREDENTIALS_PATH: str | None = _get_optional("GOOGLE_SHEET_CREDENTIALS_PATH")
GOOGLE_CREDENTIALS_JSON: str | None = _get_optional("GOOGLE_CREDENTIALS_JSON")
GOOGLE_SHEET_ID: str | None = _get_optional("GOOGLE_SHEET_ID")
# Название листа в таблице (например "Hooks CRM" или "Hook Analytics")
GOOGLE_SHEET_WORKSHEET_NAME: str = _get_optional("GOOGLE_SHEET_WORKSHEET_NAME") or "Hooks CRM"
REPORT_CHAT_ID: int | None = int(v) if (v := _get_optional("REPORT_CHAT_ID")) else None

# Лимит одновременных запросов к AI
MAX_CONCURRENT_ANALYSIS: int = 4
# Задержка между записями в таблицу (секунды)
SHEETS_WRITE_DELAY: float = 1.2


def setup_logging() -> None:
    """Настраивает уровень логирования по LOG_LEVEL."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
