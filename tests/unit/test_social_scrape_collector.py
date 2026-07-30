from __future__ import annotations

import pytest

from src.services.scrapecreators_service import SocialVideoMetric
from datetime import datetime, timezone

from src.services.social_scrape_collector import (
    IncompleteSocialSnapshotError,
    SocialScrapeResult,
    _should_emit_daily_metric,
    _validate_snapshot_completeness,
    calculate_account_daily_views,
    collect_configured_social_accounts,
)


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


def test_should_emit_daily_metric_accepts_normal_daily_interval():
    should_emit, reason = _should_emit_daily_metric(
        snapshot_date="2026-07-08",
        previous_date="2026-07-07",
        previous_scraped_at="2026-07-07T06:00:00Z",
        current_scraped_at=datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert should_emit is True
    assert reason is None


def test_should_emit_daily_metric_skips_migration_gap():
    should_emit, reason = _should_emit_daily_metric(
        snapshot_date="2026-07-08",
        previous_date="2026-07-06",
        previous_scraped_at="2026-07-06T20:00:00Z",
        current_scraped_at=datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert should_emit is False
    assert reason == "previous_date_mismatch:2026-07-06->2026-07-07"


def test_should_emit_daily_metric_skips_short_interval():
    should_emit, reason = _should_emit_daily_metric(
        snapshot_date="2026-07-08",
        previous_date="2026-07-07",
        previous_scraped_at="2026-07-07T20:00:00Z",
        current_scraped_at=datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert should_emit is False
    assert reason == "interval_too_short:10.00h"


def test_snapshot_completeness_rejects_large_drop(monkeypatch):
    monkeypatch.setattr(
        "src.services.social_scrape_collector.config.SOCIAL_SCRAPE_MIN_BASELINE_VIDEOS",
        20,
    )
    monkeypatch.setattr(
        "src.services.social_scrape_collector.config.SOCIAL_SCRAPE_MIN_SNAPSHOT_RATIO",
        0.5,
    )

    with pytest.raises(IncompleteSocialSnapshotError, match="received 19 videos"):
        _validate_snapshot_completeness(
            platform="TikTok",
            handle="kamil_smith4",
            current_count=19,
            previous_count=54,
        )


def test_failed_account_is_retried_after_primary_pass(monkeypatch):
    account = {
        "id": "account-1",
        "platform": "TikTok",
        "handle": "example",
        "display_name": "Example",
    }
    attempts = []

    monkeypatch.setattr(
        "src.services.social_scrape_collector.list_enabled_social_scrape_accounts",
        lambda _supabase: [account],
    )
    monkeypatch.setattr(
        "src.services.social_scrape_collector.config.SOCIAL_SCRAPE_RETRY_FAILED_ACCOUNTS",
        True,
    )

    def fake_collect(_supabase, _account, calculate_daily=True):
        attempts.append(calculate_daily)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return SocialScrapeResult(
            platform="TikTok",
            handle="example",
            display_name="Example",
            status="success",
            pages_requested=1,
            videos_received=1,
            videos_saved=1,
            total_lifetime_views=100,
            start_video_found=True,
        )

    monkeypatch.setattr(
        "src.services.social_scrape_collector.collect_social_account",
        fake_collect,
    )

    results = collect_configured_social_accounts(object())

    assert len(attempts) == 2
    assert results[0].status == "success"
