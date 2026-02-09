"""
Integration tests for Image Handler.

Tests cover:
- Full flow from photo upload to AI analysis
- Database saving
- Google Sheets export
- Report generation
- Duplicate detection
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest


# We need to patch the sheets_service before importing image handler
# because the module creates an asyncio.Queue at import time
# Mock sheets_service completely before any imports
mock_sheets_module = MagicMock()
mock_sheets_module.queue_export_to_sheet = AsyncMock()
sys.modules["src.services.sheets_service"] = mock_sheets_module

# Add project root to path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Now import the modules under test
from src.bot.handlers.image import (
    VideoProcessingResult,
    _convert_ai_result_to_processing_result,
    _download_all_photos,
    build_summary_report,
)


class TestVideoProcessingResult:
    """Tests for VideoProcessingResult dataclass."""

    def test_default_creation(self):
        """Should create with default values."""
        result = VideoProcessingResult(index=1)
        assert result.index == 1
        assert result.success is False
        assert result.score == 0
        assert result.rating_label == "N/A"

    def test_full_creation(self):
        """Should create with all fields."""
        result = VideoProcessingResult(
            index=2,
            video_title="Test Video",
            success=True,
            score=85,
            rating_label="Scale",
            error_message=None,
            ai_result={"key": "value"},
            raw_response="raw",
            is_duplicate=False,
        )
        assert result.index == 2
        assert result.video_title == "Test Video"
        assert result.success is True
        assert result.score == 85
        assert result.rating_label == "Scale"


class TestConvertAiResultToProcessingResult:
    """Tests for AI result conversion."""

    def test_convert_valid_video(self, sample_ai_response_video):
        """Should convert valid video result correctly."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_video,
            raw_response="test response"
        )

        assert result.success is True
        assert result.index == 1
        assert result.score == 78
        assert result.video_title == sample_ai_response_video["video_title"]
        assert result.rating_label == "Scale"
        assert result.ai_result == sample_ai_response_video
        assert result.raw_response == "test response"

    def test_convert_valid_carousel(self, sample_ai_response_carousel):
        """Should convert valid carousel result correctly."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_carousel,
            raw_response="test"
        )

        assert result.success is True
        assert result.score == 65
        assert result.rating_label == "Iterate"

    def test_convert_kill_verdict(self, sample_ai_response_kill):
        """Should correctly identify Kill verdict."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_kill,
            raw_response="test"
        )

        assert result.rating_label == "Kill"
        assert result.score == 25

    def test_convert_with_hook_text_fallback(self):
        """Should use hook_text when video_title is missing."""
        ai_result = {
            "hook_text": "Hook Text Here",
            "video_title": None,
            "score": 50,
            "verdict": "🟡 ITERATE",
            "metrics": {"views": 1000},
        }

        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=ai_result,
            raw_response="test"
        )

        assert result.video_title == "Hook Text Here"

    def test_convert_empty_result(self):
        """Should handle empty result."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result={},
            raw_response="test"
        )

        assert result.success is False
        assert "Пустой или некорректный результат" in result.error_message

    def test_convert_none_result(self):
        """Should handle None result."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=None,
            raw_response="test"
        )

        assert result.success is False
        assert result.error_message is not None


class TestBuildSummaryReport:
    """Tests for report generation."""

    def test_single_successful_video(self, sample_ai_response_video):
        """Should generate report for single video."""
        results = [
            VideoProcessingResult(
                index=1,
                success=True,
                video_title=sample_ai_response_video["video_title"],
                score=78,
                rating_label="Scale",
                ai_result=sample_ai_response_video,
            )
        ]

        report = build_summary_report(
            results=results,
            saved=1,
            failed=0,
            duplicates=0,
            total_images=1
        )

        assert "1 видео" in report
        assert "1" in report  # Saved count
        assert "78/100" in report
        assert "🟢" in report

    def test_multiple_videos_mixed_results(self):
        """Should generate report for multiple videos with different outcomes."""
        results = [
            VideoProcessingResult(
                index=1,
                success=True,
                video_title="Video 1",
                score=80,
                rating_label="Scale",
                ai_result={"platform": "tiktok"},
            ),
            VideoProcessingResult(
                index=2,
                success=True,
                video_title="Video 2",
                score=45,
                rating_label="Kill",
                ai_result={"platform": "instagram"},
            ),
            VideoProcessingResult(
                index=3,
                success=False,
                error_message="Analysis failed",
            ),
        ]

        report = build_summary_report(
            results=results,
            saved=2,
            failed=1,
            duplicates=0,
            total_images=3
        )

        assert "3 видео" in report
        assert "2" in report  # Saved
        assert "1" in report  # Failed
        assert "🟢" in report  # Scale
        assert "🔴" in report  # Kill
        assert "⚠️" in report  # Error

    def test_duplicate_video_in_report(self, sample_ai_response_video):
        """Should mark duplicate videos in report."""
        results = [
            VideoProcessingResult(
                index=1,
                success=True,
                video_title="Duplicate Video",
                score=70,
                rating_label="Iterate",
                ai_result=sample_ai_response_video,
                is_duplicate=True,
            )
        ]

        report = build_summary_report(
            results=results,
            saved=0,
            failed=0,
            duplicates=1,
            total_images=1
        )

        assert "Данные не изменились" in report
        assert "♻️" in report

    def test_long_title_truncation(self):
        """Should truncate long titles."""
        long_title = "A" * 50

        results = [
            VideoProcessingResult(
                index=1,
                success=True,
                video_title=long_title,
                score=75,
                rating_label="Scale",
                ai_result={"platform": "tiktok"},
            )
        ]

        report = build_summary_report(
            results=results,
            saved=1,
            failed=0,
            duplicates=0,
            total_images=1
        )

        # Title should be truncated with ...
        assert "…" in report or len(report) < 100

    def test_empty_results(self):
        """Should handle empty results gracefully."""
        report = build_summary_report(
            results=[],
            saved=0,
            failed=0,
            duplicates=0,
            total_images=0
        )

        assert "0 видео" in report
        assert "Сохранено: 0" in report


class TestDownloadAllPhotos:
    """Tests for photo downloading."""

    @pytest.mark.asyncio
    async def test_download_single_photo(self):
        """Should download single photo successfully."""
        mock_bot = AsyncMock()
        mock_file = Mock()
        mock_file.file_path = "path/to/file.jpg"
        mock_bot.get_file.return_value = mock_file

        mock_downloaded = Mock()
        mock_downloaded.read.return_value = b"image data"
        mock_bot.download_file.return_value = mock_downloaded

        mock_message = Mock()
        mock_message.photo = [Mock(file_id="file123")]
        mock_message.message_id = 1

        result = await _download_all_photos([mock_message], mock_bot)

        assert len(result) == 1
        assert result[0] == b"image data"

    @pytest.mark.asyncio
    async def test_download_multiple_photos(self):
        """Should download multiple photos in parallel."""
        mock_bot = AsyncMock()

        async def mock_download(file_path):
            mock_data = Mock()
            mock_data.read.return_value = f"data from {file_path}".encode()
            return mock_data

        mock_bot.get_file = AsyncMock(side_effect=[
            Mock(file_path="path/1.jpg"),
            Mock(file_path="path/2.jpg"),
            Mock(file_path="path/3.jpg"),
        ])
        mock_bot.download_file = AsyncMock(side_effect=mock_download)

        messages = [
            Mock(photo=[Mock(file_id="1")], message_id=1),
            Mock(photo=[Mock(file_id="2")], message_id=2),
            Mock(photo=[Mock(file_id="3")], message_id=3),
        ]

        result = await _download_all_photos(messages, mock_bot)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_skip_messages_without_photo(self):
        """Should skip messages that don't have photos."""
        mock_bot = AsyncMock()

        mock_file = Mock()
        mock_file.file_path = "path/to/file.jpg"
        mock_bot.get_file.return_value = mock_file

        mock_downloaded = Mock()
        mock_downloaded.read.return_value = b"image data"
        mock_bot.download_file.return_value = mock_downloaded

        messages = [
            Mock(photo=[Mock(file_id="1")], message_id=1),
            Mock(photo=None, message_id=2),  # No photo
            Mock(photo=[Mock(file_id="3")], message_id=3),
        ]

        result = await _download_all_photos(messages, mock_bot)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_handle_download_error(self):
        """Should handle download errors gracefully."""
        mock_bot = AsyncMock()
        mock_bot.get_file.side_effect = Exception("Network error")

        messages = [
            Mock(photo=[Mock(file_id="1")], message_id=1),
        ]

        result = await _download_all_photos(messages, mock_bot)

        assert len(result) == 0


class TestDataFlowValidation:
    """Tests for validating data flow through the system."""

    def test_ai_result_to_processing_result_structure(self, sample_ai_response_video):
        """AI result should convert to processing result with all required fields."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_video,
            raw_response="test"
        )

        # Verify structure needed for database
        assert result.ai_result is not None
        assert result.ai_result.get("content_type") is not None
        assert result.ai_result.get("platform") is not None
        assert result.ai_result.get("metrics") is not None
        assert result.ai_result.get("score") is not None
        assert result.ai_result.get("verdict") is not None

        # Verify structure needed for sheets
        assert result.video_title is not None
        assert result.score is not None
        assert result.rating_label is not None

    def test_processing_result_to_database_fields(self, sample_ai_response_video):
        """Processing result should contain all fields needed for database."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_video,
            raw_response="test"
        )

        ai_data = result.ai_result

        # Fields used by insert_video
        assert "content_type" in ai_data
        assert "platform" in ai_data
        assert "video_title" in ai_data or "title" in ai_data
        assert "metrics" in ai_data
        assert "score" in ai_data
        assert "analysis" in ai_data
        assert "verdict" in ai_data

        # Metrics fields
        metrics = ai_data.get("metrics", {})
        assert "views" in metrics
        assert "likes" in metrics
        assert "comments" in metrics
        assert "shares" in metrics
        assert "saves" in metrics

    def test_processing_result_to_sheets_fields(self, sample_ai_response_video):
        """Processing result should contain all fields needed for Google Sheets."""
        result = _convert_ai_result_to_processing_result(
            index=1,
            ai_result=sample_ai_response_video,
            raw_response="test"
        )

        ai_data = result.ai_result

        # Fields used by sheets export (columns A-P)
        assert ai_data.get("posted_at") is not None  # B
        assert ai_data.get("content_type") is not None  # C
        assert ai_data.get("platform") is not None  # E
        assert ai_data.get("hook_text") is not None  # F
        assert ai_data.get("hook_type") is not None  # G
        assert ai_data.get("score") is not None  # H
        assert ai_data.get("verdict") is not None  # I

        metrics = ai_data.get("metrics", {})
        assert metrics.get("views") is not None  # J
        assert metrics.get("likes") is not None  # K
        assert metrics.get("comments") is not None  # L
        assert metrics.get("shares") is not None  # M
