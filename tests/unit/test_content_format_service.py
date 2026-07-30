from __future__ import annotations

from src.services.content_format_service import (
    FormatScheduleItem,
    _parse_publish_scope,
    build_format_assignments,
)


def _format(format_id: int, *, pairs=None) -> FormatScheduleItem:
    return FormatScheduleItem(
        format_id=format_id,
        format_name=f"Format {format_id}",
        posting_date="2026-07-28",
        occurrence_index=1,
        source_row=format_id,
        source_url=f"https://example.test/{format_id}",
        raw_publish_scope="",
        allowed_pairs=pairs,
        scope_valid=True,
    )


def _video(video_id: str, published_at: str, *, account: str = "@eli_robinsonn"):
    return {
        "platform": "TikTok",
        "account_name": account,
        "country": "USA",
        "video_id": video_id,
        "published_at": published_at,
    }


def test_matches_earliest_video_to_smallest_format_id():
    rows = [
        _video("later", "2026-07-28T18:00:00+00:00"),
        _video("earlier", "2026-07-28T10:00:00+00:00"),
    ]

    assignments = build_format_assignments(rows, [_format(80), _format(42)])
    by_video = {item["video_id"]: item for item in assignments}

    assert by_video["earlier"]["format_id"] == 42
    assert by_video["later"]["format_id"] == 80
    assert {item["format_match_status"] for item in assignments} == {"Matched"}


def test_count_mismatch_does_not_guess():
    assignments = build_format_assignments(
        [_video("one", "2026-07-28T10:00:00+00:00")],
        [_format(42), _format(80)],
    )

    assert assignments[0]["format_id"] is None
    assert assignments[0]["format_match_status"] == "Requires review: count mismatch"


def test_otty_account_is_explicitly_excluded():
    assignments = build_format_assignments(
        [
            _video(
                "one",
                "2026-07-28T10:00:00+00:00",
                account="@otty.and.lotty",
            )
        ],
        [_format(42)],
    )

    assert assignments[0]["format_id"] is None
    assert assignments[0]["format_match_status"] == "Excluded account"


def test_publish_scope_requires_exact_platform_and_country():
    pairs, valid = _parse_publish_scope(
        "TikTok: США, Франция; Instagram: Великобритания"
    )

    assert valid is True
    assert pairs == frozenset(
        {
            ("TikTok", "USA"),
            ("TikTok", "France"),
            ("Instagram", "United Kingdom"),
        }
    )


def test_post_after_midnight_minsk_matches_next_local_day(monkeypatch):
    monkeypatch.setattr(
        "src.services.content_format_service.config.CONTENT_PERFORMANCE_TIMEZONE",
        "Europe/Minsk",
    )
    row = _video("late-utc", "2026-07-27T22:30:00+00:00")
    next_day_format = FormatScheduleItem(
        format_id=42,
        format_name="Format 42",
        posting_date="2026-07-28",
        occurrence_index=1,
        source_row=42,
        source_url="https://example.test/42",
        raw_publish_scope="",
        allowed_pairs=None,
        scope_valid=True,
    )

    assignments = build_format_assignments([row], [next_day_format])

    assert assignments[0]["source_post_date"] == "2026-07-28"
    assert assignments[0]["format_match_status"] == "Matched"
