"""
Free public metadata scraper for TikTok/Instagram URLs.

This uses yt-dlp extractors. It does not log into accounts and therefore can only return
publicly available counters. Treat it as a cheap first pass, not as a guaranteed API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from yt_dlp import YoutubeDL


URL_PATTERN = re.compile(r"https?://[^\s<>()]+", flags=re.IGNORECASE)


@dataclass(frozen=True)
class PublicVideoMetrics:
    platform: str
    url: str
    title: str | None
    uploader: str | None
    upload_date: str | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    raw_id: str | None


def extract_urls(text: str) -> list[str]:
    urls = URL_PATTERN.findall(text or "")
    cleaned: list[str] = []
    for url in urls:
        normalized = url.rstrip(".,;)")
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _detect_platform(url: str, info: dict[str, Any]) -> str:
    raw = " ".join(
        str(value or "")
        for value in (
            url,
            info.get("extractor"),
            info.get("extractor_key"),
            info.get("webpage_url_domain"),
        )
    ).lower()
    if "tiktok" in raw:
        return "TikTok"
    if "instagram" in raw or "instagr.am" in raw:
        return "Instagram"
    return "Other"


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def scrape_public_video(url: str) -> PublicVideoMetrics:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise ValueError("yt-dlp did not return metadata")

    platform = _detect_platform(url, info)
    return PublicVideoMetrics(
        platform=platform,
        url=str(info.get("webpage_url") or url),
        title=info.get("title"),
        uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
        upload_date=info.get("upload_date"),
        views=_to_int(info.get("view_count")),
        likes=_to_int(info.get("like_count")),
        comments=_to_int(info.get("comment_count")),
        shares=_to_int(info.get("repost_count") or info.get("share_count")),
        raw_id=str(info.get("id")) if info.get("id") is not None else None,
    )
