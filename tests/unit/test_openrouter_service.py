"""
Unit tests for OpenRouter AI service.

Tests cover:
- JSON parsing from various formats (direct, markdown, embedded)
- Response structure validation
- Error handling
- Batch processing data structures
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.ai.openrouter_service import (
    _extract_json_object,
    _parse_response,
    analyze_screenshot,
    SYSTEM_PROMPT,
    USER_PROMPT,
)


class TestExtractJsonObject:
    """Tests for _extract_json_object helper function."""

    def test_extract_simple_json(self):
        """Should extract simple JSON object from text."""
        text = 'Some text {"key": "value"} more text'
        result = _extract_json_object(text)
        assert result == '{"key": "value"}'

    def test_extract_nested_json(self):
        """Should extract nested JSON object."""
        text = 'Start {"outer": {"inner": "value"}} End'
        result = _extract_json_object(text)
        assert result == '{"outer": {"inner": "value"}}'

    def test_extract_with_escaped_quotes(self):
        """Should handle escaped quotes inside strings."""
        text = '{"key": "value with \\"quotes\\""}'
        result = _extract_json_object(text)
        assert result == '{"key": "value with \\"quotes\\""}'

    def test_extract_with_single_quotes(self):
        """Should handle single quotes correctly."""
        text = "Some text {'key': 'value'} more"
        result = _extract_json_object(text)
        assert result == "{'key': 'value'}"

    def test_no_json_in_text(self):
        """Should return None if no JSON found."""
        text = "Just plain text without any braces"
        result = _extract_json_object(text)
        assert result is None

    def test_unbalanced_braces(self):
        """Should handle unbalanced braces gracefully."""
        text = '{"key": "value"'
        result = _extract_json_object(text)
        # Should not crash, but may return None or partial
        assert result is None


class TestParseResponse:
    """Tests for _parse_response function."""

    def test_parse_direct_json(self):
        """Should parse direct JSON response."""
        data = [{"content_type": "video", "score": 75}]
        text = json.dumps(data)
        result = _parse_response(text)
        assert result == data

    def test_parse_json_object_wrapped_in_list(self):
        """Should wrap single dict in list."""
        data = {"content_type": "video", "score": 75}
        text = json.dumps(data)
        result = _parse_response(text)
        assert result == [data]

    def test_parse_markdown_code_block(self):
        """Should extract JSON from markdown code block."""
        data = [{"content_type": "carousel", "score": 60}]
        text = f"```json\n{json.dumps(data)}\n```"
        result = _parse_response(text)
        assert result == data

    def test_parse_embedded_json(self):
        """Should extract JSON embedded in text."""
        data = [{"content_type": "video", "score": 80}]
        text = f"Here is the analysis: {json.dumps(data)} Thanks!"
        result = _parse_response(text)
        assert result == data

    def test_parse_empty_response(self):
        """Should handle empty response."""
        result = _parse_response("")
        assert result is None

    def test_parse_invalid_json(self):
        """Should handle invalid JSON gracefully."""
        result = _parse_response("not valid json {broken}")
        assert result is None

    def test_parse_batch_response(self, sample_ai_batch_response):
        """Should parse batch response with multiple videos."""
        text = json.dumps(sample_ai_batch_response)
        result = _parse_response(text)
        assert result is not None
        assert len(result) == 2
        assert result[0]["content_type"] == "video"
        assert result[1]["content_type"] == "carousel"


class TestResponseStructureValidation:
    """Tests for validating AI response structure matches expected format."""

    def test_video_response_has_required_fields(self, sample_ai_response_video):
        """Video response should have all required fields."""
        required_fields = [
            "content_type",
            "hook_text",
            "hook_type",
            "video_title",
            "posted_at",
            "platform",
            "metrics",
            "score",
            "verdict",
            "analysis",
            "recommendations",
        ]
        for field in required_fields:
            assert field in sample_ai_response_video, f"Missing field: {field}"

    def test_carousel_response_has_required_fields(self, sample_ai_response_carousel):
        """Carousel response should have all required fields."""
        required_fields = [
            "content_type",
            "hook_text",
            "hook_type",
            "video_title",
            "posted_at",
            "platform",
            "metrics",
            "score",
            "verdict",
            "analysis",
            "recommendations",
        ]
        for field in required_fields:
            assert field in sample_ai_response_carousel, f"Missing field: {field}"

    def test_metrics_has_required_fields(self, sample_ai_response_video):
        """Metrics should have required fields."""
        metrics = sample_ai_response_video["metrics"]
        required_metrics = [
            "views",
            "likes",
            "comments",
            "shares",
            "saves",
        ]
        for field in required_metrics:
            assert field in metrics, f"Missing metric: {field}"

    def test_hook_type_is_valid(self, sample_ai_response_video):
        """hook_type should be one of: short, medium, long."""
        hook_type = sample_ai_response_video["hook_type"]
        assert hook_type in ["short", "medium", "long"]

    def test_content_type_is_valid(self, sample_ai_response_video, sample_ai_response_carousel):
        """content_type should be video or carousel."""
        assert sample_ai_response_video["content_type"] in ["video", "carousel"]
        assert sample_ai_response_carousel["content_type"] in ["video", "carousel"]

    def test_platform_is_valid(self, sample_ai_response_video):
        """platform should be valid."""
        valid_platforms = ["tiktok", "instagram", "youtube", "other"]
        assert sample_ai_response_video["platform"] in valid_platforms

    def test_verdict_format(self, sample_ai_response_video, sample_ai_response_kill):
        """verdict should follow expected format with emoji."""
        verdict = sample_ai_response_video["verdict"]
        assert any(emoji in verdict for emoji in ["🔴", "🟡", "🟢", "✂️", "🚀"])

    def test_score_range(self, sample_ai_response_video):
        """score should be between 0 and 100."""
        score = sample_ai_response_video["score"]
        assert 0 <= score <= 100

    def test_recommendations_is_list(self, sample_ai_response_video):
        """recommendations should be a list."""
        assert isinstance(sample_ai_response_video["recommendations"], list)


class TestAnalyzeScreenshot:
    """Tests for analyze_screenshot function (requires mocking HTTP)."""

    @patch("src.ai.openrouter_service.requests.post")
    @patch("src.ai.openrouter_service.config")
    def test_analyze_screenshot_success(
        self, mock_config, mock_post, mock_openrouter_response
    ):
        """Should successfully analyze screenshot and return parsed results."""
        mock_config.OPENROUTER_API_KEY = "test-key"
        mock_config.OPENROUTER_MODEL = "test-model"
        mock_post.return_value = mock_openrouter_response

        # Create dummy image bytes
        image_bytes = b"fake-image-data"

        result, raw_text = analyze_screenshot([image_bytes])

        assert result is not None
        assert len(result) == 1
        assert result[0]["content_type"] == "video"
        assert result[0]["score"] == 70
        assert raw_text is not None

    @patch("src.ai.openrouter_service.requests.post")
    @patch("src.ai.openrouter_service.config")
    def test_analyze_screenshot_batch(
        self, mock_config, mock_post, sample_ai_batch_response
    ):
        """Should handle batch processing of multiple videos."""
        mock_config.OPENROUTER_API_KEY = "test-key"
        mock_config.OPENROUTER_MODEL = "test-model"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps(sample_ai_batch_response)
                }
            }]
        }
        mock_post.return_value = mock_response

        # Multiple images
        images = [b"image1", b"image2", b"image3"]

        result, raw_text = analyze_screenshot(images)

        assert result is not None
        assert len(result) == 2  # Two videos from batch

    @patch("src.ai.openrouter_service.requests.post")
    @patch("src.ai.openrouter_service.config")
    def test_analyze_screenshot_api_error(self, mock_config, mock_post):
        """Should handle API errors gracefully."""
        mock_config.OPENROUTER_API_KEY = "test-key"
        mock_config.OPENROUTER_MODEL = "test-model"
        mock_post.side_effect = Exception("API Error")

        result, raw_text = analyze_screenshot([b"image"])

        assert result is None
        assert raw_text is None

    @patch("src.ai.openrouter_service.requests.post")
    @patch("src.ai.openrouter_service.config")
    def test_analyze_screenshot_empty_response(self, mock_config, mock_post):
        """Should handle empty AI response."""
        mock_config.OPENROUTER_API_KEY = "test-key"
        mock_config.OPENROUTER_MODEL = "test-model"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": ""}}]
        }
        mock_post.return_value = mock_response

        result, raw_text = analyze_screenshot([b"image"])

        assert result is None

    @patch("src.ai.openrouter_service.requests.post")
    @patch("src.ai.openrouter_service.config")
    def test_analyze_screenshot_video_title_fallback(
        self, mock_config, mock_post
    ):
        """Should use hook_text as fallback for video_title if missing."""
        mock_config.OPENROUTER_API_KEY = "test-key"
        mock_config.OPENROUTER_MODEL = "test-model"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps([{
                        "content_type": "video",
                        "hook_text": "Fallback Title",
                        "video_title": None,  # Missing title
                        "score": 50,
                        "verdict": "🟡 ITERATE",
                        "metrics": {"views": 1000},
                    }])
                }
            }]
        }
        mock_post.return_value = mock_response

        result, _ = analyze_screenshot([b"image"])

        assert result is not None
        assert result[0]["video_title"] == "Fallback Title"


class TestPrompts:
    """Tests for system and user prompts."""

    def test_system_prompt_contains_batch_instructions(self):
        """System prompt should mention batch processing."""
        assert "BATCH" in SYSTEM_PROMPT.upper() or "batch" in SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_video_classification(self):
        """System prompt should contain video classification rules."""
        assert "Video analysis" in SYSTEM_PROMPT or "VIDEO" in SYSTEM_PROMPT

    def test_system_prompt_contains_carousel_classification(self):
        """System prompt should contain carousel classification rules."""
        assert "Post analysis" in SYSTEM_PROMPT or "CAROUSEL" in SYSTEM_PROMPT

    def test_system_prompt_contains_hook_extraction(self):
        """System prompt should contain hook text extraction."""
        assert "hook_text" in SYSTEM_PROMPT

    def test_system_prompt_contains_metrics_structure(self):
        """System prompt should define metrics structure."""
        assert "metrics" in SYSTEM_PROMPT.lower()

    def test_user_prompt_contains_current_time(self):
        """User prompt should include current time placeholder."""
        assert "{current_time_str}" in USER_PROMPT


class TestDataStructuresForDatabase:
    """Tests that AI response structure is compatible with database schema."""

    def test_ai_response_maps_to_db_fields(self, sample_ai_response_video):
        """AI response fields should map correctly to DB columns."""
        # Fields used by insert_video function
        db_mappings = {
            "platform": sample_ai_response_video.get("platform"),
            "title": sample_ai_response_video.get("title") or sample_ai_response_video.get("video_title"),
            "metrics": sample_ai_response_video.get("metrics"),
            "score": sample_ai_response_video.get("score"),
            "analysis": sample_ai_response_video.get("analysis"),
            "verdict": sample_ai_response_video.get("verdict"),
            "content_type": sample_ai_response_video.get("content_type"),
            "hook_text": sample_ai_response_video.get("hook_text"),
        }

        # All critical fields should be present
        assert db_mappings["platform"] is not None
        assert db_mappings["title"] is not None
        assert db_mappings["metrics"] is not None
        assert db_mappings["score"] is not None
        assert db_mappings["verdict"] is not None

    def test_metrics_contains_required_for_calculations(self, sample_ai_response_video):
        """Metrics should contain fields needed for rate calculations."""
        metrics = sample_ai_response_video["metrics"]
        required_for_er = ["views", "likes", "comments", "shares", "saves"]
        for field in required_for_er:
            assert field in metrics, f"Missing field for ER calculation: {field}"

    def test_metrics_can_calculate_aggregated_er(self, sample_ai_response_video):
        """Should be able to calculate aggregated ER from metrics."""
        m = sample_ai_response_video["metrics"]
        if m.get("views", 0) > 0:
            er = (m["likes"] + m["comments"] + m["shares"] + m["saves"]) / m["views"] * 100
            assert er >= 0


class TestDataStructuresForSheets:
    """Tests that AI response structure is compatible with Google Sheets export."""

    def test_ai_response_maps_to_sheets_columns(self, sample_ai_response_video):
        """AI response fields should map to Google Sheets columns."""
        # Columns from sheets_service: A-P
        sheets_mappings = {
            "processed_at": "timestamp",  # Generated by sheets service
            "posted_at": sample_ai_response_video.get("posted_at"),
            "content_type": sample_ai_response_video.get("content_type"),
            "platform": sample_ai_response_video.get("platform"),
            "video_title": sample_ai_response_video.get("hook_text") or sample_ai_response_video.get("video_title"),
            "hook_type": sample_ai_response_video.get("hook_type"),
            "score": sample_ai_response_video.get("score"),
            "verdict": sample_ai_response_video.get("verdict"),
        }

        # All critical fields should be present
        assert sheets_mappings["posted_at"] is not None
        assert sheets_mappings["content_type"] is not None
        assert sheets_mappings["score"] is not None

    def test_metrics_maps_to_sheets_metrics(self, sample_ai_response_video):
        """Metrics should map to sheets columns J-P."""
        metrics = sample_ai_response_video["metrics"]
        sheets_metrics = {
            "views": metrics.get("views"),
            "likes": metrics.get("likes"),
            "comments": metrics.get("comments"),
            "shares": metrics.get("shares"),
            "retention_3s": metrics.get("retention_3s"),
            "avg_watch_time": metrics.get("avg_watch_time_pct"),
        }

        # Core metrics should be present
        assert sheets_metrics["views"] is not None
        assert sheets_metrics["likes"] is not None

    def test_carousel_has_photos_viewed(self, sample_ai_response_carousel):
        """Carousel should have photos_viewed for avg_watch_time calculation."""
        metrics = sample_ai_response_carousel["metrics"]
        assert "photos_viewed" in metrics
        assert "total_photos" in metrics
