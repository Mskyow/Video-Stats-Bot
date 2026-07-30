"""Collect and analyze comments for videos that recently crossed 50K views."""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from supabase import Client

from src import config
from src.services.comment_analysis_service import analyze_comments
from src.services.scrapecreators_service import ScrapeCreatorsClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EligibleVideo:
    platform: str
    account_name: str
    video_id: str
    video_url: str
    first_threshold_date: date
    source_comment_count: int | None


def _utc_start(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat()


def _threshold_videos(supabase: Client) -> list[EligibleVideo]:
    """Find the first stored day each video met the configured view threshold."""
    response = (
        supabase.table("social_video_snapshots")
        .select(
            "platform,account_name,video_id,video_url,snapshot_date,"
            "scraped_at,views,comments"
        )
        .gte("views", config.CONTENT_COMMENT_VIEW_THRESHOLD)
        .order("snapshot_date")
        .order("scraped_at")
        .execute()
    )
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in response.data or []:
        grouped[
            (
                str(row.get("platform") or ""),
                str(row.get("account_name") or ""),
                str(row.get("video_id") or ""),
            )
        ].append(row)

    eligible: list[EligibleVideo] = []
    for (platform, account_name, video_id), rows in grouped.items():
        if not platform or not account_name or not video_id:
            continue
        first_date = date.fromisoformat(str(rows[0]["snapshot_date"])[:10])
        latest = rows[-1]
        video_url = str(latest.get("video_url") or "").strip()
        if not video_url:
            continue
        source_comments = latest.get("comments")
        eligible.append(
            EligibleVideo(
                platform=platform,
                account_name=account_name,
                video_id=video_id,
                video_url=video_url,
                first_threshold_date=first_date,
                source_comment_count=(
                    int(source_comments) if source_comments is not None else None
                ),
            )
        )
    return eligible


def _existing_comments(
    supabase: Client,
    video: EligibleVideo,
) -> dict[str, dict[str, Any]]:
    response = (
        supabase.table("social_video_comments")
        .select("comment_id,content_hash,first_seen_at")
        .eq("platform", video.platform)
        .eq("account_name", video.account_name)
        .eq("video_id", video.video_id)
        .execute()
    )
    return {
        str(item["comment_id"]): item
        for item in response.data or []
        if item.get("comment_id")
    }


def _stored_comment_texts(
    supabase: Client,
    video: EligibleVideo,
) -> tuple[list[str], list[str]]:
    response = (
        supabase.table("social_video_comments")
        .select("comment_id,comment_text,comment_published_at,first_seen_at")
        .eq("platform", video.platform)
        .eq("account_name", video.account_name)
        .eq("video_id", video.video_id)
        .order("comment_published_at")
        .order("first_seen_at")
        .limit(config.CONTENT_COMMENT_MAX_ANALYZED)
        .execute()
    )
    rows = list(response.data or [])
    return (
        [str(item.get("comment_text") or "") for item in rows],
        [str(item.get("comment_id") or "") for item in rows],
    )


def _upsert_tracking(
    supabase: Client,
    video: EligibleVideo,
    *,
    now: datetime,
) -> None:
    tracking_end = video.first_threshold_date + timedelta(
        days=config.CONTENT_COMMENT_TRACKING_DAYS
    )
    supabase.table("social_video_comment_tracking").upsert(
        {
            "platform": video.platform,
            "account_name": video.account_name,
            "video_id": video.video_id,
            "tracking_started_at": _utc_start(video.first_threshold_date),
            "tracking_ends_at": _utc_start(tracking_end),
            "source_comment_count": video.source_comment_count,
            "updated_at": now.isoformat(),
        },
        on_conflict="platform,account_name,video_id",
    ).execute()


def _save_comments(
    supabase: Client,
    video: EligibleVideo,
    comments: list[Any],
    *,
    now: datetime,
) -> int:
    existing = _existing_comments(supabase, video)
    changed = 0
    payload: list[dict[str, Any]] = []
    for comment in comments:
        content_hash = hashlib.sha256(comment.text.encode("utf-8")).hexdigest()
        previous = existing.get(comment.comment_id)
        if not previous or previous.get("content_hash") != content_hash:
            changed += 1
        payload.append(
            {
                "platform": video.platform,
                "account_name": video.account_name,
                "video_id": video.video_id,
                "comment_id": comment.comment_id,
                "comment_text": comment.text,
                "content_hash": content_hash,
                "comment_published_at": comment.published_at,
                "first_seen_at": (
                    previous.get("first_seen_at") if previous else now.isoformat()
                ),
                "last_seen_at": now.isoformat(),
            }
        )
    for start in range(0, len(payload), 500):
        supabase.table("social_video_comments").upsert(
            payload[start : start + 500],
            on_conflict="platform,account_name,video_id,comment_id",
        ).execute()
    return changed


def _mark_analyzed(
    supabase: Client,
    video: EligibleVideo,
    comment_ids: list[str],
    *,
    analyzed_at: str,
) -> None:
    for start in range(0, len(comment_ids), 200):
        ids = [item for item in comment_ids[start : start + 200] if item]
        if not ids:
            continue
        (
            supabase.table("social_video_comments")
            .update({"analyzed_at": analyzed_at})
            .eq("platform", video.platform)
            .eq("account_name", video.account_name)
            .eq("video_id", video.video_id)
            .in_("comment_id", ids)
            .execute()
        )


def sync_eligible_video_comments(
    supabase: Client,
    *,
    now: datetime | None = None,
    client: ScrapeCreatorsClient | None = None,
) -> dict[str, Any]:
    """Run one idempotent daily collection for each video in its five-day window."""
    current = now or datetime.now(timezone.utc)
    today = current.date()
    scraper = client or ScrapeCreatorsClient()
    videos = _threshold_videos(supabase)
    statuses: list[dict[str, Any]] = []

    for video in videos:
        _upsert_tracking(supabase, video, now=current)
        exclusive_end = video.first_threshold_date + timedelta(
            days=config.CONTENT_COMMENT_TRACKING_DAYS
        )
        if today < video.first_threshold_date or today >= exclusive_end:
            statuses.append(
                {
                    "video_id": video.video_id,
                    "status": "outside_tracking_window",
                }
            )
            continue

        tracking_response = (
            supabase.table("social_video_comment_tracking")
            .select("last_comments_collected_at,collection_attempts")
            .eq("platform", video.platform)
            .eq("account_name", video.account_name)
            .eq("video_id", video.video_id)
            .limit(1)
            .execute()
        )
        tracking = (tracking_response.data or [{}])[0]
        last_collected = str(tracking.get("last_comments_collected_at") or "")
        if last_collected[:10] == today.isoformat():
            statuses.append(
                {"video_id": video.video_id, "status": "already_collected_today"}
            )
            continue

        attempts = int(tracking.get("collection_attempts") or 0) + 1
        try:
            fetched = scraper.fetch_video_comments(
                video.platform,
                video.video_url,
                max_comments=config.CONTENT_COMMENT_MAX_ANALYZED,
            )
            changed = _save_comments(
                supabase,
                video,
                fetched.comments,
                now=current,
            )
            texts, comment_ids = _stored_comment_texts(supabase, video)
            analysis = analyze_comments(texts)
            analyzed_at = current.isoformat()
            _mark_analyzed(
                supabase,
                video,
                comment_ids,
                analyzed_at=analyzed_at,
            )
            (
                supabase.table("social_video_comment_tracking")
                .update(
                    {
                        "last_comments_collected_at": analyzed_at,
                        "source_comment_count": (
                            fetched.source_total
                            if fetched.source_total is not None
                            else video.source_comment_count
                        ),
                        "top_level_comments_collected": len(fetched.comments),
                        "comments_analyzed": analysis.comments_analyzed,
                        "app_questions_present": analysis.app_questions_present,
                        "app_questions_count": analysis.app_questions_count,
                        "ai_comment_summary": analysis.ai_comment_summary,
                        "analysis_model": analysis.model,
                        "analysis_version": analysis.version,
                        "last_analysis_at": analyzed_at,
                        "collection_attempts": attempts,
                        "last_error": None,
                        "updated_at": analyzed_at,
                    }
                )
                .eq("platform", video.platform)
                .eq("account_name", video.account_name)
                .eq("video_id", video.video_id)
                .execute()
            )
            statuses.append(
                {
                    "video_id": video.video_id,
                    "status": "collected",
                    "comments": len(fetched.comments),
                    "changed": changed,
                    "analyzed": analysis.comments_analyzed,
                    "credits": fetched.credits_charged,
                    "truncated": fetched.truncated,
                }
            )
        except Exception as exc:
            logger.exception(
                "Comment collection failed for %s/%s/%s",
                video.platform,
                video.account_name,
                video.video_id,
            )
            (
                supabase.table("social_video_comment_tracking")
                .update(
                    {
                        "collection_attempts": attempts,
                        "last_error": str(exc)[:1000],
                        "updated_at": current.isoformat(),
                    }
                )
                .eq("platform", video.platform)
                .eq("account_name", video.account_name)
                .eq("video_id", video.video_id)
                .execute()
            )
            statuses.append(
                {
                    "video_id": video.video_id,
                    "status": "error",
                    "error": str(exc),
                }
            )

    return {
        "threshold_videos": len(videos),
        "results": statuses,
    }
