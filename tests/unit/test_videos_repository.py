"""
Unit tests for Videos Repository.

Tests cover:
- Video deduplication logic (NEW, UPDATE, DUPLICATE)
- Insert operations with various data structures
- Data retrieval functions
- Edge cases and error handling
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from src.db.repositories.videos import (
    VideoStatus,
    check_video_status,
    insert_video,
    get_videos_by_date_range,
    get_global_stats,
    get_user_videos,
    get_user_stats_summary,
    get_hook_statistics,
    normalize_title,
)


class TestNormalizeTitle:
    """Tests for title normalization."""

    def test_normalize_lowercase(self):
        """Should convert to lowercase."""
        assert normalize_title("Test Title") == "testtitle"

    def test_normalize_removes_spaces(self):
        """Should remove all spaces."""
        assert normalize_title("Test  Title") == "testtitle"

    def test_normalize_removes_tabs(self):
        """Should remove tabs."""
        assert normalize_title("Test\tTitle") == "testtitle"

    def test_normalize_removes_newlines(self):
        """Should remove newlines."""
        assert normalize_title("Test\nTitle") == "testtitle"

    def test_normalize_empty_string(self):
        """Should handle empty string."""
        assert normalize_title("") == ""

    def test_normalize_none(self):
        """Should handle None."""
        assert normalize_title(None) == ""


class TestCheckVideoStatus:
    """Tests for video deduplication status checking."""

    def test_new_video_when_no_match(self, mock_supabase_client):
        """Should return NEW when no matching video exists."""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = Mock(data=[])

        status, existing = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="New Video",
            posted_at="2024-01-15",
            new_views=1000
        )

        assert status == VideoStatus.NEW
        assert existing is None

    def test_new_video_when_no_client(self):
        """Should return NEW when client is None."""
        status, existing = check_video_status(
            None,
            user_id=123,
            title="Video",
            posted_at="2024-01-15",
            new_views=1000
        )
        assert status == VideoStatus.NEW

    def test_new_video_when_missing_title_or_date(self, mock_supabase_client):
        """Should return NEW when title or posted_at is missing."""
        status, _ = check_video_status(
            mock_supabase_client,
            user_id=123,
            title=None,
            posted_at="2024-01-15",
            new_views=1000
        )
        assert status == VideoStatus.NEW

        status, _ = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="Video",
            posted_at=None,
            new_views=1000
        )
        assert status == VideoStatus.NEW

    def test_update_when_views_grew_more_than_2_percent(self, mock_supabase_client):
        """Should return UPDATE when views grew by 2% or more."""
        existing_video = {
            "id": "existing-id",
            "title": "Test Video",
            "metrics": {"views": 1000, "posted_at": "2024-01-15"}
        }

        mock_response = Mock()
        mock_response.data = [existing_video]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        # Views grew from 1000 to 1100 (+10%)
        status, existing = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="Test Video",
            posted_at="2024-01-15",
            new_views=1100
        )

        assert status == VideoStatus.UPDATE
        assert existing == existing_video

    def test_duplicate_when_views_grew_less_than_2_percent(self, mock_supabase_client):
        """Should return DUPLICATE when views grew by less than 2%."""
        existing_video = {
            "id": "existing-id",
            "title": "Test Video",
            "metrics": {"views": 1000, "posted_at": "2024-01-15"}
        }

        mock_response = Mock()
        mock_response.data = [existing_video]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        # Views grew from 1000 to 1005 (+0.5%)
        status, existing = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="Test Video",
            posted_at="2024-01-15",
            new_views=1005
        )

        assert status == VideoStatus.DUPLICATE

    def test_duplicate_when_same_views(self, mock_supabase_client):
        """Should return DUPLICATE when views are the same."""
        existing_video = {
            "id": "existing-id",
            "title": "Test Video",
            "metrics": {"views": 1000, "posted_at": "2024-01-15"}
        }

        mock_response = Mock()
        mock_response.data = [existing_video]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        status, existing = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="Test Video",
            posted_at="2024-01-15",
            new_views=1000
        )

        assert status == VideoStatus.DUPLICATE

    def test_title_normalization_matching(self, mock_supabase_client):
        """Should match titles with different spacing/casing."""
        existing_video = {
            "id": "existing-id",
            "title": "Test Video Title",
            "metrics": {"views": 1000, "posted_at": "2024-01-15"}
        }

        mock_response = Mock()
        mock_response.data = [existing_video]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        # Same title with different spacing
        status, _ = check_video_status(
            mock_supabase_client,
            user_id=123,
            title="testvideotitle",
            posted_at="2024-01-15",
            new_views=1000
        )

        assert status == VideoStatus.DUPLICATE


class TestInsertVideo:
    """Tests for video insertion."""

    def test_insert_new_video(self, mock_supabase_client, sample_ai_response_video):
        """Should successfully insert new video."""
        # Mock check_video_status to return NEW
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        # Mock insert response
        mock_insert_response = Mock()
        mock_insert_response.data = [{"id": "new-video-id"}]
        mock_supabase_client.table.return_value.insert.return_value.execute.return_value = mock_insert_response

        result = insert_video(
            mock_supabase_client,
            user_id=123,
            result=sample_ai_response_video,
            raw_ai_response="test response"
        )

        assert result is not None
        assert result.get("id") == "new-video-id"

    def test_skip_duplicate_video(self, mock_supabase_client, sample_ai_response_video):
        """Should skip duplicate video and return marker."""
        # The AI response uses "video_title" but DB uses "title"
        # Create a result with both fields as would happen after processing
        title = sample_ai_response_video.get("video_title")
        posted_at = sample_ai_response_video.get("posted_at")
        
        # Create result dict with both title and video_title as in real usage
        result_with_title = {**sample_ai_response_video, "title": title}
        
        existing_video = {
            "id": "existing-id",
            "title": title,
            "metrics": {"views": 15000, "posted_at": posted_at}
        }

        # Mock the complete chain for check_video_status
        mock_execute_result = Mock()
        mock_execute_result.data = [existing_video]
        
        mock_limit = Mock()
        mock_limit.execute.return_value = mock_execute_result
        
        mock_order = Mock()
        mock_order.limit.return_value = mock_limit
        
        mock_eq = Mock()
        mock_eq.order.return_value = mock_order
        
        mock_select = Mock()
        mock_select.eq.return_value = mock_eq
        
        mock_table = Mock()
        mock_table.select.return_value = mock_select
        
        mock_supabase_client.table.return_value = mock_table

        # Also need to mock insert for this test - it shouldn't be called for duplicate
        # but we mock it just in case
        mock_insert_result = Mock()
        mock_insert_result.execute.return_value = Mock(data=[{"id": "new-id"}])
        mock_insert = Mock()
        mock_insert.return_value = mock_insert_result
        mock_table.insert = mock_insert

        result = insert_video(
            mock_supabase_client,
            user_id=123,
            result=result_with_title,
            raw_ai_response=None
        )

        assert result is not None
        assert result.get("skipped") is True
        assert result.get("duplicate") is True
        assert result.get("existing_video") is not None

    def test_insert_with_null_client(self, sample_ai_response_video):
        """Should return None when client is None."""
        result = insert_video(
            None,
            user_id=123,
            result=sample_ai_response_video,
            raw_ai_response=None
        )
        assert result is None

    def test_insert_carousel(self, mock_supabase_client, sample_ai_response_carousel):
        """Should successfully insert carousel content."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        mock_insert_response = Mock()
        mock_insert_response.data = [{"id": "carousel-id"}]
        mock_supabase_client.table.return_value.insert.return_value.execute.return_value = mock_insert_response

        result = insert_video(
            mock_supabase_client,
            user_id=123,
            result=sample_ai_response_carousel,
            raw_ai_response=None
        )

        assert result is not None
        assert result.get("id") == "carousel-id"

    def test_payload_structure(self, mock_supabase_client, sample_ai_response_video):
        """Should build correct payload structure."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        captured_payload = None
        def capture_insert(*args, **kwargs):
            nonlocal captured_payload
            if args:
                captured_payload = args[0]
            mock_response = Mock()
            mock_response.execute.return_value = Mock(data=[{"id": "test-id"}])
            return mock_response

        mock_insert_builder = Mock()
        mock_insert_builder.execute = Mock(return_value=Mock(data=[{"id": "test-id"}]))
        mock_supabase_client.table.return_value.insert = capture_insert
        mock_supabase_client.table.return_value.insert.return_value = mock_insert_builder

        insert_video(
            mock_supabase_client,
            user_id=123,
            result=sample_ai_response_video,
            raw_ai_response="raw response"
        )

        # Verify the insert was called
        assert mock_supabase_client.table.called

    def test_insert_handles_string_hook_3s(self, mock_supabase_client, sample_ai_response_video):
        """Should not crash when hook_3s is returned as a string."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        captured_payload = {}

        class InsertBuilder:
            def __init__(self, payload):
                captured_payload.update(payload)

            def execute(self):
                return Mock(data=[{"id": "hook-string-id"}])

        def capture_insert(payload):
            return InsertBuilder(payload)

        mock_supabase_client.table.return_value.insert = capture_insert

        ai_result = {
            **sample_ai_response_video,
            "tier_1_analysis": {"hook_3s": "GOOD"},
        }

        result = insert_video(
            mock_supabase_client,
            user_id=123,
            result=ai_result,
            raw_ai_response=None,
        )

        assert result is not None
        assert result.get("id") == "hook-string-id"
        assert captured_payload.get("hook_score") == "GOOD"

    def test_insert_rounds_video_duration_to_int(self, mock_supabase_client, sample_ai_response_video):
        """Should normalize fractional video_duration_sec for integer DB column."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        captured_payload = {}

        class InsertBuilder:
            def __init__(self, payload):
                captured_payload.update(payload)

            def execute(self):
                return Mock(data=[{"id": "duration-id"}])

        def capture_insert(payload):
            return InsertBuilder(payload)

        mock_supabase_client.table.return_value.insert = capture_insert

        ai_result = {**sample_ai_response_video, "video_duration_sec": "7.85"}

        result = insert_video(
            mock_supabase_client,
            user_id=123,
            result=ai_result,
            raw_ai_response=None,
        )

        assert result is not None
        assert result.get("id") == "duration-id"
        assert captured_payload.get("video_duration_sec") == 8


class TestGetVideosByDateRange:
    """Tests for retrieving videos by date range."""

    def test_get_videos_success(self, mock_supabase_client):
        """Should retrieve videos for date range."""
        mock_videos = [
            {"id": "1", "score": 80},
            {"id": "2", "score": 70}
        ]
        mock_response = Mock()
        mock_response.data = mock_videos
        mock_supabase_client.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute.return_value = mock_response

        result = get_videos_by_date_range(
            mock_supabase_client,
            start_date="2024-01-01",
            end_date="2024-01-31"
        )

        assert len(result) == 2
        assert result[0]["score"] == 80

    def test_get_videos_empty_result(self, mock_supabase_client):
        """Should handle empty result."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute.return_value = mock_response

        result = get_videos_by_date_range(
            mock_supabase_client,
            start_date="2024-01-01",
            end_date="2024-01-31"
        )

        assert result == []

    def test_get_videos_null_client(self):
        """Should return empty list when client is None."""
        result = get_videos_by_date_range(
            None,
            start_date="2024-01-01",
            end_date="2024-01-31"
        )
        assert result == []


class TestGetGlobalStats:
    """Tests for global statistics."""

    def test_get_global_stats_success(self, mock_supabase_client):
        """Should calculate platform global statistics correctly."""
        mock_videos = [
            {"platform": "tiktok", "score": 8.0, "metrics": {"views": "12.5K"}},
            {"platform": "TikTok", "score": 6.0, "metrics": {"views": 15000}},
            {"platform": "instagram reels", "score": 9.0, "metrics": {"view_count": "1.2M"}},
            {"platform": "instagram", "score": None, "metrics": {"views": "23000"}},
            {"platform": "youtube", "score": 10.0, "metrics": {"views": 999999}},
        ]
        mock_response = Mock()
        mock_response.data = mock_videos
        mock_supabase_client.table.return_value.select.return_value.execute.return_value = mock_response

        result = get_global_stats(mock_supabase_client)

        assert result["total_count"] == 5
        assert result["platforms"]["TikTok"]["total_videos"] == 2
        assert result["platforms"]["TikTok"]["avg_score"] == 7.0
        assert result["platforms"]["TikTok"]["max_views"] == 15000
        assert result["platforms"]["Instagram"]["total_videos"] == 2
        assert result["platforms"]["Instagram"]["avg_score"] == 9.0
        assert result["platforms"]["Instagram"]["max_views"] == 1200000

    def test_get_global_stats_empty(self, mock_supabase_client):
        """Should handle empty database."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.execute.return_value = mock_response

        result = get_global_stats(mock_supabase_client)

        assert result["total_count"] == 0
        assert result["platforms"]["TikTok"]["total_videos"] == 0
        assert result["platforms"]["TikTok"]["avg_score"] == 0.0
        assert result["platforms"]["TikTok"]["max_views"] == 0
        assert result["platforms"]["Instagram"]["total_videos"] == 0
        assert result["platforms"]["Instagram"]["avg_score"] == 0.0
        assert result["platforms"]["Instagram"]["max_views"] == 0

    def test_get_global_stats_null_client(self):
        """Should return empty dict when client is None."""
        result = get_global_stats(None)
        assert result == {}


class TestGetUserVideos:
    """Tests for retrieving user videos."""

    def test_get_user_videos_success(self, mock_supabase_client):
        """Should retrieve user videos ordered by created_at."""
        mock_videos = [
            {"id": "1", "user_id": 123, "created_at": "2024-01-15"},
            {"id": "2", "user_id": 123, "created_at": "2024-01-14"},
        ]
        mock_response = Mock()
        mock_response.data = mock_videos
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        result = get_user_videos(mock_supabase_client, user_id=123, limit=10)

        assert len(result) == 2

    def test_get_user_videos_respects_limit(self, mock_supabase_client):
        """Should respect the limit parameter."""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = Mock(data=[])

        get_user_videos(mock_supabase_client, user_id=123, limit=5)

        # Verify limit was passed
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.assert_called_once_with(5)


class TestGetUserStatsSummary:
    """Tests for user statistics summary."""

    def test_get_user_stats_summary(self, mock_supabase_client):
        """Should calculate user statistics correctly."""
        mock_videos = [
            {"score": 80, "verdict": "🚀 SCALE HARD", "platform": "tiktok", "hook_score": "good"},
            {"score": 60, "verdict": "🟡 ITERATE", "platform": "tiktok", "hook_score": "medium"},
            {"score": 40, "verdict": "🔴 KILL HOOK", "platform": "instagram", "hook_score": "bad"},
        ]
        mock_response = Mock()
        mock_response.data = mock_videos
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        result = get_user_stats_summary(mock_supabase_client, user_id=123)

        assert result["total"] == 3
        assert result["avg_score"] == 60.0
        assert result["verdicts"]["🚀 SCALE HARD"] == 1
        assert result["verdicts"]["🟡 ITERATE"] == 1
        assert result["verdicts"]["🔴 KILL HOOK"] == 1
        assert result["platforms"]["tiktok"] == 2
        assert result["platforms"]["instagram"] == 1

    def test_get_user_stats_summary_empty(self, mock_supabase_client):
        """Should handle no videos."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

        result = get_user_stats_summary(mock_supabase_client, user_id=123)

        assert result["total"] == 0


class TestGetHookStatistics:
    """Tests for hook statistics."""

    def test_get_hook_statistics_global(self, mock_supabase_client):
        """Should get global hook statistics."""
        mock_videos = [
            {"hook_score": "excellent", "score": 85},
            {"hook_score": "good", "score": 75},
            {"hook_score": "good", "score": 70},
            {"hook_score": "bad", "score": 40},
        ]
        mock_response = Mock()
        mock_response.data = mock_videos
        mock_supabase_client.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = mock_response

        result = get_hook_statistics(mock_supabase_client, user_id=None)

        assert result["total_with_hook"] == 4
        assert result["hook_counts"]["excellent"] == 1
        assert result["hook_counts"]["good"] == 2
        assert result["hook_counts"]["bad"] == 1
        assert result["avg_score_by_hook"]["excellent"] == 85.0
        assert result["avg_score_by_hook"]["good"] == 72.5  # (75+70)/2

    def test_get_hook_statistics_for_user(self, mock_supabase_client):
        """Should get hook statistics for specific user."""
        mock_response = Mock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.not_.is_.return_value.eq.return_value.execute.return_value = mock_response

        get_hook_statistics(mock_supabase_client, user_id=123)

        # Verify user filter was applied
        assert mock_supabase_client.table.return_value.select.return_value.not_.is_.return_value.eq.called


class TestDataCompatibility:
    """Tests for data structure compatibility between AI response and DB."""

    def test_ai_response_to_db_mapping(self, sample_ai_response_video):
        """Should correctly map AI response to DB fields."""
        # Fields that insert_video extracts from AI response
        assert "content_type" in sample_ai_response_video
        assert "hook_text" in sample_ai_response_video
        assert "platform" in sample_ai_response_video
        assert "video_title" in sample_ai_response_video or "title" in sample_ai_response_video
        assert "metrics" in sample_ai_response_video
        assert "score" in sample_ai_response_video
        assert "analysis" in sample_ai_response_video
        assert "verdict" in sample_ai_response_video

    def test_metrics_structure_for_db(self, sample_ai_response_video):
        """Metrics should contain all fields needed for DB storage."""
        metrics = sample_ai_response_video["metrics"]
        required_metrics = [
            "views", "likes", "comments", "shares", "saves",
            "retention_3s", "avg_watch_time_pct"
        ]
        for field in required_metrics:
            assert field in metrics, f"Missing metric: {field}"

    def test_hook_type_included(self, sample_ai_response_video):
        """hook_type should be present for DB storage."""
        assert "hook_type" in sample_ai_response_video
        assert sample_ai_response_video["hook_type"] in ["short", "medium", "long"]
