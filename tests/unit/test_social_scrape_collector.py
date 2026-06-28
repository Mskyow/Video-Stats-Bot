from __future__ import annotations

from src.services.scrapecreators_service import SocialVideoMetric
from src.services.social_scrape_collector import calculate_account_daily_views


def _video(video_id: str, views: int, published_at: str) -> SocialVideoMetric:
    return SocialVideoMetric(
        platform="TikTok",
        account_name="@example",
        video_id=video_id,
        video_url=None,
        published_at=published_at,
        title=None,
        views=views,
        likes=None,
        comments=None,
        saves=None,
        shares=None,
        raw_json={},
    )


def test_daily_views_include_matched_deltas_and_new_videos():
    current = [
        _video("matched", 150, "2026-06-28T10:00:00Z"),
        _video("new", 30, "2026-06-29T08:00:00Z"),
        _video("old-unmatched", 500, "2026-06-20T08:00:00Z"),
    ]
    previous = {"matched": {"views": 100}}

    total, matched, new_videos = calculate_account_daily_views(
        current,
        previous,
        previous_scraped_at="2026-06-29T06:00:00Z",
    )

    assert total == 80
    assert matched == 1
    assert new_videos == 1


def test_daily_views_clamp_counter_decreases_to_zero():
    total, matched, new_videos = calculate_account_daily_views(
        [_video("matched", 90, "2026-06-28T10:00:00Z")],
        {"matched": {"views": 100}},
        previous_scraped_at="2026-06-29T06:00:00Z",
    )

    assert total == 0
    assert matched == 1
    assert new_videos == 0
