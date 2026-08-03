"""Load the Otty content plan and persist deterministic per-video format matches."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from supabase import Client

from src import config
from src.services.content_performance_service import list_content_performance_rows
from src.services.sheets_service import _get_client


EXCLUDED_FORMAT_ACCOUNTS = {"otty.and.lotty"}
MANUAL_OVERRIDE_STATUS_PREFIX = "Requires review: manual override"

COUNTRY_ALIASES = {
    "сша": "USA",
    "usa": "USA",
    "united states": "USA",
    "великобритания": "United Kingdom",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "франция": "France",
    "france": "France",
    "аргентина": "Argentina",
    "argentina": "Argentina",
}


@dataclass(frozen=True)
class FormatScheduleItem:
    format_id: int
    format_name: str
    posting_date: str
    occurrence_index: int
    source_row: int
    source_url: str
    raw_publish_scope: str
    allowed_pairs: frozenset[tuple[str, str]] | None
    scope_valid: bool


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _normalize_platform(value: Any) -> str | None:
    text = _plain(value)
    if text in {"tiktok", "tik tok", "tt", "тикток"}:
        return "TikTok"
    if text in {"instagram", "insta", "ig", "инстаграм", "инст"}:
        return "Instagram"
    return None


def _normalize_country(value: Any) -> str | None:
    return COUNTRY_ALIASES.get(_plain(value))


def _parse_publish_scope(
    raw_scope: str,
) -> tuple[frozenset[tuple[str, str]] | None, bool]:
    """Return None for universal scope, otherwise normalized platform/country pairs."""
    if not raw_scope.strip():
        return None, True

    pairs: set[tuple[str, str]] = set()
    for segment in raw_scope.split(";"):
        if not segment.strip():
            continue
        if ":" not in segment:
            return frozenset(), False
        platform_text, countries_text = segment.split(":", 1)
        platform = _normalize_platform(platform_text)
        if not platform:
            return frozenset(), False
        for country_text in countries_text.split(","):
            country = _normalize_country(country_text)
            if not country:
                return frozenset(), False
            pairs.add((platform, country))
    return frozenset(pairs), bool(pairs)


def _source_year(added_at: str, fallback_year: int) -> int:
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", added_at)
    return int(match.group(1)) if match else fallback_year


def _posting_dates(raw_date: str, *, year: int) -> list[str]:
    dates: list[str] = []
    for day_text, month_text, explicit_year in re.findall(
        r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?",
        raw_date,
    ):
        parsed_year = int(explicit_year) if explicit_year else year
        if parsed_year < 100:
            parsed_year += 2000
        try:
            parsed = datetime(parsed_year, int(month_text), int(day_text))
        except ValueError:
            continue
        dates.append(parsed.date().isoformat())
    return dates


def load_otty_format_schedule() -> list[FormatScheduleItem]:
    """Read and normalize dated format occurrences from the configured content plan."""
    book = _get_client().open_by_key(config.CONTENT_FORMATS_SHEET_ID)
    worksheet = book.get_worksheet_by_id(config.CONTENT_FORMATS_WORKSHEET_GID)
    values = worksheet.get_all_values()
    if not values:
        return []

    current_year = datetime.now(timezone.utc).year
    schedule: list[FormatScheduleItem] = []
    for source_row, raw_row in enumerate(values[1:], start=2):
        row = list(raw_row) + [""] * max(0, 17 - len(raw_row))
        format_id_text = row[1].strip()
        if not format_id_text.isdigit():
            continue
        brief = row[2].strip()
        format_name = next(
            (line.strip() for line in brief.splitlines() if line.strip()),
            f"Format {format_id_text}",
        )
        raw_date = row[4].strip()
        dates = _posting_dates(
            raw_date,
            year=_source_year(row[0].strip(), current_year),
        )
        if not dates:
            continue
        raw_scope = row[6].strip()
        allowed_pairs, scope_valid = _parse_publish_scope(raw_scope)
        source_url = (
            f"https://docs.google.com/spreadsheets/d/{config.CONTENT_FORMATS_SHEET_ID}"
            f"/edit#gid={config.CONTENT_FORMATS_WORKSHEET_GID}&range=A{source_row}"
        )
        for occurrence_index, posting_date in enumerate(dates, start=1):
            schedule.append(
                FormatScheduleItem(
                    format_id=int(format_id_text),
                    format_name=format_name,
                    posting_date=posting_date,
                    occurrence_index=occurrence_index,
                    source_row=source_row,
                    source_url=source_url,
                    raw_publish_scope=raw_scope,
                    allowed_pairs=allowed_pairs,
                    scope_valid=scope_valid,
                )
            )
    return schedule


def _clean_account(value: Any) -> str:
    return str(value or "").strip().removeprefix("@").casefold()


def _is_manual_override(value: Any) -> bool:
    return str(value or "").startswith(MANUAL_OVERRIDE_STATUS_PREFIX)


def _local_post_date(value: Any) -> str | None:
    """Return the planned-content date for a publication timestamp.

    A video published from midnight through 04:00 Minsk time is treated as
    part of the preceding publishing day. This keeps late-night posts matched
    to the format that was planned for the prior day.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    localized = parsed.astimezone(ZoneInfo(config.CONTENT_PERFORMANCE_TIMEZONE))
    if localized.hour < 4 or (localized.hour == 4 and localized.minute == 0 and localized.second == 0):
        return (localized - timedelta(days=1)).date().isoformat()
    return localized.date().isoformat()


def build_format_assignments(
    rows: list[dict[str, Any]],
    schedule: list[FormatScheduleItem],
) -> list[dict[str, Any]]:
    """Apply the agreed count-and-order algorithm without relying on sheet row position."""
    schedule_by_date: dict[str, list[FormatScheduleItem]] = defaultdict(list)
    for item in schedule:
        schedule_by_date[item.posting_date].append(item)

    grouped_videos: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        post_date = _local_post_date(row.get("published_at"))
        if not post_date:
            continue
        grouped_videos[
            (
                str(row.get("platform") or ""),
                str(row.get("account_name") or ""),
                post_date,
            )
        ].append(row)

    now = datetime.now(timezone.utc).isoformat()
    assignments: list[dict[str, Any]] = []
    for (platform, account_name, post_date), videos in grouped_videos.items():
        videos.sort(key=lambda item: str(item.get("published_at") or ""))
        country = str(videos[0].get("country") or "").strip() or None
        base = {
            "platform": platform,
            "account_name": account_name,
            "country": country,
            "source_post_date": post_date,
            "format_id": None,
            "format_name": None,
            "format_source": None,
            "format_source_row": None,
            "format_occurrence_index": None,
            "raw_publish_scope": None,
            "matched_at": now,
            "updated_at": now,
        }

        if _clean_account(account_name) in EXCLUDED_FORMAT_ACCOUNTS:
            for video in videos:
                assignments.append(
                    {
                        **base,
                        "video_id": str(video["video_id"]),
                        "format_match_status": "Excluded account",
                    }
                )
            continue

        date_formats = schedule_by_date.get(post_date, [])
        invalid_scope = any(not item.scope_valid for item in date_formats)
        if invalid_scope:
            for video in videos:
                assignments.append(
                    {
                        **base,
                        "video_id": str(video["video_id"]),
                        "format_match_status": "Requires review: invalid publish scope",
                    }
                )
            continue

        eligible = [
            item
            for item in date_formats
            if item.allowed_pairs is None
            or (country is not None and (platform, country) in item.allowed_pairs)
        ]
        eligible.sort(key=lambda item: (item.format_id, item.occurrence_index))

        if not date_formats:
            status = "No format scheduled"
        elif not country:
            status = "Requires review: unknown account country"
        elif not eligible:
            status = "Requires review: no eligible format"
        elif len(eligible) != len(videos):
            status = "Requires review: count mismatch"
        else:
            status = "Matched"

        if status != "Matched":
            for video in videos:
                assignments.append(
                    {
                        **base,
                        "video_id": str(video["video_id"]),
                        "format_match_status": status,
                    }
                )
            continue

        for video, format_item in zip(videos, eligible, strict=True):
            assignments.append(
                {
                    **base,
                    "video_id": str(video["video_id"]),
                    "format_id": format_item.format_id,
                    "format_name": format_item.format_name,
                    "format_source": format_item.source_url,
                    "format_source_row": format_item.source_row,
                    "format_occurrence_index": format_item.occurrence_index,
                    "format_match_status": "Matched",
                    "raw_publish_scope": format_item.raw_publish_scope,
                }
            )
    return assignments


def sync_content_format_assignments(
    supabase: Client,
    *,
    rows: list[dict[str, Any]] | None = None,
    lookback_days: int = 7,
) -> dict[str, Any]:
    performance_rows = rows or list_content_performance_rows(
        supabase,
        lookback_days=lookback_days,
    )
    manual_rows = (
        supabase.table("social_video_format_assignments")
        .select("platform,account_name,video_id,format_match_status")
        .like("format_match_status", f"{MANUAL_OVERRIDE_STATUS_PREFIX}%")
        .execute()
        .data
        or []
    )
    manual_keys = {
        (str(item["platform"]), str(item["account_name"]), str(item["video_id"]))
        for item in manual_rows
        if _is_manual_override(item.get("format_match_status"))
    }
    performance_rows = [
        row
        for row in performance_rows
        if (
            str(row.get("platform") or ""),
            str(row.get("account_name") or ""),
            str(row.get("video_id") or ""),
        )
        not in manual_keys
    ]
    schedule = load_otty_format_schedule()
    assignments = build_format_assignments(performance_rows, schedule)
    if assignments:
        supabase.table("social_video_format_assignments").upsert(
            assignments,
            on_conflict="platform,account_name,video_id",
        ).execute()
    statuses = Counter(item["format_match_status"] for item in assignments)
    return {
        "formats_loaded": len(schedule),
        "videos_processed": len(assignments),
        "statuses": dict(statuses),
    }
