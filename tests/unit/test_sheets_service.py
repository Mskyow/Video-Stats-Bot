"""
Unit tests for Google Sheets Service.

Tests cover:
- Row data mapping and formatting
- Credentials loading
- Export functionality
- Error handling
- Data structure validation for sheets export
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

import pytest

# Ensure there's an event loop for asyncio.Queue creation
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Ensure fresh import
if "src.services.sheets_service" in sys.modules:
    del sys.modules["src.services.sheets_service"]

# Mock config before import
mock_config = MagicMock()
mock_config.GOOGLE_SHEET_CREDENTIALS_PATH = None
mock_config.GOOGLE_CREDENTIALS_JSON = None
mock_config.GOOGLE_SHEET_ID = "test-sheet-id"
mock_config.GOOGLE_SHEET_WORKSHEET_NAME = "TestSheet"
mock_config.SHEETS_WRITE_DELAY = 1.0
sys.modules["src.config"] = mock_config

from src.services.sheets_service import (
    _get_credentials,
    _get_client,
    _get_worksheet,
    export_video_to_sheet,
    queue_export,
)


class TestCredentialsLoading:
    """Tests for Google credentials loading."""

    @patch("src.services.sheets_service.json.loads")
    @patch("src.services.sheets_service.ServiceAccountCredentials")
    def test_credentials_from_json_env(self, mock_creds_class, mock_json_loads):
        """Should load credentials from GOOGLE_CREDENTIALS_JSON env var."""
        mock_json_loads.return_value = {"type": "service_account"}
        mock_creds = Mock()
        mock_creds_class.from_json_keyfile_dict.return_value = mock_creds

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}'):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                result = _get_credentials()

        assert result == mock_creds
        mock_creds_class.from_json_keyfile_dict.assert_called_once()

    @patch("src.services.sheets_service.Path")
    @patch("src.services.sheets_service.ServiceAccountCredentials")
    def test_credentials_from_file(self, mock_creds_class, mock_path_class):
        """Should load credentials from file when env var not set."""
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path_class.return_value = mock_path
        mock_creds = Mock()
        mock_creds_class.from_json_keyfile_name.return_value = mock_creds

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", "/path/to/creds.json"):
                result = _get_credentials()

        assert result == mock_creds
        mock_creds_class.from_json_keyfile_name.assert_called_once()

    def test_credentials_error_when_not_configured(self):
        """Should raise error when no credentials configured."""
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                with pytest.raises(ValueError) as exc_info:
                    _get_credentials()
                assert "Не заданы credentials" in str(exc_info.value)

    @patch("src.services.sheets_service.json.loads")
    def test_credentials_error_invalid_json(self, mock_json_loads):
        """Should raise error when JSON is invalid."""
        mock_json_loads.side_effect = json.JSONDecodeError("test", "doc", 0)

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "invalid json"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                with pytest.raises(ValueError) as exc_info:
                    _get_credentials()
                assert "Невалидный JSON" in str(exc_info.value)


class TestClientAndWorksheet:
    """Tests for client and worksheet retrieval."""

    @patch("src.services.sheets_service._get_credentials")
    @patch("src.services.sheets_service.gspread")
    def test_get_client(self, mock_gspread, mock_get_creds):
        """Should create authorized client."""
        mock_creds = Mock()
        mock_get_creds.return_value = mock_creds
        mock_client = Mock()
        mock_gspread.authorize.return_value = mock_client

        result = _get_client()

        assert result == mock_client
        mock_gspread.authorize.assert_called_once_with(mock_creds)

    @patch("src.services.sheets_service._get_client")
    def test_get_worksheet(self, mock_get_client):
        """Should open worksheet by ID and name."""
        mock_client = Mock()
        mock_spreadsheet = Mock()
        mock_worksheet = Mock()
        mock_client.open_by_key.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_get_client.return_value = mock_client

        with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test-sheet-id"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_WORKSHEET_NAME", "TestWorksheet"):
                result = _get_worksheet(mock_client)

        assert result == mock_worksheet
        mock_client.open_by_key.assert_called_once_with("test-sheet-id")
        mock_spreadsheet.worksheet.assert_called_once_with("TestWorksheet")


class TestRowDataMapping:
    """Tests for mapping AI response data to Google Sheets columns."""

    def test_video_data_mapping(self, sample_ai_response_video):
        """Should correctly map video data to expected column structure."""
        # Expected columns A-P based on sheets_service.py
        metrics = sample_ai_response_video["metrics"]

        # A: Processed At (generated)
        # B: Posted At
        assert sample_ai_response_video["posted_at"] is not None

        # C: Content Type
        assert sample_ai_response_video["content_type"] in ["video", "carousel"]

        # D: Age (Hours) - calculated from posted_at
        posted_at = sample_ai_response_video["posted_at"]
        assert posted_at is not None

        # E: Platform
        assert sample_ai_response_video["platform"] is not None

        # F: Video Title / Hook Text
        hook_text = sample_ai_response_video.get("hook_text")
        video_title = sample_ai_response_video.get("video_title")
        assert hook_text is not None or video_title is not None

        # G: Hook Type
        assert sample_ai_response_video["hook_type"] in ["short", "medium", "long"]

        # H: Score
        assert isinstance(sample_ai_response_video["score"], (int, float))
        assert 0 <= sample_ai_response_video["score"] <= 10

        # I: Verdict
        assert sample_ai_response_video["verdict"] is not None

        # J: Views
        assert metrics["views"] is not None

        # K: Likes
        assert metrics["likes"] is not None

        # L: Comments
        assert metrics["comments"] is not None

        # M: Shares
        assert metrics["shares"] is not None

        # N: Retention 3s
        assert "retention_3s" in metrics

        # O: Avg Watch Time (%)
        assert "avg_watch_time_pct" in metrics

        # P: Engagement Rate (%) - calculated
        if metrics["views"] > 0:
            er = (metrics["likes"] + metrics["comments"] + metrics["shares"] + metrics["saves"]) / metrics["views"] * 100
            assert er >= 0

    def test_carousel_data_mapping(self, sample_ai_response_carousel):
        """Should correctly map carousel data with photos_viewed."""
        metrics = sample_ai_response_carousel["metrics"]

        assert sample_ai_response_carousel["content_type"] == "carousel"
        assert metrics["photos_viewed"] is not None
        assert metrics["total_photos"] is not None

    def test_calculated_er_format(self, sample_ai_response_video):
        """Should calculate ER with one decimal place."""
        metrics = sample_ai_response_video["metrics"]
        views = metrics.get("views", 0)
        if views > 0:
            er = (metrics["likes"] + metrics["shares"] + metrics["comments"] + metrics["saves"]) / views * 100
            er_str = f"{er:.1f}%"
            assert "%" in er_str
            # Verify one decimal place
            decimal_part = er_str.split(".")[1].replace("%", "")
            assert len(decimal_part) == 1


class TestSyncExportToSheet:
    """Tests for synchronous export to Google Sheets."""

    @patch("src.services.sheets_service._get_client")
    @patch("src.services.sheets_service._get_worksheet")
    def test_export_success(self, mock_get_worksheet, mock_get_client, sample_ai_response_video):
        """Should successfully export video data to sheet."""
        mock_worksheet = Mock()
        mock_worksheet.append_row = Mock()
        mock_get_worksheet.return_value = mock_worksheet

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test-sheet"):
                result = export_video_to_sheet(sample_ai_response_video)

        assert result is True
        mock_worksheet.append_row.assert_called_once()

        # Verify row structure
        call_args = mock_worksheet.append_row.call_args[0][0]
        assert len(call_args) == 16  # Columns A-P

    @patch("src.services.sheets_service._get_client")
    @patch("src.services.sheets_service._get_worksheet")
    def test_export_carousel(self, mock_get_worksheet, mock_get_client, sample_ai_response_carousel):
        """Should export carousel with correct content type."""
        mock_worksheet = Mock()
        mock_worksheet.append_row = Mock()
        mock_get_worksheet.return_value = mock_worksheet

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test-sheet"):
                result = export_video_to_sheet(sample_ai_response_carousel)

        assert result is True
        call_args = mock_worksheet.append_row.call_args[0][0]
        assert call_args[2] == "Carousel"  # Column C: Content Type

    @patch("src.services.sheets_service._get_client")
    @patch("src.services.sheets_service._get_worksheet")
    def test_export_with_missing_fields(self, mock_get_worksheet, mock_get_client):
        """Should handle missing fields gracefully."""
        mock_worksheet = Mock()
        mock_worksheet.append_row = Mock()
        mock_get_worksheet.return_value = mock_worksheet

        # Minimal data
        minimal_data = {
            "content_type": "video",
            "metrics": {"views": 100},
        }

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test-sheet"):
                result = export_video_to_sheet(minimal_data)

        assert result is True
        call_args = mock_worksheet.append_row.call_args[0][0]
        assert len(call_args) == 16
        # Check defaults for missing fields
        assert call_args[2] == "Video"  # Default content type
        assert call_args[9] == 100  # Views

    def test_export_skipped_when_not_configured(self):
        """Should skip export when credentials not configured."""
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                result = export_video_to_sheet({})

        assert result is False

    @patch("src.services.sheets_service._get_client")
    def test_export_handles_worksheet_not_found(self, mock_get_client):
        """Should handle worksheet not found error."""
        import gspread
        mock_get_client.side_effect = gspread.WorksheetNotFound("Sheet not found")

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test-sheet"):
                result = export_video_to_sheet({"content_type": "video", "metrics": {}})

        assert result is False

    @patch("src.services.sheets_service._get_client")
    def test_export_handles_file_not_found(self, mock_get_client):
        """Should handle file not found error."""
        mock_get_client.side_effect = FileNotFoundError("Credentials file not found")

        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", "/fake/path.json"):
                result = export_video_to_sheet({"content_type": "video", "metrics": {}})

        assert result is False


class TestRowStructure:
    """Tests for row structure validation."""

    def test_row_has_16_columns(self, sample_ai_response_video):
        """Row should have exactly 16 columns (A-P)."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                assert len(call_args) == 16

    def test_column_a_processed_at(self, sample_ai_response_video):
        """Column A should be processed timestamp."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                # Should be a timestamp string
                assert len(call_args[0]) > 0  # processed_at

    def test_column_c_content_type_video(self, sample_ai_response_video):
        """Column C should show 'Video' for video content."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                assert call_args[2] == "Video"

    def test_column_c_content_type_carousel(self, sample_ai_response_carousel):
        """Column C should show 'Carousel' for carousel content."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_carousel)

                call_args = mock_worksheet.append_row.call_args[0][0]
                assert call_args[2] == "Carousel"

    def test_column_n_retention_format(self, sample_ai_response_video):
        """Column N should format retention with % sign."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                retention = call_args[13]  # Column N (0-indexed: 13)
                assert "%" in str(retention)

    def test_column_o_avg_watch_time_format(self, sample_ai_response_video):
        """Column O should format avg watch time with % sign."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                awt = call_args[14]  # Column O (0-indexed: 14)
                if awt != "Not Found":
                    assert "%" in str(awt)

    def test_column_p_er_format(self, sample_ai_response_video):
        """Column P should format ER with % and one decimal."""
        with patch("src.services.sheets_service._get_client") as mock_get_client:
            with patch("src.services.sheets_service._get_worksheet") as mock_get_worksheet:
                mock_worksheet = Mock()
                mock_worksheet.append_row = Mock()
                mock_get_worksheet.return_value = mock_worksheet

                with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test"):
                    with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "test"):
                        export_video_to_sheet(sample_ai_response_video)

                call_args = mock_worksheet.append_row.call_args[0][0]
                er = call_args[15]  # Column P (0-indexed: 15)
                if er != "Not Found":
                    assert "%" in str(er)


class TestAsyncQueueExport:
    """Tests for async queue export."""

    @pytest.mark.asyncio
    async def test_queue_export_adds_to_queue(self, sample_ai_response_video):
        """Should add data to export queue."""
        import asyncio
        test_queue = asyncio.Queue()

        with patch("src.services.sheets_service._export_queue", test_queue):
            await queue_export(sample_ai_response_video)

            # Verify data was added to queue
            assert test_queue.qsize() == 1
            queued_data = await test_queue.get()
            assert queued_data == sample_ai_response_video


class TestDataCompatibility:
    """Tests for data compatibility between AI response and sheets format."""

    def test_ai_response_maps_to_all_sheets_columns(self, sample_ai_response_video):
        """AI response should contain data for all 16 columns."""
        # Columns A-P mapping verification
        required_mappings = {
            "posted_at": sample_ai_response_video.get("posted_at"),  # B
            "content_type": sample_ai_response_video.get("content_type"),  # C
            "platform": sample_ai_response_video.get("platform"),  # E
            "hook_text": sample_ai_response_video.get("hook_text"),  # F
            "hook_type": sample_ai_response_video.get("hook_type"),  # G
            "score": sample_ai_response_video.get("score"),  # H
            "verdict": sample_ai_response_video.get("verdict"),  # I
        }

        for col, value in required_mappings.items():
            assert value is not None, f"Missing data for column mapping: {col}"

        # Metrics mapping
        metrics = sample_ai_response_video.get("metrics", {})
        metric_mappings = {
            "views": metrics.get("views"),  # J
            "likes": metrics.get("likes"),  # K
            "comments": metrics.get("comments"),  # L
            "shares": metrics.get("shares"),  # M
            "retention_3s": metrics.get("retention_3s"),  # N
            "avg_watch_time_pct": metrics.get("avg_watch_time_pct"),  # O
        }

        for col, value in metric_mappings.items():
            assert value is not None, f"Missing metric for column: {col}"
