"""
Сервис для работы с Google Sheets.

Функционал:
- Авторизация через Service Account.
- Экспорт всех хуков в существующую таблицу (без фильтрации по score).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    GOOGLE_SHEET_CREDENTIALS_PATH,
    GOOGLE_CREDENTIALS_JSON,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_WORKSHEET_NAME,
)

logger = logging.getLogger(__name__)


def _get_credentials() -> ServiceAccountCredentials:
    """
    Загружает credentials для Google Sheets.
    
    Поддерживает два способа:
    - Из JSON-файла (локально, GOOGLE_SHEET_CREDENTIALS_PATH)
    - Из переменной окружения (Railway, GOOGLE_CREDENTIALS_JSON)

    Returns:
        Готовые к использованию credentials.
    """
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    # Приоритет: GOOGLE_CREDENTIALS_JSON (для Railway)
    if GOOGLE_CREDENTIALS_JSON:
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
            credentials = ServiceAccountCredentials.from_json_keyfile_dict(
                creds_dict, scope
            )
            return credentials
        except json.JSONDecodeError as e:
            raise ValueError(f"Невалидный JSON в GOOGLE_CREDENTIALS_JSON: {e}")

    # Fallback: GOOGLE_SHEET_CREDENTIALS_PATH (для локальной разработки)
    if GOOGLE_SHEET_CREDENTIALS_PATH:
        creds_path = Path(GOOGLE_SHEET_CREDENTIALS_PATH)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Файл credentials не найден: {creds_path}. "
                "Проверьте GOOGLE_SHEET_CREDENTIALS_PATH в .env или используйте GOOGLE_CREDENTIALS_JSON"
            )
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            str(creds_path), scope
        )
        return credentials

    raise ValueError(
        "Не заданы credentials для Google Sheets. "
        "Укажите GOOGLE_CREDENTIALS_JSON (для Railway) или GOOGLE_SHEET_CREDENTIALS_PATH (для локальной разработки)"
    )


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
            - hook_type (str | None): Тип хука (Short/Medium/Long).
            - tier_1_analysis (dict | None): Детальный анализ с retention_3s.
    
    Returns:
        True, если экспорт выполнен; False — если произошла ошибка.
    """
    credentials_configured = GOOGLE_CREDENTIALS_JSON or GOOGLE_SHEET_CREDENTIALS_PATH
    if not credentials_configured or not GOOGLE_SHEET_ID:
        logger.debug("Google Sheets not configured; skip export")
        return False
    try:
        client = _get_client()
        worksheet = _get_worksheet(client)

        # Извлекаем данные
        platform = video_data.get("platform") or "Unknown"
        title = video_data.get("title") or "-"
        hook_type = video_data.get("hook_type") or "-"
        
        # Retention 3s из tier_1_analysis
        tier_1 = video_data.get("tier_1_analysis") or {}
        retention_3s = "-"
        if tier_1.get("hook_3s"):
            retention_3s = tier_1["hook_3s"].get("retention_3s", "-")
            # Fallback if retention_3s key is missing but value is there
            if retention_3s == "-" and "value" in tier_1["hook_3s"]:
                 retention_3s = tier_1["hook_3s"]["value"]

        verdict = video_data.get("verdict") or "-"

        # Формируем строку: [Date] | [Platform] | [Video Title] | [Hook Type] | [Retention 3s] | [Verdict]
        row = [
            date.today().isoformat(),
            platform,
            title,
            hook_type,
            retention_3s,
            verdict,
        ]

        worksheet.append_row(row)
        logger.info(
            "Exported hook to sheet: platform=%s, title=%s",
            platform,
            title,
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