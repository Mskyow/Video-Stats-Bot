"""
Сервис для работы с Google Sheets.

Функционал:
- Авторизация через Service Account.
- Асинхронный экспорт данных через очередь (asyncio.Queue).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    GOOGLE_SHEET_CREDENTIALS_PATH,
    GOOGLE_CREDENTIALS_JSON,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_WORKSHEET_NAME,
    SHEETS_WRITE_DELAY,
)

logger = logging.getLogger(__name__)

# Глобальная очередь для экспорта данных
_export_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


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


def _sync_export_to_sheet(video_data: dict[str, Any]) -> bool:
    """
    Синхронная функция экспорта данных видео в Google Sheet.

    Колонки (Strict Order):
    - Col A: Processed At (Current Timestamp)
    - Col B: Posted At (Normalized date from AI)
    - Col C: Content Type (Video / Carousel)
    - Col D: Age (Hours) (Calculated from posted_at)
    - Col E: Platform (TikTok/Reels)
    - Col F: Video Title (OCR)
    - Col G: Hook Type (Short/Medium/Long)
    - Col H: Score (0-100)
    - Col I: Verdict (KILL/ITERATE/SCALE)
    - Col J: Views (Raw Int)
    - Col K: Likes (Raw Int)
    - Col L: Shares (Raw Int)
    - Col M: Retention 3s (Raw %)
    - Col N: Avg Watch Time (Raw %)
    - Col O: Engagement Rate (%)

    Args:
        video_data: Словарь с результатами анализа AI.

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

        # 1. Подготовка данных (Mapping)

        # A: Processed At
        processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        processed_at_dt = datetime.now()

        # B: Posted At (normalized date from AI - записываем "как есть")
        posted_at = video_data.get("posted_at")
        if not posted_at:
            posted_at = "Not Found"

        # C: Content Type (Video / Carousel)
        content_type = video_data.get("content_type")
        if content_type == "carousel":
            content_type_val = "Carousel"
        else:
            content_type_val = "Video"

        # D: Age (Hours) - расчет из posted_at
        age_hours_val = "Not Found"
        if posted_at and posted_at != "Not Found":
            try:
                # Парсим posted_at (формат YYYY-MM-DD...)
                if len(posted_at) >= 10:
                    posted_at_dt = datetime.strptime(posted_at[:10], "%Y-%m-%d")
                    delta = processed_at_dt - posted_at_dt
                    age_hours = delta.total_seconds() / 3600
                    age_hours_val = f"{age_hours:.1f}"
            except (ValueError, TypeError):
                age_hours_val = "Not Found"

        metrics = video_data.get("metrics") or {}

        # E: Platform
        platform = video_data.get("platform")
        if platform:
             platform = str(platform).capitalize()
        else:
             platform = "Not Recognized"

        # F: Video Title - use hook_text if available, otherwise fall back to title
        hook_text = video_data.get("hook_text")
        if hook_text and hook_text.strip():
            video_title = hook_text.strip()
        else:
            video_title = video_data.get("title")
            if not video_title:
                video_title = "Not Found"

        # G: Hook Type
        hook_type = video_data.get("hook_type")
        if not hook_type:
            hook_type = "Not Found"

        # H: Score
        score = video_data.get("score")
        if score is None:
            score = 0

        # I: Verdict
        verdict = video_data.get("verdict")
        if not verdict:
            verdict = "Not Found"

        # J: Views
        views = metrics.get("views")
        if views is None:
            views = "Not Found"

        # K: Likes
        likes = metrics.get("likes")
        if likes is None:
            likes = "Not Found"

        # L: Shares
        shares = metrics.get("shares")
        if shares is None:
            shares = "Not Found"

        # M: Retention 3s
        retention_3s = metrics.get("retention_3s")
        # Fallback to tier_1 if missing in metrics
        if retention_3s is None:
            tier_1 = video_data.get("tier_1_analysis") or {}
            if tier_1.get("hook_3s"):
                retention_3s = tier_1["hook_3s"].get("value")

        retention_3s_val = f"{retention_3s}%" if retention_3s is not None else "Not Found"

        # N: Avg Watch Time (%)
        # Приоритет: avg_watch_time_pct из metrics
        avg_watch_time = metrics.get("avg_watch_time_pct")
        avg_watch_time_val = f"{avg_watch_time}%" if avg_watch_time is not None else "Not Found"

        # O: Engagement Rate (%)
        er_val = "Not Found"
        calculated_rates = video_data.get("calculated_rates") or {}
        aggregated_er = calculated_rates.get("aggregated_er")

        if aggregated_er is not None:
            er_val = f"{aggregated_er:.1f}%"
        else:
            # Считаем вручную: (likes + shares + comments + saves) / views
            likes_num = metrics.get("likes") if metrics.get("likes") is not None else 0
            shares_num = metrics.get("shares") if metrics.get("shares") is not None else 0
            comments_num = metrics.get("comments") if metrics.get("comments") is not None else 0
            saves_num = metrics.get("saves") if metrics.get("saves") is not None else 0
            views_num = metrics.get("views") if metrics.get("views") is not None else 0

            if views_num > 0:
                er = (likes_num + shares_num + comments_num + saves_num) / views_num * 100
                er_val = f"{er:.1f}%"

        # Формируем строку (обновленный порядок колонок)
        row = [
            processed_at,       # A: Processed At
            posted_at,          # B: Posted At
            content_type_val,   # C: Content Type (Video / Carousel)
            age_hours_val,      # D: Age (Hours)
            platform,           # E: Platform
            video_title,        # F: Video Title / Hook Text
            hook_type,          # G: Hook Type
            score,              # H: Score
            verdict,            # I: Verdict
            views,              # J: Views
            likes,              # K: Likes
            shares,             # L: Shares
            retention_3s_val,   # M: Retention 3s
            avg_watch_time_val, # N: Avg Watch Time (%)
            er_val              # O: Engagement Rate (%)
        ]

        # Отправка в Google Sheets
        worksheet.append_row(row)
        logger.info(f"Exported video to sheet: '{video_title}' (Score: {score})")
        return True

    except FileNotFoundError as e:
        logger.warning(f"Sheet export skipped (files): {e}")
        return False
    except gspread.WorksheetNotFound:
        logger.error(f"Worksheet '{GOOGLE_SHEET_WORKSHEET_NAME}' not found.")
        return False
    except Exception as e:
        logger.error(f"Failed to export video to sheet: {e}")
        # Не роняем бота, просто логируем ошибку
        return False


async def sheets_worker() -> None:
    """
    Асинхронный воркер для обработки очереди экспорта в Google Sheets.
    Работает в бесконечном цикле, обрабатывая задачи из очереди.
    """
    while True:
        data = await _export_queue.get()

        try:
            await asyncio.to_thread(_sync_export_to_sheet, data)
        except Exception as e:
            logger.error(f"Error in sheets_worker: {e}")
        finally:
            _export_queue.task_done()
            await asyncio.sleep(SHEETS_WRITE_DELAY)


async def queue_export_to_sheet(video_data: dict[str, Any]) -> None:
    """Добавляет данные в очередь на экспорт."""
    await _export_queue.put(video_data)
