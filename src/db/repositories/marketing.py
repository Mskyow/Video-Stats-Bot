"""
Repository for daily top-of-funnel channel metrics.

This table is intentionally account/channel-level, not video-level:
marketing analytics needs "how many views did TikTok/Instagram/YouTube bring today",
while creative analytics can still live in the per-video screenshot flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import Client


TABLE_NAME = "channel_daily_metrics"


def upsert_channel_daily_metric(
    supabase: Client,
    *,
    metric_date: str,
    platform: str,
    account_name: str = "total",
    views: int,
    likes: int | None = None,
    comments: int | None = None,
    saves: int | None = None,
    shares: int | None = None,
    source: str = "telegram_text",
    raw_text: str | None = None,
    created_by_telegram_id: int | None = None,
) -> dict[str, Any]:
    payload = {
        "metric_date": metric_date,
        "platform": platform,
        "account_name": account_name or "total",
        "views": int(views),
        "likes": likes,
        "comments": comments,
        "saves": saves,
        "shares": shares,
        "source": source,
        "raw_text": raw_text,
        "created_by_telegram_id": created_by_telegram_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = (
        supabase.table(TABLE_NAME)
        .upsert(payload, on_conflict="metric_date,platform,account_name")
        .execute()
    )
    data = response.data or []
    return data[0] if data else payload


def list_channel_daily_metrics(
    supabase: Client,
    *,
    metric_date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = supabase.table(TABLE_NAME).select("*").order("metric_date", desc=True).limit(limit)
    if metric_date:
        query = query.eq("metric_date", metric_date)
    response = query.execute()
    return list(response.data or [])


def insert_public_video_scrape(
    supabase: Client,
    *,
    platform: str,
    url: str,
    raw_id: str | None = None,
    title: str | None = None,
    uploader: str | None = None,
    upload_date: str | None = None,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    created_by_telegram_id: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload = {
        "platform": platform or "Other",
        "url": url,
        "raw_id": raw_id,
        "title": title,
        "uploader": uploader,
        "upload_date": upload_date,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "created_by_telegram_id": created_by_telegram_id,
        "error": error,
    }
    response = supabase.table("public_video_scrapes").insert(payload).execute()
    data = response.data or []
    return data[0] if data else payload


def upsert_social_video_snapshot(
    supabase: Client,
    *,
    snapshot_date: str,
    platform: str,
    account_name: str,
    video_id: str,
    video_url: str | None = None,
    published_at: str | None = None,
    title: str | None = None,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    saves: int | None = None,
    shares: int | None = None,
    provider: str = "scrapecreators",
    raw_json: dict[str, Any] | None = None,
    account_id: str | None = None,
    run_id: str | None = None,
    page_number: int | None = None,
    position_in_run: int | None = None,
) -> dict[str, Any]:
    payload = {
        "snapshot_date": snapshot_date,
        "platform": platform,
        "account_name": account_name,
        "video_id": video_id,
        "video_url": video_url,
        "published_at": published_at,
        "title": title,
        "views": views,
        "likes": likes,
        "comments": comments,
        "saves": saves,
        "shares": shares,
        "provider": provider,
        "raw_json": raw_json,
        "account_id": account_id,
        "run_id": run_id,
        "page_number": page_number,
        "position_in_run": position_in_run,
    }
    response = (
        supabase.table("social_video_snapshots")
        .upsert(payload, on_conflict="snapshot_date,platform,account_name,video_id,provider")
        .execute()
    )
    data = response.data or []
    return data[0] if data else payload


def list_enabled_social_scrape_accounts(supabase: Client) -> list[dict[str, Any]]:
    response = (
        supabase.table("social_scrape_accounts")
        .select("*")
        .eq("enabled", True)
        .order("platform")
        .order("handle")
        .execute()
    )
    return list(response.data or [])


def create_social_scrape_run(supabase: Client, *, account_id: str) -> dict[str, Any]:
    response = (
        supabase.table("social_scrape_runs")
        .insert({"account_id": account_id, "status": "running"})
        .execute()
    )
    data = response.data or []
    if not data:
        raise RuntimeError("Supabase did not return the created scrape run")
    return data[0]


def finish_social_scrape_run(
    supabase: Client,
    *,
    run_id: str,
    status: str,
    pages_requested: int,
    videos_received: int,
    videos_in_scope: int,
    total_lifetime_views: int | None,
    start_video_found: bool | None,
    raw_pages: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "pages_requested": pages_requested,
        "videos_received": videos_received,
        "videos_in_scope": videos_in_scope,
        "total_lifetime_views": total_lifetime_views,
        "start_video_found": start_video_found,
        "raw_pages": raw_pages,
        "error": error,
    }
    response = (
        supabase.table("social_scrape_runs")
        .update(payload)
        .eq("id", run_id)
        .execute()
    )
    data = response.data or []
    return data[0] if data else {"id": run_id, **payload}


def upsert_social_video_snapshots(
    supabase: Client,
    *,
    snapshot_date: str,
    account_id: str,
    run_id: str,
    videos: list[Any],
) -> list[dict[str, Any]]:
    payloads = [
        {
            "snapshot_date": snapshot_date,
            "platform": item.platform,
            "account_name": item.account_name,
            "video_id": item.video_id,
            "video_url": item.video_url,
            "published_at": item.published_at,
            "title": item.title,
            "views": item.views,
            "likes": item.likes,
            "comments": item.comments,
            "saves": item.saves,
            "shares": item.shares,
            "provider": "scrapecreators",
            "raw_json": item.raw_json,
            "account_id": account_id,
            "run_id": run_id,
            "page_number": item.page_number,
            "position_in_run": item.position_in_run,
        }
        for item in videos
    ]
    if not payloads:
        return []
    response = (
        supabase.table("social_video_snapshots")
        .upsert(
            payloads,
            on_conflict="snapshot_date,platform,account_name,video_id,provider",
        )
        .execute()
    )
    return list(response.data or payloads)


def list_latest_previous_video_snapshots(
    supabase: Client,
    *,
    platform: str,
    account_name: str,
    before_date: str,
    provider: str = "scrapecreators",
    limit: int = 1000,
) -> dict[str, dict[str, Any]]:
    response = (
        supabase.table("social_video_snapshots")
        .select("*")
        .eq("platform", platform)
        .eq("account_name", account_name)
        .eq("provider", provider)
        .lt("snapshot_date", before_date)
        .order("snapshot_date", desc=True)
        .limit(limit)
        .execute()
    )
    latest_by_video: dict[str, dict[str, Any]] = {}
    for row in response.data or []:
        video_id = str(row.get("video_id") or "")
        if video_id and video_id not in latest_by_video:
            latest_by_video[video_id] = row
    return latest_by_video
