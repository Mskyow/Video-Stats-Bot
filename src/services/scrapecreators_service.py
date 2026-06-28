"""
ScrapeCreators integration for public TikTok / Instagram snapshots.

The API returns current public lifetime metrics per video. Daily metrics are
therefore calculated by our code as deltas between saved snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from src.config import SCRAPECREATORS_API_KEY, SCRAPECREATORS_TIMEOUT_SEC


BASE_URL = "https://api.scrapecreators.com"


class ScrapeCreatorsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SocialVideoMetric:
    platform: str
    account_name: str
    video_id: str
    video_url: str | None
    published_at: str | None
    title: str | None
    views: int | None
    likes: int | None
    comments: int | None
    saves: int | None
    shares: int | None
    raw_json: dict[str, Any]
    page_number: int = 1
    position_in_run: int = 1


@dataclass(frozen=True)
class SocialAccountFetch:
    platform: str
    account_name: str
    videos: list[SocialVideoMetric]
    raw_pages: list[dict[str, Any]]
    videos_received: int
    start_video_found: bool | None
    has_more: bool
    max_pages_reached: bool


def _clean_handle(handle: str) -> str:
    return handle.strip().removeprefix("@")


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(value: Any) -> str | None:
    timestamp = _to_int(value)
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _instagram_caption_text(item: dict[str, Any]) -> str | None:
    caption = item.get("caption")
    if isinstance(caption, dict):
        text = caption.get("text")
        if text:
            return str(text)
    return None


class ScrapeCreatorsClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or SCRAPECREATORS_API_KEY
        if not self.api_key:
            raise ScrapeCreatorsError("SCRAPECREATORS_API_KEY is not configured")

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(
            BASE_URL + path,
            headers={"x-api-key": self.api_key},
            params=params,
            timeout=SCRAPECREATORS_TIMEOUT_SEC,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise ScrapeCreatorsError(f"Non-JSON response: HTTP {response.status_code}") from exc

        if response.status_code >= 400:
            message = data.get("message") or data.get("error") or str(data)[:300]
            raise ScrapeCreatorsError(f"HTTP {response.status_code}: {message}")
        return data

    @staticmethod
    def _instagram_metric(
        item: dict[str, Any],
        clean_handle: str,
        *,
        page_number: int = 1,
        position_in_run: int = 1,
    ) -> SocialVideoMetric | None:
        video_id = str(item.get("id") or item.get("code") or "")
        if not video_id:
            return None
        return SocialVideoMetric(
            platform="Instagram",
            account_name="@" + clean_handle,
            video_id=video_id,
            video_url=item.get("url")
            or (f"https://www.instagram.com/p/{item.get('code')}/" if item.get("code") else None),
            published_at=_epoch_to_iso(item.get("taken_at")),
            title=_instagram_caption_text(item),
            views=_to_int(item.get("ig_play_count") or item.get("play_count")),
            likes=_to_int(item.get("like_count")),
            comments=_to_int(item.get("comment_count")),
            saves=None,
            shares=None,
            raw_json=item,
            page_number=page_number,
            position_in_run=position_in_run,
        )

    def fetch_instagram_posts(self, handle: str) -> list[SocialVideoMetric]:
        clean = _clean_handle(handle)
        data = self._get("/v2/instagram/user/posts", {"handle": clean, "trim": "true"})
        metrics: list[SocialVideoMetric] = []
        for position, item in enumerate(data.get("items") or [], 1):
            if isinstance(item, dict):
                metric = self._instagram_metric(item, clean, position_in_run=position)
                if metric:
                    metrics.append(metric)
        return metrics

    @staticmethod
    def _tiktok_metric(
        item: dict[str, Any],
        clean_handle: str,
        *,
        page_number: int = 1,
        position_in_run: int = 1,
    ) -> SocialVideoMetric | None:
        video_id = str(item.get("aweme_id") or "")
        if not video_id:
            return None
        statistics = item.get("statistics") or {}
        return SocialVideoMetric(
            platform="TikTok",
            account_name="@" + clean_handle,
            video_id=video_id,
            video_url=item.get("share_url")
            or item.get("url")
            or f"https://www.tiktok.com/@{clean_handle}/video/{video_id}",
            published_at=_epoch_to_iso(item.get("create_time")),
            title=item.get("desc"),
            views=_to_int(statistics.get("play_count")),
            likes=_to_int(statistics.get("digg_count")),
            comments=_to_int(statistics.get("comment_count")),
            saves=_to_int(statistics.get("collect_count")),
            shares=_to_int(statistics.get("share_count")),
            raw_json=item,
            page_number=page_number,
            position_in_run=position_in_run,
        )

    def fetch_tiktok_videos(self, handle: str) -> list[SocialVideoMetric]:
        clean = _clean_handle(handle)
        data = self._get(
            "/v3/tiktok/profile/videos",
            {"handle": clean, "sort_by": "latest", "trim": "true"},
        )
        metrics: list[SocialVideoMetric] = []
        for position, item in enumerate(data.get("aweme_list") or [], 1):
            if isinstance(item, dict):
                metric = self._tiktok_metric(item, clean, position_in_run=position)
                if metric:
                    metrics.append(metric)
        return metrics

    def fetch_account_since(
        self,
        platform: str,
        handle: str,
        *,
        start_video_id: str | None = None,
        start_published_at: str | None = None,
        max_pages: int = 20,
    ) -> SocialAccountFetch:
        """Fetch newest-to-oldest pages until the configured first relevant video."""
        normalized = platform.strip().lower()
        clean = _clean_handle(handle)
        is_instagram = normalized in {"instagram", "insta", "ig"}
        is_tiktok = normalized in {"tiktok", "tik tok", "tt"}
        if not (is_instagram or is_tiktok):
            raise ScrapeCreatorsError(f"Unsupported platform: {platform}")

        cutoff = None
        if start_published_at:
            cutoff = datetime.fromisoformat(start_published_at.replace("Z", "+00:00"))
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)

        videos: list[SocialVideoMetric] = []
        raw_pages: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        videos_received = 0
        start_found = False if start_video_id else None
        has_more = True
        stop = False

        for page_number in range(1, max(1, max_pages) + 1):
            if is_instagram:
                params: dict[str, Any] = {"handle": clean, "trim": "true"}
                if cursor:
                    params["next_max_id"] = cursor
                data = self._get("/v2/instagram/user/posts", params)
                items = data.get("items") or []
            else:
                params = {"handle": clean, "sort_by": "latest", "trim": "true"}
                if cursor:
                    params["max_cursor"] = cursor
                data = self._get("/v3/tiktok/profile/videos", params)
                items = data.get("aweme_list") or []

            raw_pages.append(data)
            videos_received += len(items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                position = len(videos) + 1
                metric = (
                    self._instagram_metric(
                        item, clean, page_number=page_number, position_in_run=position
                    )
                    if is_instagram
                    else self._tiktok_metric(
                        item, clean, page_number=page_number, position_in_run=position
                    )
                )
                if not metric or metric.video_id in seen_ids:
                    continue

                published = None
                if metric.published_at:
                    published = datetime.fromisoformat(metric.published_at.replace("Z", "+00:00"))

                if cutoff and published and published < cutoff:
                    stop = True
                    break

                seen_ids.add(metric.video_id)
                videos.append(metric)
                if start_video_id and metric.video_id == str(start_video_id):
                    start_found = True
                    stop = True
                    break

            if stop:
                has_more = False
                break

            if is_instagram:
                next_cursor = data.get("next_max_id")
                has_more = bool(data.get("more_available") and next_cursor)
            else:
                next_cursor = data.get("max_cursor")
                has_more = bool(data.get("has_more") and next_cursor)

            if not has_more:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ScrapeCreatorsError("Pagination cursor repeated; aborted to avoid a loop")
            seen_cursors.add(cursor)

        max_pages_reached = has_more and not stop and len(raw_pages) >= max(1, max_pages)
        return SocialAccountFetch(
            platform="Instagram" if is_instagram else "TikTok",
            account_name="@" + clean,
            videos=videos,
            raw_pages=raw_pages,
            videos_received=videos_received,
            start_video_found=start_found,
            has_more=has_more,
            max_pages_reached=max_pages_reached,
        )

    def fetch_account_videos(self, platform: str, handle: str) -> list[SocialVideoMetric]:
        normalized = platform.strip().lower()
        if normalized in {"instagram", "insta", "ig"}:
            return self.fetch_instagram_posts(handle)
        if normalized in {"tiktok", "tik tok", "tt"}:
            return self.fetch_tiktok_videos(handle)
        raise ScrapeCreatorsError(f"Unsupported platform: {platform}")


def calculate_view_deltas(
    current: list[SocialVideoMetric],
    previous_by_video_id: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    total_delta = 0
    matched = 0
    for item in current:
        if item.views is None:
            continue
        previous = previous_by_video_id.get(item.video_id)
        if not previous:
            continue
        previous_views = _to_int(previous.get("views"))
        if previous_views is None:
            continue
        total_delta += max(0, item.views - previous_views)
        matched += 1
    return total_delta, matched
