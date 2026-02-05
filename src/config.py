"""
Загрузка настроек из переменных окружения.
Обязательные: TG_TOKEN, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY.
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
        raise ValueError(f"Не задана обязательная переменная окружения: {key}")
    return value.strip()


def _get_optional(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    return value.strip() if value else None


# Обязательные
TG_TOKEN: str = _get("TG_TOKEN")
GEMINI_API_KEY: str = _get("GEMINI_API_KEY")
SUPABASE_URL: str = _get("SUPABASE_URL")
SUPABASE_KEY: str = _get("SUPABASE_KEY")

# Опциональные
GEMINI_MODEL: str = _get_optional("GEMINI_MODEL") or "gemini-2.0-flash-thinking-exp"
LOG_LEVEL: str = _get_optional("LOG_LEVEL") or "INFO"
WEBHOOK_URL: str | None = _get_optional("WEBHOOK_URL")
WEBHOOK_PATH: str = _get_optional("WEBHOOK_PATH") or "/webhook"


def setup_logging() -> None:
    """Настраивает уровень логирования по LOG_LEVEL."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
