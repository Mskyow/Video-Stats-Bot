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


def _get_int_optional(key: str, default: int) -> int:
    raw = _get_optional(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Переменная {key} должна быть целым числом, получено: {raw}") from exc


def _get_float_optional(key: str, default: float) -> float:
    raw = _get_optional(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Переменная {key} должна быть числом, получено: {raw}") from exc


def _get_bool_optional(key: str, default: bool) -> bool:
    raw = _get_optional(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Переменная {key} должна быть bool (true/false), получено: {raw}")


# Обязательные
TG_TOKEN: str = _get("TG_TOKEN")
OPENROUTER_API_KEY: str = _get("OPENROUTER_API_KEY")
SUPABASE_URL: str = _get("SUPABASE_URL")
SUPABASE_KEY: str = _get("SUPABASE_KEY")

# Опциональные
AUTH_SECRET: str | None = _get_optional("AUTH_SECRET")
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
REPORT_TOPIC_ID: int | None = int(v) if (v := _get_optional("REPORT_TOPIC_ID")) else None
REPORT_TIMEZONE: str = _get_optional("REPORT_TIMEZONE") or "Europe/Minsk"
REPORT_HOUR: int = _get_int_optional("REPORT_HOUR", 15)
REPORT_MINUTE: int = _get_int_optional("REPORT_MINUTE", 0)

# Marketing funnel sources
APPSTORE_ISSUER_ID: str | None = _get_optional("APPSTORE_ISSUER_ID")
APPSTORE_KEY_ID: str | None = _get_optional("APPSTORE_KEY_ID")
APPSTORE_PRIVATE_KEY: str | None = _get_optional("APPSTORE_PRIVATE_KEY")
APPSTORE_BUNDLE_ID: str | None = _get_optional("APPSTORE_BUNDLE_ID")

GOOGLE_PLAY_PACKAGE_NAME: str | None = _get_optional("GOOGLE_PLAY_PACKAGE_NAME")
GOOGLE_PLAY_REPORTS_BUCKET: str | None = _get_optional("GOOGLE_PLAY_REPORTS_BUCKET")
GOOGLE_PLAY_REPORTS_PREFIX: str | None = _get_optional("GOOGLE_PLAY_REPORTS_PREFIX")
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON: str | None = _get_optional("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
GOOGLE_PLAY_SERVICE_ACCOUNT_PATH: str | None = _get_optional("GOOGLE_PLAY_SERVICE_ACCOUNT_PATH")

# Лимит одновременных запросов к AI
MAX_CONCURRENT_ANALYSIS: int = max(1, _get_int_optional("MAX_CONCURRENT_ANALYSIS", 5))
# Тайм-аут одного запроса к OpenRouter (сек)
OPENROUTER_TIMEOUT_SEC: float = max(10.0, _get_float_optional("OPENROUTER_TIMEOUT_SEC", 120.0))
# Количество HTTP retry при сетевых/5xx ошибках OpenRouter
OPENROUTER_MAX_RETRIES: int = max(1, _get_int_optional("OPENROUTER_MAX_RETRIES", 3))
# Использовать response_format/json_schema (если провайдер поддерживает)
OPENROUTER_USE_STRUCTURED_OUTPUT: bool = _get_bool_optional(
    "OPENROUTER_USE_STRUCTURED_OUTPUT", True
)
# Каждые N запросов логировать quality snapshot AI-пайплайна
AI_QUALITY_LOG_EVERY_N: int = max(1, _get_int_optional("AI_QUALITY_LOG_EVERY_N", 25))
# Жесткий тайм-аут скачивания файла из Telegram (сек)
TG_FILE_DOWNLOAD_TIMEOUT_SEC: float = max(
    1.0, _get_float_optional("TG_FILE_DOWNLOAD_TIMEOUT_SEC", 20.0)
)
# Задержка между записями в таблицу (секунды)
SHEETS_WRITE_DELAY: float = 1.2

# Временные коэффициенты для оценки search impressions из viral views.
# Используются только пока по social-виралу нет фактических CSV из сторов.
TIKTOK_SEARCH_IMPRESSIONS_RATE: float = max(
    0.0, _get_float_optional("TIKTOK_SEARCH_IMPRESSIONS_RATE", 0.008)
)
YOUTUBE_SEARCH_IMPRESSIONS_RATE: float = max(
    0.0, _get_float_optional("YOUTUBE_SEARCH_IMPRESSIONS_RATE", 0.005)
)
INSTAGRAM_SEARCH_IMPRESSIONS_RATE: float = max(
    0.0, _get_float_optional("INSTAGRAM_SEARCH_IMPRESSIONS_RATE", 0.004)
)

# AI model policy: Gemini 3 Flash по умолчанию. Можно переопределить через .env.
# Для теста точности: OPENROUTER_MODEL=google/gemini-3.1-pro-preview
OPENROUTER_MODEL: str = _get_optional("OPENROUTER_MODEL") or "google/gemini-3-flash-preview"

# AI Day Summary настройки
ENABLE_DAY_SUMMARY: bool = _get_bool_optional("ENABLE_DAY_SUMMARY", True)
DAY_SUMMARY_MODEL: str = _get_optional("DAY_SUMMARY_MODEL") or "google/gemini-3-flash-preview"
DAY_SUMMARY_MAX_TOKENS: int = max(100, _get_int_optional("DAY_SUMMARY_MAX_TOKENS", 1500))
DAY_SUMMARY_TEMPERATURE: float = max(0.0, min(2.0, _get_float_optional("DAY_SUMMARY_TEMPERATURE", 0.7)))
DAY_SUMMARY_TRANSLATE_TOP_HOOKS: bool = _get_bool_optional(
    "DAY_SUMMARY_TRANSLATE_TOP_HOOKS", True
)


def setup_logging() -> None:
    """Настраивает уровень логирования по LOG_LEVEL."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
