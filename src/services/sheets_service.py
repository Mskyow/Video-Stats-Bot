"""
Google Sheets integration for two independent flows:
- Video Analysis: one row per analyzed video from screenshots
- Marketing Funnels: one row per Date + Channel + Store, with TOTAL rows
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dateparser
import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    GOOGLE_CREDENTIALS_JSON,
    GOOGLE_SHEET_CREDENTIALS_PATH,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_WORKSHEET_NAME,
    INSTAGRAM_SEARCH_IMPRESSIONS_RATE,
    TIKTOK_SEARCH_IMPRESSIONS_RATE,
    YOUTUBE_SEARCH_IMPRESSIONS_RATE,
)

logger = logging.getLogger(__name__)

VIDEO_ANALYSIS_WORKSHEET_NAME = "Video Analysis"
MARKETING_FUNNELS_WORKSHEET_NAME = GOOGLE_SHEET_WORKSHEET_NAME or "Marketing Funnels"

VIDEO_ANALYSIS_COLUMNS = [
    "Recorded At",
    "Platform",
    "Video Title",
    "Posted At",
    "Content Type",
    "Views",
    "Comments",
    "Shares",
    "Retention",
    "Avg Watch Time",
    "ER",
    "Score",
    "Verdict",
]

MARKETING_FUNNELS_COLUMNS = [
    "Date",
    "Channel",
    "Store",
    "Views",
    "Search Impressions",
    "Product Page Views",
    "Installs",
    "Purchases",
]

# Backwards-compatibility alias for older tests/imports.
REPORT_COLUMNS = VIDEO_ANALYSIS_COLUMNS

CSV_REQUIRED_COLUMNS = (
    "date",
    "channel",
    "store",
    "search_impressions",
    "product_page_views",
    "installs",
)
CSV_OPTIONAL_COLUMNS = ("views", "purchases")

CHANNELS = (
    "TikTok Viral",
    "YouTube Viral",
    "Instagram Viral",
    "Store Organic",
    "Facebook Ads",
    "Apple Search Ads",
    "TOTAL",
)
STORES = ("App Store", "Google Play")

MAX_RETRIES = 3
RETRY_DELAY_BASE = 2

_sheets_queue: asyncio.Queue[dict[str, Any]] | None = None


def get_sheets_queue() -> asyncio.Queue[dict[str, Any]]:
    global _sheets_queue
    if _sheets_queue is None:
        _sheets_queue = asyncio.Queue()
    return _sheets_queue


def queue_video_analysis_export(video_data: dict[str, Any]) -> None:
    try:
        get_sheets_queue().put_nowait({"kind": "video_analysis", "payload": video_data})
    except asyncio.QueueFull:
        logger.warning("Sheets export queue is full; dropping video export")


def queue_export(video_data: dict[str, Any]) -> None:
    """Backward-compatible alias."""
    queue_video_analysis_export(video_data)


async def sheets_worker() -> None:
    logger.info("Sheets worker started")
    queue = get_sheets_queue()
    while True:
        try:
            item = await queue.get()
            kind = item.get("kind")
            payload = item.get("payload") or {}
            loop = asyncio.get_running_loop()

            if kind == "video_analysis":
                await loop.run_in_executor(None, export_video_to_sheet, payload)
            else:
                logger.warning("Unknown sheets worker item kind: %s", kind)

            queue.task_done()
        except asyncio.CancelledError:
            logger.info("Sheets worker cancelled")
            break
        except Exception:
            logger.exception("Unhandled error in sheets worker")
            await asyncio.sleep(1)


def _get_credentials() -> ServiceAccountCredentials:
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    if GOOGLE_SHEET_CREDENTIALS_PATH:
        creds_path = Path(GOOGLE_SHEET_CREDENTIALS_PATH)
        if creds_path.exists():
            return ServiceAccountCredentials.from_json_keyfile_name(str(creds_path), scope)
        logger.warning("File not found at GOOGLE_SHEET_CREDENTIALS_PATH: %s", creds_path)

    if GOOGLE_CREDENTIALS_JSON:
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
            return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse GOOGLE_CREDENTIALS_JSON: %s", exc)

    raise FileNotFoundError(
        "Google Sheets credentials not found. "
        "Set GOOGLE_SHEET_CREDENTIALS_PATH or GOOGLE_CREDENTIALS_JSON."
    )


def _get_client() -> gspread.Client:
    return gspread.authorize(_get_credentials())


def _open_spreadsheet(client: gspread.Client) -> gspread.Spreadsheet:
    if not GOOGLE_SHEET_ID:
        raise FileNotFoundError("GOOGLE_SHEET_ID is not configured.")
    return client.open_by_key(GOOGLE_SHEET_ID)


def _column_letter(index: int) -> str:
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    headers: list[str],
) -> gspread.Worksheet:
    try:
        worksheet = spreadsheet.worksheet(title)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=max(len(headers), 12),
        )
    _ensure_headers(worksheet, headers)
    return worksheet


def _ensure_headers(worksheet: gspread.Worksheet, headers: list[str]) -> None:
    end_col = _column_letter(len(headers))
    worksheet.update(
        range_name=f"A1:{end_col}1",
        values=[headers],
        value_input_option="USER_ENTERED",
    )


def _normalize_platform(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "tiktok" in text:
        return "TikTok"
    if "youtube" in text:
        return "YouTube"
    if "instagram" in text or "reels" in text:
        return "Instagram"
    return str(value or "-")


def _platform_to_channel(value: Any) -> str | None:
    platform = _normalize_platform(value).lower()
    if platform == "tiktok":
        return "TikTok Viral"
    if platform == "youtube":
        return "YouTube Viral"
    if platform == "instagram":
        return "Instagram Viral"
    return None


def _search_impressions_rate_for_channel(channel: str) -> float:
    return {
        "TikTok Viral": TIKTOK_SEARCH_IMPRESSIONS_RATE,
        "YouTube Viral": YOUTUBE_SEARCH_IMPRESSIONS_RATE,
        "Instagram Viral": INSTAGRAM_SEARCH_IMPRESSIONS_RATE,
    }.get(channel, 0.0)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"^\s*posted\s+on\s+", "", text, flags=re.IGNORECASE)
    try:
        parsed = dateparser.parse(text)
    except Exception:
        parsed = None
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_date_key(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _calculate_age_hours(posted_at: str | None) -> float | None:
    parsed = _parse_datetime(posted_at)
    if not parsed:
        return None
    now = datetime.now(timezone.utc)
    return round((now - parsed).total_seconds() / 3600, 1)


def _format_percentage(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return f"{int(numeric)}%"
    return f"{numeric:.2f}".rstrip("0").rstrip(".") + "%"


def _format_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def _parse_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().replace(" ", "").replace(",", "")
    if not text:
        return 0
    try:
        return int(round(float(text)))
    except (TypeError, ValueError):
        return 0


def _calculate_er(actions: dict[str, Any] | None, views: int | None) -> float | None:
    if not views or views <= 0 or not actions:
        return None
    total = 0.0
    for value in actions.values():
        if isinstance(value, (int, float)):
            total += float(value)
    if total <= 0:
        return None
    return round((total / views) * 100, 2)


def _normalize_verdict_for_sheet(verdict: Any) -> str:
    if verdict is None:
        return ""
    raw = str(verdict).strip()
    if not raw:
        return ""
    normalized = re.sub(r"\s+", " ", raw)
    upper = normalized.upper()
    verdict_order = ("KILL", "FIX", "ITERATE", "SCALE")
    target = next((v for v in verdict_order if v in upper), None)
    if not target:
        return normalized
    emoji_by_target = {
        "KILL": "🔴",
        "FIX": "✂️",
        "ITERATE": "🟡",
        "SCALE": "🚀",
    }
    emoji = emoji_by_target[target]
    parenthesized = re.search(r"\(([^)]*)\)", normalized)
    source = parenthesized.group(1).strip() if parenthesized else normalized
    matched = re.search(rf"\b{target}\b\s*(.*)$", source, flags=re.IGNORECASE)
    tail = matched.group(1).strip() if matched else ""
    tail = re.sub(r"^[-–—:/|]+\s*", "", tail)
    if tail.startswith("(") and tail.endswith(")"):
        tail = tail[1:-1].strip()
    if tail:
        if target == "ITERATE":
            return f"{emoji} {target} ({tail})"
        return f"{emoji} {target} {tail}"
    return f"{emoji} {target}"


def _extract_video_title(video_data: dict[str, Any]) -> str:
    return (
        str(
            video_data.get("video_title")
            or video_data.get("title")
            or video_data.get("hook_text")
            or "-"
        )
    )


def _build_video_analysis_row(video_data: dict[str, Any]) -> list[str]:
    metrics = video_data.get("metrics") or {}
    views = metrics.get("views")
    comments = metrics.get("comments")
    shares = metrics.get("shares")
    posted_at = video_data.get("posted_at")
    recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    retention = metrics.get("retention_3s") or video_data.get("retention_3s")
    avg_watch_time = metrics.get("avg_watch_time_pct") or video_data.get("avg_watch_time_pct")
    er = video_data.get("aggregated_er")
    if er is None:
        er = _calculate_er(
            {
                "likes": metrics.get("likes") or 0,
                "comments": comments or 0,
                "shares": shares or 0,
                "saves": metrics.get("saves") or 0,
            },
            _parse_int(views),
        )

    return [
        recorded_at,
        _normalize_platform(video_data.get("platform")),
        _extract_video_title(video_data),
        re.sub(r"^\s*posted\s+on\s+", "", str(posted_at or ""), flags=re.IGNORECASE).strip(),
        str(video_data.get("content_type") or "-"),
        _format_number(views),
        _format_number(comments),
        _format_number(shares),
        _format_percentage(retention),
        _format_percentage(avg_watch_time),
        _format_percentage(er),
        _format_number(video_data.get("score")),
        _normalize_verdict_for_sheet(video_data.get("verdict")),
    ]


def _build_row(video_data: dict[str, Any]) -> list[str]:
    """Backward-compatible alias used by existing tests/imports."""
    return _build_video_analysis_row(video_data)


def _append_row_with_retry(
    worksheet: gspread.Worksheet,
    row: list[str],
    max_retries: int = MAX_RETRIES,
) -> bool:
    for attempt in range(max_retries):
        try:
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            return True
        except APIError as exc:
            if getattr(exc.response, "status_code", None) == 429:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("Sheets rate limit, retry in %ss", delay)
                time.sleep(delay)
                continue
            logger.exception("Google Sheets API error on append: %s", exc)
        except Exception:
            logger.exception("Unexpected append error")
        if attempt < max_retries - 1:
            time.sleep(RETRY_DELAY_BASE)
    return False


def _worksheet_records(
    worksheet: gspread.Worksheet,
    headers: list[str],
) -> list[dict[str, Any]]:
    values = worksheet.get_all_values()
    if len(values) <= 1:
        return []
    records: list[dict[str, Any]] = []
    for row_index, row in enumerate(values[1:], start=2):
        padded = row + [""] * max(0, len(headers) - len(row))
        record = {"_row_index": row_index}
        for idx, header in enumerate(headers):
            record[header] = padded[idx] if idx < len(padded) else ""
        records.append(record)
    return records


def _marketing_row_key(date_value: str, channel: str, store: str) -> tuple[str, str, str]:
    return date_value.strip(), channel.strip(), store.strip()


def _update_marketing_row(
    worksheet: gspread.Worksheet,
    row_index: int,
    row_data: dict[str, Any],
) -> None:
    values = [[str(row_data.get(column, "")) for column in MARKETING_FUNNELS_COLUMNS]]
    end_col = _column_letter(len(MARKETING_FUNNELS_COLUMNS))
    worksheet.update(
        range_name=f"A{row_index}:{end_col}{row_index}",
        values=values,
        value_input_option="USER_ENTERED",
    )


def _append_marketing_row(
    worksheet: gspread.Worksheet,
    row_data: dict[str, Any],
) -> None:
    worksheet.append_row(
        [str(row_data.get(column, "")) for column in MARKETING_FUNNELS_COLUMNS],
        value_input_option="USER_ENTERED",
    )


def _upsert_marketing_row(
    worksheet: gspread.Worksheet,
    row_data: dict[str, Any],
) -> str:
    records = _worksheet_records(worksheet, MARKETING_FUNNELS_COLUMNS)
    key = _marketing_row_key(row_data["Date"], row_data["Channel"], row_data["Store"])
    for record in records:
        if _marketing_row_key(record["Date"], record["Channel"], record["Store"]) == key:
            merged = {column: record.get(column, "") for column in MARKETING_FUNNELS_COLUMNS}
            for column in MARKETING_FUNNELS_COLUMNS:
                incoming = row_data.get(column, None)
                if incoming is None:
                    continue
                merged[column] = incoming
            _update_marketing_row(worksheet, record["_row_index"], merged)
            return "updated"
    _append_marketing_row(worksheet, row_data)
    return "created"


def _ensure_social_rows(
    worksheet: gspread.Worksheet,
    date_value: str,
    channel: str,
    total_views: int,
) -> None:
    heuristic = round(total_views * _search_impressions_rate_for_channel(channel))
    records = _worksheet_records(worksheet, MARKETING_FUNNELS_COLUMNS)
    keyed = {
        _marketing_row_key(record["Date"], record["Channel"], record["Store"]): record
        for record in records
    }
    for store in STORES:
        key = _marketing_row_key(date_value, channel, store)
        existing = keyed.get(key)
        if existing:
            merged = {column: existing.get(column, "") for column in MARKETING_FUNNELS_COLUMNS}
            merged["Views"] = str(total_views)
            if not str(merged.get("Search Impressions", "")).strip() and heuristic > 0:
                merged["Search Impressions"] = str(heuristic)
            _update_marketing_row(worksheet, existing["_row_index"], merged)
        else:
            _append_marketing_row(
                worksheet,
                {
                    "Date": date_value,
                    "Channel": channel,
                    "Store": store,
                    "Views": str(total_views),
                    "Search Impressions": str(heuristic) if heuristic > 0 else "",
                    "Product Page Views": "",
                    "Installs": "",
                    "Purchases": "",
                },
            )


def _recompute_total_rows(
    worksheet: gspread.Worksheet,
    affected_pairs: set[tuple[str, str]],
) -> None:
    records = _worksheet_records(worksheet, MARKETING_FUNNELS_COLUMNS)
    totals_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    total_row_indexes: dict[tuple[str, str], int] = {}

    for record in records:
        pair = (record["Date"], record["Store"])
        if record["Channel"] == "TOTAL":
            total_row_indexes[pair] = record["_row_index"]
            continue
        if pair not in affected_pairs:
            continue
        bucket = totals_by_pair.setdefault(
            pair,
            {
                "Views": 0,
                "Search Impressions": 0,
                "Product Page Views": 0,
                "Installs": 0,
                "Purchases": 0,
                "has_purchases": False,
            },
        )
        bucket["Views"] += _parse_int(record["Views"])
        bucket["Search Impressions"] += _parse_int(record["Search Impressions"])
        bucket["Product Page Views"] += _parse_int(record["Product Page Views"])
        bucket["Installs"] += _parse_int(record["Installs"])
        if str(record["Purchases"]).strip():
            bucket["has_purchases"] = True
            bucket["Purchases"] += _parse_int(record["Purchases"])

    for pair in affected_pairs:
        date_value, store = pair
        bucket = totals_by_pair.get(pair)
        if not bucket:
            continue
        row_data = {
            "Date": date_value,
            "Channel": "TOTAL",
            "Store": store,
            "Views": str(bucket["Views"]) if bucket["Views"] else "",
            "Search Impressions": str(bucket["Search Impressions"]) if bucket["Search Impressions"] else "",
            "Product Page Views": str(bucket["Product Page Views"]) if bucket["Product Page Views"] else "",
            "Installs": str(bucket["Installs"]) if bucket["Installs"] else "",
            "Purchases": str(bucket["Purchases"]) if bucket["has_purchases"] else "",
        }
        if pair in total_row_indexes:
            _update_marketing_row(worksheet, total_row_indexes[pair], row_data)
        else:
            _append_marketing_row(worksheet, row_data)


def _aggregate_social_views(
    video_worksheet: gspread.Worksheet,
    date_value: str,
    channel: str,
) -> int:
    records = _worksheet_records(video_worksheet, VIDEO_ANALYSIS_COLUMNS)
    total = 0
    for record in records:
        platform_channel = _platform_to_channel(record["Platform"])
        if platform_channel != channel:
            continue
        candidate_date = _normalize_date_key(record["Posted At"]) or _normalize_date_key(record["Recorded At"])
        if candidate_date != date_value:
            continue
        total += _parse_int(record["Views"])
    return total


def _sync_social_views_for_video(
    spreadsheet: gspread.Spreadsheet,
    video_data: dict[str, Any],
) -> None:
    channel = _platform_to_channel(video_data.get("platform"))
    if not channel:
        return
    date_value = _normalize_date_key(video_data.get("posted_at")) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    video_ws = _get_or_create_worksheet(spreadsheet, VIDEO_ANALYSIS_WORKSHEET_NAME, VIDEO_ANALYSIS_COLUMNS)
    funnel_ws = _get_or_create_worksheet(spreadsheet, MARKETING_FUNNELS_WORKSHEET_NAME, MARKETING_FUNNELS_COLUMNS)
    total_views = _aggregate_social_views(video_ws, date_value, channel)
    _ensure_social_rows(funnel_ws, date_value, channel, total_views)
    _recompute_total_rows(funnel_ws, {(date_value, store) for store in STORES})


def export_video_to_sheet(video_data: dict[str, Any]) -> bool:
    if (not GOOGLE_SHEET_CREDENTIALS_PATH and not GOOGLE_CREDENTIALS_JSON) or not GOOGLE_SHEET_ID:
        logger.warning("Google Sheets not configured; skip export")
        return False
    try:
        client = _get_client()
        spreadsheet = _open_spreadsheet(client)
        video_ws = _get_or_create_worksheet(spreadsheet, VIDEO_ANALYSIS_WORKSHEET_NAME, VIDEO_ANALYSIS_COLUMNS)
        row = _build_video_analysis_row(video_data)
        if not _append_row_with_retry(video_ws, row):
            return False
        _sync_social_views_for_video(spreadsheet, video_data)
        logger.info(
            "Exported video analysis to Google Sheets: platform=%s title=%s",
            video_data.get("platform"),
            _extract_video_title(video_data),
        )
        return True
    except FileNotFoundError as exc:
        logger.warning("Sheet export skipped: %s", exc)
        return False
    except Exception:
        logger.exception("Unexpected error during Google Sheets export")
        return False


export_hook_to_sheet = export_video_to_sheet


def validate_normalized_csv_headers(fieldnames: list[str] | None) -> list[str]:
    normalized = {_normalize_csv_field_name(name) for name in (fieldnames or []) if name}
    return [column for column in CSV_REQUIRED_COLUMNS if column not in normalized]


def _normalize_csv_field_name(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _normalize_channel(value: Any) -> str | None:
    text = _normalize_csv_field_name(str(value or ""))
    mapping = {
        "tiktok": "TikTok Viral",
        "tiktok_viral": "TikTok Viral",
        "youtube": "YouTube Viral",
        "youtube_viral": "YouTube Viral",
        "instagram": "Instagram Viral",
        "instagram_viral": "Instagram Viral",
        "store_organic": "Store Organic",
        "organic": "Store Organic",
        "facebook_ads": "Facebook Ads",
        "fb_ads": "Facebook Ads",
        "fb": "Facebook Ads",
        "apple_search_ads": "Apple Search Ads",
        "asa": "Apple Search Ads",
        "total": "TOTAL",
    }
    return mapping.get(text)


def _normalize_store(value: Any) -> str | None:
    text = _normalize_csv_field_name(str(value or ""))
    mapping = {
        "app_store": "App Store",
        "appstore": "App Store",
        "ios": "App Store",
        "google_play": "Google Play",
        "googleplay": "Google Play",
        "android": "Google Play",
    }
    return mapping.get(text)


def _normalize_csv_row(row: dict[str, Any]) -> dict[str, str]:
    normalized = {
        _normalize_csv_field_name(key): ("" if value is None else str(value).strip())
        for key, value in row.items()
        if key is not None
    }
    date_value = _normalize_date_key(normalized.get("date"))
    channel = _normalize_channel(normalized.get("channel"))
    store = _normalize_store(normalized.get("store"))
    if not date_value or not channel or not store:
        raise ValueError("Invalid date/channel/store in CSV row")

    return {
        "Date": date_value,
        "Channel": channel,
        "Store": store,
        "Views": _format_number(_parse_int(normalized.get("views"))) if normalized.get("views", "") != "" else None,
        "Search Impressions": _format_number(_parse_int(normalized.get("search_impressions"))),
        "Product Page Views": _format_number(_parse_int(normalized.get("product_page_views"))),
        "Installs": _format_number(_parse_int(normalized.get("installs"))),
        "Purchases": _format_number(_parse_int(normalized.get("purchases"))) if normalized.get("purchases", "") != "" else None,
    }


def import_marketing_funnel_csv_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if (not GOOGLE_SHEET_CREDENTIALS_PATH and not GOOGLE_CREDENTIALS_JSON) or not GOOGLE_SHEET_ID:
        raise FileNotFoundError("Google Sheets not configured.")

    client = _get_client()
    spreadsheet = _open_spreadsheet(client)
    funnel_ws = _get_or_create_worksheet(
        spreadsheet,
        MARKETING_FUNNELS_WORKSHEET_NAME,
        MARKETING_FUNNELS_COLUMNS,
    )

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    affected_pairs: set[tuple[str, str]] = set()

    for row_number, row in enumerate(rows, start=2):
        try:
            normalized = _normalize_csv_row(row)
            action = _upsert_marketing_row(funnel_ws, normalized)
            if action == "created":
                created += 1
            else:
                updated += 1
            affected_pairs.add((normalized["Date"], normalized["Store"]))
        except Exception as exc:
            skipped += 1
            errors.append(f"Row {row_number}: {exc}")

    if affected_pairs:
        _recompute_total_rows(funnel_ws, affected_pairs)

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "affected_dates": sorted({date_value for date_value, _ in affected_pairs}),
    }


def parse_csv_text(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(content.splitlines())
    return list(reader)
