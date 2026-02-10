"""
Сервис для работы с Google Sheets.

Функционал:
- Авторизация через Service Account.
- Экспорт данных видео в 16-колоночный формат отчета.
- Умный дорасчет отсутствующих полей (age_hours, aggregated_er).
- Безопасное форматирование процентов и пустых значений.
- Отказоустойчивость с retry-логикой и exponential backoff.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dateparser
import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    GOOGLE_SHEET_CREDENTIALS_PATH,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_WORKSHEET_NAME,
)

logger = logging.getLogger(__name__)

# Очередь для экспорта данных в Google Sheets
_sheets_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

# Константы для retry-логики
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # секунды

# Определение 16 колонок отчета в строгом порядке
REPORT_COLUMNS = [
    "Processed At",   # 1
    "Posted At",      # 2
    "Content Type",   # 3
    "Age",            # 4
    "Platform",       # 5
    "Hook Text",      # 6
    "Hook Type",      # 7
    "Score",          # 8
    "Verdict",        # 9
    "Views",          # 10
    "Likes",          # 11
    "Comments",       # 12
    "Shares",         # 13
    "Retention",      # 14
    "Watch Time",     # 15
    "ER",             # 16
]


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

    Обрабатывает очередь _sheets_queue и вызывает export_video_to_sheet
    для каждого элемента в очереди.
    """
    logger.info("Sheets worker started")
    while True:
        try:
            video_data = await _sheets_queue.get()
            logger.debug("Processing video for sheets export: %s", video_data.get("title", "Unknown"))

            # export_video_to_sheet синхронная, запускаем в executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, export_video_to_sheet, video_data)

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

    Raises:
        FileNotFoundError: Если файл credentials не найден.
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
    Если лист не найден, пытается создать его.

    Args:
        client: Авторизованный gspread Client.

    Returns:
        Рабочий лист.

    Raises:
        WorksheetNotFound: Если лист не найден и не удалось создать.
    """
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET_NAME)
        return worksheet
    except WorksheetNotFound:
        available_sheets = [ws.title for ws in spreadsheet.worksheets()]
        logger.warning(
            "Worksheet '%s' not found. Available sheets: %s. Attempting to create...",
            GOOGLE_SHEET_WORKSHEET_NAME,
            available_sheets
        )
        try:
            worksheet = spreadsheet.add_worksheet(title=GOOGLE_SHEET_WORKSHEET_NAME, rows=1000, cols=20)
            logger.info("Successfully created worksheet '%s'", GOOGLE_SHEET_WORKSHEET_NAME)
            return worksheet
        except Exception as e:
            logger.error("Failed to create worksheet '%s': %s", GOOGLE_SHEET_WORKSHEET_NAME, e)
            raise


def _ensure_headers(worksheet: gspread.Worksheet) -> None:
    """
    Проверяет и создаёт заголовки колонок, если они отсутствуют.

    Args:
        worksheet: Рабочий лист Google Sheets.
    """
    try:
        first_row = worksheet.row_values(1)
        # Если первая строка пустая или не совпадает с ожидаемыми заголовками
        if not first_row or first_row[0] != REPORT_COLUMNS[0]:
            logger.info("Creating headers in Google Sheet")
            worksheet.insert_row(REPORT_COLUMNS, 1)
    except Exception as e:
        logger.warning("Could not ensure headers: %s", e)


def _calculate_age_hours(posted_at: str | None) -> float | None:
    """
    Вычисляет возраст контента в часах из строки даты posted_at.

    Args:
        posted_at: Строка с датой публикации.

    Returns:
        Возраст в часах или None, если не удалось распарсить.
    """
    if not posted_at:
        return None

    try:
        parsed_date = dateparser.parse(posted_at)
        if parsed_date:
            now = datetime.now(timezone.utc)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            age_hours = (now - parsed_date).total_seconds() / 3600
            return round(age_hours, 1)
    except Exception as e:
        logger.debug("Failed to parse posted_at '%s': %s", posted_at, e)

    return None


def _calculate_er(actions: dict[str, Any] | None, views: int | None) -> float | None:
    """
    Вычисляет Engagement Rate по формуле: (actions / views) * 100.

    Args:
        actions: Словарь с действиями (likes, comments, shares и т.д.).
        views: Количество просмотров.

    Returns:
        ER в процентах или None, если невозможно вычислить.
    """
    if not views or views <= 0:
        return None

    if not actions or not isinstance(actions, dict):
        return None

    # Суммируем все действия
    total_actions = sum(
        value for value in actions.values()
        if isinstance(value, (int, float)) and value > 0
    )

    if total_actions <= 0:
        return None

    er = (total_actions / views) * 100
    return round(er, 2)


def _format_percentage(value: float | None) -> str:
    """
    Форматирует float значение как процент.

    Args:
        value: Числовое значение (например, 0.5 для 0.5%).

    Returns:
        Отформатированная строка с процентом или "-".
    """
    if value is None:
        return "-"
    return f"{value}%"


def _format_number(value: int | float | None) -> str:
    """
    Форматирует числовое значение.

    Args:
        value: Числовое значение.

    Returns:
        Отформатированная строка или "-".
    """
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _safe_get(data: dict[str, Any], key: str, default: str = "-") -> str:
    """
    Безопасно получает значение из словаря.

    Args:
        data: Словарь с данными.
        key: Ключ для получения значения.
        default: Значение по умолчанию.

    Returns:
        Значение или default, если значение пустое.
    """
    value = data.get(key)
    if value is None or value == "" or value == []:
        return default
    return str(value)


def _build_row(video_data: dict[str, Any]) -> list[str]:
    """
    Формирует строку данных для экспорта в 16-колоночный формат.

    Args:
        video_data: Словарь с данными видео.

    Returns:
        Список значений для 16 колонок.
    """
    # 1. Processed At - текущая дата/время
    processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 2. Posted At - дата публикации
    posted_at = _safe_get(video_data, "posted_at", "-")

    # 3. Content Type - тип контента
    content_type = _safe_get(video_data, "content_type", "-")

    # 4. Age - возраст в часах (с дорасчетом если нужно)
    age_hours = video_data.get("age_hours")
    if age_hours is None and posted_at != "-":
        age_hours = _calculate_age_hours(posted_at)
    age = _format_number(age_hours)

    # 5. Platform - платформа
    platform = _safe_get(video_data, "platform", "-")

    # 6. Hook Text - текст хука
    hook_text = _safe_get(video_data, "hook_text", "-")
    if hook_text == "-":
        # Fallback на title если hook_text не найден
        hook_text = _safe_get(video_data, "title", "-")

    # 7. Hook Type - тип хука
    hook_type = _safe_get(video_data, "hook_type", "-")

    # 8. Score - общий балл
    score = video_data.get("score")
    score_str = _format_number(score) if score is not None else "-"

    # 9. Verdict - вердикт
    verdict = _safe_get(video_data, "verdict", "-")

    # 10-13. Metrics: Views, Likes, Comments, Shares
    metrics = video_data.get("metrics") or {}
    views = metrics.get("views")
    likes = metrics.get("likes")
    comments = metrics.get("comments")
    shares = metrics.get("shares")

    views_str = _format_number(views)
    likes_str = _format_number(likes)
    comments_str = _format_number(comments)
    shares_str = _format_number(shares)

    # 14. Retention - удержание аудитории
    retention = video_data.get("retention")
    retention_str = _format_percentage(retention)

    # 15. Watch Time - время просмотра
    watch_time = video_data.get("watch_time")
    watch_time_str = _format_number(watch_time)

    # 16. ER - Engagement Rate (с дорасчетом если нужно)
    aggregated_er = video_data.get("aggregated_er")
    if aggregated_er is None and views:
        # Дорасчет ER по формуле: (actions / views) * 100
        actions = video_data.get("actions") or {
            "likes": likes or 0,
            "comments": comments or 0,
            "shares": shares or 0,
        }
        aggregated_er = _calculate_er(actions, views)
    er_str = _format_percentage(aggregated_er)

    return [
        processed_at,   # 1
        posted_at,      # 2
        content_type,   # 3
        age,            # 4
        platform,       # 5
        hook_text,      # 6
        hook_type,      # 7
        score_str,      # 8
        verdict,        # 9
        views_str,      # 10
        likes_str,      # 11
        comments_str,   # 12
        shares_str,     # 13
        retention_str,  # 14
        watch_time_str, # 15
        er_str,         # 16
    ]


def _append_row_with_retry(
    worksheet: gspread.Worksheet,
    row: list[str],
    max_retries: int = MAX_RETRIES,
) -> bool:
    """
    Добавляет строку в таблицу с retry-логикой и exponential backoff.

    Args:
        worksheet: Рабочий лист Google Sheets.
        row: Список значений для добавления.
        max_retries: Максимальное количество попыток.

    Returns:
        True если успешно, False если исчерпаны попытки.
    """
    for attempt in range(max_retries):
        try:
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            return True
        except APIError as e:
            if e.response.status_code == 429:  # Rate limit
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    "Rate limit hit, retrying in %ds (attempt %d/%d)",
                    delay, attempt + 1, max_retries
                )
                time.sleep(delay)
            else:
                logger.exception("API error on attempt %d: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY_BASE)
        except Exception as e:
            logger.exception("Error on attempt %d: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY_BASE)

    logger.error("Failed to append row after %d attempts", max_retries)
    return False


def export_video_to_sheet(video_data: dict[str, Any]) -> bool:
    """
    Экспортирует данные видео в Google Sheet в 16-колоночном формате.

    Колонки (в порядке):
        1. Processed At - дата/время обработки
        2. Posted At - дата публикации
        3. Content Type - тип контента
        4. Age - возраст в часах (дорасчет из posted_at если нужно)
        5. Platform - платформа
        6. Hook Text - текст хука
        7. Hook Type - тип хука
        8. Score - общий балл
        9. Verdict - вердикт
        10. Views - просмотры
        11. Likes - лайки
        12. Comments - комментарии
        13. Shares - репосты
        14. Retention - удержание
        15. Watch Time - время просмотра
        16. ER - Engagement Rate (дорасчет если нужно)

    Args:
        video_data: Словарь с данными видео.

    Returns:
        True, если экспорт выполнен; False — если произошла ошибка.
    """
    if not GOOGLE_SHEET_CREDENTIALS_PATH or not GOOGLE_SHEET_ID:
        logger.debug("Google Sheets not configured; skip export")
        return False

    try:
        client = _get_client()
        worksheet = _get_worksheet(client)

        # Убеждаемся, что заголовки есть
        _ensure_headers(worksheet)

        # Формируем строку данных
        row = _build_row(video_data)

        # Добавляем строку с retry-логикой
        success = _append_row_with_retry(worksheet, row)

        if success:
            logger.info(
                "Exported video to sheet: platform=%s, title=%s",
                video_data.get("platform", "Unknown"),
                video_data.get("title", "Unknown"),
            )
        return success

    except FileNotFoundError as e:
        logger.warning("Sheet export skipped: %s", e)
        return False
    except WorksheetNotFound as e:
        logger.error(
            "Worksheet '%s' not found in spreadsheet '%s'. "
            "Please create the sheet manually. Error: %s",
            GOOGLE_SHEET_WORKSHEET_NAME,
            GOOGLE_SHEET_ID,
            e,
        )
        return False
    except APIError as e:
        logger.exception(
            "Google Sheets API error: status=%s, code=%s, message=%s",
            getattr(e.response, 'status_code', 'unknown'),
            getattr(e, 'code', 'unknown'),
            str(e),
        )
        return False
    except Exception as e:
        logger.exception(
            "Unexpected error during sheet export: %s (type: %s)",
            e,
            type(e).__name__,
        )
        return False


# Backwards compatibility alias
export_hook_to_sheet = export_video_to_sheet
