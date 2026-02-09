"""
Сервис для работы с Google Sheets.

Функционал:
- Авторизация через Service Account.
- Экспорт всех хуков в существующую таблицу (без фильтрации по score).
- Асинхронный background worker для обработки очереди экспорта.
"""
from __future__ import annotations

import asyncio
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

# Очередь для экспорта данных в Google Sheets
_sheets_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


def queue_export(video_data: dict[str, Any]) -> None:
    """
    Добавляет данные видео в очередь для экспорта в Google Sheets.

    Args:
        video_data: Словарь с данными видео для экспорта.
    """
    try:
        _sheets_queue.put_nowait(video_data)
        logger.debug("Queued video for sheets export: %s", video_data.get("title", "Unknown"))
    except asyncio.QueueFull:
        logger.warning("Sheets export queue is full, dropping video: %s", video_data.get("title", "Unknown"))


async def sheets_worker() -> None:
    """
    Background worker для асинхронного экспорта данных в Google Sheets.

    Обрабатывает очередь _sheets_queue и вызывает export_hook_to_sheet
    для каждого элемента в очереди.
    """
    logger.info("Sheets worker started")
    while True:
        try:
            video_data = await _sheets_queue.get()
            logger.debug("Processing video for sheets export: %s", video_data.get("title", "Unknown"))

            # export_hook_to_sheet синхронная, запускаем в executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, export_hook_to_sheet, video_data)

            if result:
                logger.info("Successfully exported to sheets: %s", video_data.get("title", "Unknown"))
            else:
                logger.warning("Failed to export to sheets: %s", video_data.get("title", "Unknown"))

            _sheets_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Sheets worker cancelled")
            break
        except Exception as e:
            logger.exception("Error in sheets worker: %s", e)
            # Продолжаем работу даже после ошибки
            await asyncio.sleep(1)


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
            - hook_type (str | None): Тип хука (Short/Medium/Long).
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