"""Persist configured Instagram/TikTok account snapshots to Supabase."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

from supabase import Client

from src import config
from src.db.repositories.marketing import (
    create_social_scrape_run,
    finish_social_scrape_run,
    get_previous_account_snapshot,
    list_enabled_social_scrape_accounts,
    upsert_channel_daily_metric,
    upsert_social_video_snapshots,
)
from src.services.scrapecreators_service import ScrapeCreatorsClient, SocialVideoMetric


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SocialScrapeResult:
    platform: str
    handle: str
    display_name: str
    status: str
    pages_requested: int
    videos_received: int
    videos_saved: int
    total_lifetime_views: int
    start_video_found: bool | None
    daily_metric: dict[str, Any] | None = None
    error: str | None = None


def _interval_hours(previous_scraped_at: str | None, current_scraped_at: datetime) -> float | None:
    if not previous_scraped_at:
        return None
    previous_time = datetime.fromisoformat(previous_scraped_at.replace("Z", "+00:00"))
    return (current_scraped_at - previous_time).total_seconds() / 3600


def _should_emit_daily_metric(
    *,
    snapshot_date: str,
    previous_date: str | None,
    previous_scraped_at: str | None,
    current_scraped_at: datetime,
) -> tuple[bool, str | None]:
    expected_previous_date = (date.fromisoformat(snapshot_date) - timedelta(days=1)).isoformat()
    if previous_date != expected_previous_date:
        return False, f"previous_date_mismatch:{previous_date}->{expected_previous_date}"

    interval_hours = _interval_hours(previous_scraped_at, current_scraped_at)
    if interval_hours is None:
        return False, "missing_previous_scraped_at"

    if interval_hours < config.SOCIAL_SCRAPE_INTERVAL_MIN_HOURS:
        return False, f"interval_too_short:{interval_hours:.2f}h"
    if interval_hours > config.SOCIAL_SCRAPE_INTERVAL_MAX_HOURS:
        return False, f"interval_too_long:{interval_hours:.2f}h"
    return True, None


def calculate_account_daily_views(
    current: list[SocialVideoMetric],
    previous_by_video: dict[str, dict[str, Any]],
    *,
    previous_scraped_at: str | None,
) -> tuple[int, int, int]:
    previous_time = None
    if previous_scraped_at:
        previous_time = datetime.fromisoformat(previous_scraped_at.replace("Z", "+00:00"))

    total = 0
    matched = 0
    new_videos = 0
    for item in current:
        if item.views is None:
            continue
        previous = previous_by_video.get(item.video_id)
        if previous and previous.get("views") is not None:
            total += max(0, int(item.views) - int(previous["views"]))
            matched += 1
            continue

        if previous_time and item.published_at:
            published = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            if published >= previous_time:
                total += max(0, int(item.views))
                new_videos += 1
    return total, matched, new_videos


def collect_social_account(
    supabase: Client,
    account: dict[str, Any],
    *,
    client: ScrapeCreatorsClient | None = None,
    calculate_daily: bool = True,
) -> SocialScrapeResult:
    platform = str(account["platform"])
    handle = str(account["handle"]).removeprefix("@")
    display_name = str(account.get("display_name") or handle)
    run = create_social_scrape_run(supabase, account_id=str(account["id"]))
    run_id = str(run["id"])
    api_client = client or ScrapeCreatorsClient()

    try:
        fetched = api_client.fetch_account_since(
            platform,
            handle,
            start_video_id=account.get("start_video_id"),
            start_published_at=account.get("start_published_at"),
            max_pages=config.SOCIAL_SCRAPE_MAX_PAGES,
        )
        if fetched.max_pages_reached:
            raise RuntimeError(
                f"Reached SOCIAL_SCRAPE_MAX_PAGES={config.SOCIAL_SCRAPE_MAX_PAGES} "
                "before reaching the configured start video"
            )
        if account.get("start_video_id") and not fetched.start_video_found:
            logger.warning(
                "Configured start video was not found for %s @%s; date cutoff was used",
                platform,
                handle,
            )

        current_scraped_at = datetime.now(timezone.utc)
        snapshot_date = (
            current_scraped_at
            .astimezone(ZoneInfo(config.SOCIAL_SCRAPE_TIMEZONE))
            .date()
            .isoformat()
        )
        previous_date, previous_scraped_at, previous_by_video = get_previous_account_snapshot(
            supabase,
            platform=fetched.platform,
            account_name=fetched.account_name,
            before_date=snapshot_date,
        )
        saved = upsert_social_video_snapshots(
            supabase,
            snapshot_date=snapshot_date,
            account_id=str(account["id"]),
            run_id=run_id,
            videos=fetched.videos,
        )
        total_views = sum(item.views or 0 for item in fetched.videos)
        daily_metric = None
        should_emit_metric, skip_reason = _should_emit_daily_metric(
            snapshot_date=snapshot_date,
            previous_date=previous_date,
            previous_scraped_at=previous_scraped_at,
            current_scraped_at=current_scraped_at,
        )
        if calculate_daily and should_emit_metric and previous_by_video:
            daily_views, matched, new_videos = calculate_account_daily_views(
                fetched.videos,
                previous_by_video,
                previous_scraped_at=previous_scraped_at,
            )
            metric_date = (
                date.fromisoformat(snapshot_date) - timedelta(days=config.SOCIAL_SCRAPE_METRIC_LAG_DAYS)
            ).isoformat()
            daily_metric = upsert_channel_daily_metric(
                supabase,
                metric_date=metric_date,
                platform=fetched.platform,
                account_name=fetched.account_name,
                views=daily_views,
                source="scrapecreators_delta",
                raw_text=(
                    f"interval={previous_date}->{snapshot_date}; "
                    f"matched_videos={matched}; new_videos={new_videos}"
                ),
            )
        elif calculate_daily and skip_reason:
            logger.info(
                "Skipping daily metric for %s @%s: %s",
                platform,
                handle,
                skip_reason,
            )
        finish_social_scrape_run(
            supabase,
            run_id=run_id,
            status="success",
            pages_requested=len(fetched.raw_pages),
            videos_received=fetched.videos_received,
            videos_in_scope=len(fetched.videos),
            total_lifetime_views=total_views,
            start_video_found=fetched.start_video_found,
            raw_pages=fetched.raw_pages,
        )
        return SocialScrapeResult(
            platform=platform,
            handle=handle,
            display_name=display_name,
            status="success",
            pages_requested=len(fetched.raw_pages),
            videos_received=fetched.videos_received,
            videos_saved=len(saved),
            total_lifetime_views=total_views,
            start_video_found=fetched.start_video_found,
            daily_metric=daily_metric,
        )
    except Exception as exc:
        error = str(exc)[:2000]
        try:
            finish_social_scrape_run(
                supabase,
                run_id=run_id,
                status="failed",
                pages_requested=0,
                videos_received=0,
                videos_in_scope=0,
                total_lifetime_views=None,
                start_video_found=None,
                error=error,
            )
        except Exception:
            logger.exception("Could not mark social scrape run %s as failed", run_id)
        raise


def collect_configured_social_accounts(
    supabase: Client,
    *,
    calculate_daily: bool = True,
) -> list[SocialScrapeResult]:
    results: list[SocialScrapeResult] = []
    for account in list_enabled_social_scrape_accounts(supabase):
        try:
            results.append(
                collect_social_account(
                    supabase,
                    account,
                    calculate_daily=calculate_daily,
                )
            )
        except Exception as exc:
            logger.exception(
                "Social scrape failed for %s @%s",
                account.get("platform"),
                account.get("handle"),
            )
            results.append(
                SocialScrapeResult(
                    platform=str(account.get("platform") or "Unknown"),
                    handle=str(account.get("handle") or "unknown"),
                    display_name=str(account.get("display_name") or account.get("handle") or "unknown"),
                    status="failed",
                    pages_requested=0,
                    videos_received=0,
                    videos_saved=0,
                    total_lifetime_views=0,
                    start_video_found=None,
                    error=str(exc)[:500],
                )
            )
    return results
