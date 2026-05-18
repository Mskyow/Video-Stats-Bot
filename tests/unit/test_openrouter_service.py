"""Unit tests for two-step OpenRouter pipeline."""
from __future__ import annotations

import json
from unittest.mock import Mock, patch

from src.ai.openrouter_service import (
    _build_scoring_system_prompt,
    _extract_json_object,
    _normalize_extraction_result,
    _normalize_final_result,
    _normalize_metrics,
    _parse_response,
    _validate_analysis_result,
    _validate_extraction_result,
    analyze_screenshot,
    get_ai_quality_snapshot,
)


def _mock_response(content: str) -> Mock:
    response = Mock()
    response.status_code = 200
    response.raise_for_status = Mock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def test_extract_json_object_simple() -> None:
    text = 'prefix {"key":"value"} suffix'
    assert _extract_json_object(text) == '{"key":"value"}'


def test_parse_response_markdown_block() -> None:
    payload = {"platform": "tiktok"}
    text = f"```json\n{json.dumps(payload)}\n```"
    assert _parse_response(text) == payload


def test_validate_extraction_result_ok() -> None:
    result = {
        "platform": "tiktok",
        "content_type": "video",
        "video_title": "Test",
        "posted_at": "2026-02-11",
        "hook_text": "Hook",
        "hook_type": "short",
        "video_duration_sec": 12,
        "metrics": {"views": 1000, "likes": 100, "comments": 10, "shares": 5, "saves": 15},
    }
    valid, errors = _validate_extraction_result(result)
    assert valid is True
    assert errors == []


def test_validate_analysis_result_invalid_score() -> None:
    result = {
        "platform": "tiktok",
        "content_type": "video",
        "metrics": {"views": 1000, "likes": 100, "comments": 10, "shares": 5, "saves": 15},
        "score": 99,
        "verdict": "🟡 ITERATE",
        "analysis": "text",
        "recommendations": ["a"],
    }
    valid, errors = _validate_analysis_result(result)
    assert valid is False
    assert any("score must be in range 0..10" in e for e in errors)


@patch("src.ai.openrouter_service.requests.post")
@patch("src.ai.openrouter_service.config")
def test_analyze_screenshot_two_step_success(mock_config: Mock, mock_post: Mock) -> None:
    mock_config.OPENROUTER_API_KEY = "test-key"
    mock_config.OPENROUTER_MODEL = "test-model"
    mock_config.OPENROUTER_TIMEOUT_SEC = 30.0
    mock_config.OPENROUTER_MAX_RETRIES = 1
    mock_config.OPENROUTER_USE_STRUCTURED_OUTPUT = True
    mock_config.AI_QUALITY_LOG_EVERY_N = 999999

    extraction = {
        "platform": "tiktok",
        "content_type": "video",
        "video_title": "My Video",
        "posted_at": "2026-02-11",
        "hook_text": "Hook text",
        "hook_type": "short",
        "video_duration_sec": 15,
        "metrics": {
            "views": 10000,
            "likes": 500,
            "comments": 30,
            "shares": 20,
            "saves": 50,
            "retention_3s": 70,
            "completion_rate": 55,
            "avg_watch_time_pct": 62,
            "viewed_pct": 62,
            "photos_viewed": None,
            "total_photos": None,
            "tiktok_churn_point": None,
        },
    }
    scoring = {
        "platform": "tiktok",
        "content_type": "video",
        "video_title": "My Video",
        "posted_at": "2026-02-11",
        "hook_text": "Hook text",
        "hook_type": "short",
        "metrics": extraction["metrics"],
        "score": 7.6,
        "verdict": "🚀 SCALE HARD",
        "analysis": "Good performance",
        "recommendations": ["Scale format"],
        "calculated_rates": {"share_rate": 0.2, "save_rate": 0.5, "aggregated_er": 6.0},
    }

    mock_post.side_effect = [
        _mock_response(json.dumps(extraction)),
        _mock_response(json.dumps(scoring)),
    ]

    result, raw = analyze_screenshot([b"img1", b"img2"])
    assert result is not None
    assert result["score"] == 7.6
    assert result["title"] == "My Video"
    assert result["platform"] == "tiktok"
    assert raw is not None


def test_quality_snapshot_shape() -> None:
    snapshot = get_ai_quality_snapshot()
    assert "total_calls" in snapshot
    assert "success_rate_pct" in snapshot


def test_avg_watch_time_pct_can_exceed_100() -> None:
    normalized = _normalize_metrics(
        {
            "views": 100,
            "likes": 10,
            "comments": 1,
            "shares": 2,
            "saves": 3,
            "avg_watch_time_pct": 128.6,
        }
    )
    assert normalized["avg_watch_time_pct"] == 128.6


def test_avg_watch_time_zero_with_positive_retention_becomes_missing() -> None:
    normalized = _normalize_metrics(
        {
            "views": 100,
            "likes": 10,
            "comments": 1,
            "shares": 2,
            "saves": 3,
            "retention_3s": 65,
            "avg_watch_time_pct": 0,
        }
    )
    assert normalized["avg_watch_time_pct"] is None


def test_normalize_metrics_end_retention_3_screenshot_mode() -> None:
    """3-screenshot mode: end_retention_second as int, end_retention_pct clamped 0-100."""
    normalized = _normalize_metrics(
        {
            "views": 100,
            "likes": 10,
            "comments": 1,
            "shares": 2,
            "saves": 3,
            "end_retention_second": 6,
            "end_retention_pct": 10.5,
        }
    )
    assert normalized["end_retention_second"] == 6
    assert normalized["end_retention_pct"] == 10.5

    normalized_null = _normalize_metrics(
        {"views": 0, "likes": 0, "comments": 0, "shares": 0, "saves": 0}
    )
    assert normalized_null.get("end_retention_second") is None
    assert normalized_null.get("end_retention_pct") is None


def test_normalize_metrics_preserves_missing_social_counters() -> None:
    normalized = _normalize_metrics(
        {
            "views": 100,
            "likes": None,
            "comments": None,
            "shares": None,
            "saves": None,
        }
    )
    assert normalized["views"] == 100
    assert normalized["likes"] is None
    assert normalized["comments"] is None
    assert normalized["shares"] is None
    assert normalized["saves"] is None


def test_score_guardrail_caps_zero_social_actions() -> None:
    extracted = {
        "video_title": "Test",
        "posted_at": "2026-02-11",
        "platform": "reels",
        "content_type": "video",
        "hook_text": "hook text",
        "hook_type": "short",
        "video_duration_sec": 8.0,
        "metrics": {
            "views": 245,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "saves": 2,
            "retention_3s": 70.0,
            "completion_rate": 60.0,
            "avg_watch_time_pct": 128.0,
            "viewed_pct": None,
            "photos_viewed": None,
            "total_photos": None,
            "tiktok_churn_point": None,
        },
    }
    final_result = {
        "platform": "reels",
        "content_type": "video",
        "metrics": extracted["metrics"],
        "score": 7.2,
        "verdict": "🚀 SCALE HARD",
        "analysis": "Some analysis",
        "recommendations": ["Do something"],
    }
    normalized = _normalize_final_result(final_result, extracted, "test-model")
    assert normalized["score"] <= 4.8
    assert "ITERATE" in normalized["verdict"]


def test_build_scoring_system_prompt_uses_carousel_benchmarks() -> None:
    prompt = _build_scoring_system_prompt("carousel")
    assert "Selected content_type benchmark profile: carousel" in prompt
    assert "first_slide_hook" in prompt
    assert "swipe_through_rate" in prompt


def test_build_scoring_system_prompt_defaults_to_video_profile() -> None:
    prompt = _build_scoring_system_prompt("unknown")
    assert "Selected content_type benchmark profile: video" in prompt
    assert "hook_3s" in prompt


def test_normalize_extraction_result_carousel_derives_watch_time_and_strips_posted_prefix() -> None:
    extracted = {
        "platform": "reels",
        "content_type": "carousel",
        "video_title": "Carousel",
        "posted_at": "Posted on Feb 6, 2026, 12:53 PM",
        "hook_text": "hook",
        "hook_type": "short",
        "video_duration_sec": None,
        "metrics": {
            "views": 900,
            "likes": 70,
            "comments": 4,
            "shares": 6,
            "saves": 28,
            "retention_3s": 62,
            "completion_rate": None,
            "avg_watch_time_pct": None,
            "viewed_pct": None,
            "photos_viewed": 2.5,
            "total_photos": 5,
            "tiktok_churn_point": None,
        },
    }

    normalized = _normalize_extraction_result(extracted)
    assert normalized["posted_at"] == "Feb 6, 2026, 12:53 PM"
    assert normalized["metrics"]["viewed_pct"] == 50.0
    assert normalized["metrics"]["avg_watch_time_pct"] == 50.0
