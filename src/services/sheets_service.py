"""
Сервис для работы с Google Sheets.

Функционал:
- Авторизация через Service Account.
- Экспорт всех хуков в существующую таблицу (без фильтрации по score).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    GOOGLE_SHEET_CREDENTIALS_PATH,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_WORKSHEET_NAME,
)

logger = logging.getLogger(__name__)


def _get_credentials() -> ServiceAccountCredentials:
    """
    Загружает credentials для Google Sheets из JSON-файла Service Account.

    Returns:
        Готовые к использованию credentials.
    """
    creds_path = Path(GOOGLE_SHEET_CREDENTIALS_PATH)

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Файл credentials не найден: {creds_path}. "
            "Проверьте GOOGLE_SHEET_CREDENTIALS_PATH в .env"
        )

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        str(creds_path), scope
    )
    return credentials


def _get_client() -> gspread.Client:
    """
    Создаёт авторизованный gspread Client.

    Returns:
        Авторизованный клиент.
    """
    credentials = _get_credentials()
    return gspread.authorize(credentials)


def _get_worksheet(client: gspread.Client) -> gspread.Worksheet:
    """
    Открывает рабочий лист в существующей таблице.

    Args:
        client: Авторизованный gspread Client.

    Returns:
        Рабочий лист.
    """
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET_NAME)
    return worksheet


def export_hook_to_sheet(video_data: dict[str, Any]) -> bool:
    """
    Экспортирует данные видео (хука) в существующий Google Sheet.

    Отправляет ВСЕ хуки без фильтрации по score.

    Args:
        video_data: Словарь с данными видео, содержащий:
            - score (float): Общий балл видео.
            - platform (str): Платформа (TikTok, Reels, Shorts).
            - title (str | None): Название видео.
            - hook_score (str | None): Оценка хука (FAIL/BORDERLINE/GOOD/SCALE).
            - tier_1_analysis (dict | None): Детальный анализ с retention_3s.

    Returns:
        True, если экспорт выполнен; False — если произошла ошибка.
    """
    if not GOOGLE_SHEET_CREDENTIALS_PATH or not GOOGLE_SHEET_ID:
        logger.debug("Google Sheets not configured; skip export")
        return False
    try:
        client = _get_client()
        worksheet = _get_worksheet(client)

        # Извлекаем данные
        platform = video_data.get("platform") or "Unknown"
        title = video_data.get("title") or "-"
        hook_score = video_data.get("hook_score") or "-"
        score = float(video_data.get("score") or 0)

        # Retention 3s из tier_1_analysis
        tier_1 = video_data.get("tier_1_analysis") or {}
        retention_3s = "-"
        if tier_1.get("hook_3s"):
            retention_3s = tier_1["hook_3s"].get("retention_3s", "-")

        verdict = video_data.get("verdict") or "-"

        # Формируем строку
        row = [
            date.today().isoformat(),
            platform,
            title,
            hook_score,
            retention_3s,
            verdict,
        ]

        worksheet.append_row(row)
        logger.info(
            "Exported hook to sheet: platform=%s, hook_score=%s, score=%.1f",
            platform,
            hook_score,
            score,
        )
        return True

    except FileNotFoundError as e:
        logger.warning("Sheet export skipped: %s", e)
        return False
    except gspread.WorksheetNotFound:
        logger.error(
            "Worksheet '%s' not found in spreadsheet. "
            "Please create the sheet manually.",
            GOOGLE_SHEET_WORKSHEET_NAME,
        )
        return False
    except gspread.GSpreadException as e:
        logger.exception("Google Sheets API error: %s", e)
        return False
    except Exception as e:
        logger.exception("Unexpected error during sheet export: %s", e)
        return False