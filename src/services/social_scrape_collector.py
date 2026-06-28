"""Persist configured Instagram/TikTok account snapshots to Supabase."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from supabase import Client

from src import config
from src.db.repositories.marketing import (
    create_social_scrape_run,
    finish_social_scrape_run,
    list_enabled_social_scrape_accounts,
    upsert_social_video_snapshots,
)
from src.services.scrapecreators_service import ScrapeCreatorsClient


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
    error: str | None = None


def collect_social_account(
    supabase: Client,
    account: dict[str, Any],
    *,
    client: ScrapeCreatorsClient | None = None,
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

        snapshot_date = datetime.now(timezone.utc).date().isoformat()
        saved = upsert_social_video_snapshots(
            supabase,
            snapshot_date=snapshot_date,
            account_id=str(account["id"]),
            run_id=run_id,
            videos=fetched.videos,
        )
        total_views = sum(item.views or 0 for item in fetched.videos)
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


def collect_configured_social_accounts(supabase: Client) -> list[SocialScrapeResult]:
    results: list[SocialScrapeResult] = []
    for account in list_enabled_social_scrape_accounts(supabase):
        try:
            results.append(collect_social_account(supabase, account))
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
