from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import Mock, patch, MagicMock

import pytest
from gspread.exceptions import WorksheetNotFound

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

if "src.services.sheets_service" in sys.modules:
    del sys.modules["src.services.sheets_service"]

mock_config = MagicMock()
mock_config.GOOGLE_SHEET_CREDENTIALS_PATH = None
mock_config.GOOGLE_CREDENTIALS_JSON = None
mock_config.GOOGLE_SHEET_ID = "test-sheet-id"
mock_config.GOOGLE_SHEET_WORKSHEET_NAME = "Marketing Funnels"
mock_config.TIKTOK_SEARCH_IMPRESSIONS_RATE = 0.008
mock_config.YOUTUBE_SEARCH_IMPRESSIONS_RATE = 0.005
mock_config.INSTAGRAM_SEARCH_IMPRESSIONS_RATE = 0.004
sys.modules["src.config"] = mock_config

from src.services.sheets_service import (
    CSV_REQUIRED_COLUMNS,
    MARKETING_FUNNELS_COLUMNS,
    VIDEO_ANALYSIS_COLUMNS,
    _build_row,
    _calculate_age_hours,
    _get_client,
    _get_credentials,
    export_video_to_sheet,
    import_marketing_funnel_csv_rows,
    queue_export,
    validate_normalized_csv_headers,
)


class FakeWorksheet:
    def __init__(self, title: str):
        self.title = title
        self.values: list[list[str]] = []

    def update(self, range_name=None, values=None, value_input_option=None):
        row_num = int("".join(ch for ch in range_name.split(":")[0] if ch.isdigit()))
        while len(self.values) < row_num:
            self.values.append([])
        self.values[row_num - 1] = list(values[0])

    def append_row(self, row, value_input_option=None):
        self.values.append([str(cell) for cell in row])

    def get_all_values(self):
        return self.values


class FakeSpreadsheet:
    def __init__(self):
        self.sheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, title: str):
        if title not in self.sheets:
            raise WorksheetNotFound("not found")
        return self.sheets[title]

    def add_worksheet(self, title: str, rows: int, cols: int):
        worksheet = FakeWorksheet(title)
        self.sheets[title] = worksheet
        return worksheet


class FakeClient:
    def __init__(self, spreadsheet: FakeSpreadsheet):
        self._spreadsheet = spreadsheet

    def open_by_key(self, key: str):
        return self._spreadsheet


class TestCredentials:
    @patch("src.services.sheets_service.json.loads")
    @patch("src.services.sheets_service.ServiceAccountCredentials")
    def test_credentials_from_json_env(self, mock_creds_class, mock_json_loads):
        mock_json_loads.return_value = {"type": "service_account"}
        mock_creds = Mock()
        mock_creds_class.from_json_keyfile_dict.return_value = mock_creds
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}'):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                result = _get_credentials()
        assert result == mock_creds

    @patch("src.services.sheets_service.ServiceAccountCredentials")
    @patch("src.services.sheets_service.Path")
    def test_credentials_from_file(self, mock_path_class, mock_creds_class):
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path_class.return_value = mock_path
        mock_creds = Mock()
        mock_creds_class.from_json_keyfile_name.return_value = mock_creds
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", "/tmp/creds.json"):
                result = _get_credentials()
        assert result == mock_creds

    def test_credentials_missing(self):
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", None):
            with patch("src.services.sheets_service.GOOGLE_SHEET_CREDENTIALS_PATH", None):
                with pytest.raises(FileNotFoundError):
                    _get_credentials()

    @patch("src.services.sheets_service._get_credentials")
    @patch("src.services.sheets_service.gspread")
    def test_get_client(self, mock_gspread, mock_get_credentials):
        creds = Mock()
        mock_get_credentials.return_value = creds
        client = Mock()
        mock_gspread.authorize.return_value = client
        assert _get_client() == client


class TestVideoAnalysisRows:
    def test_build_video_analysis_row(self, sample_ai_response_video):
        row = _build_row(sample_ai_response_video)
        assert len(row) == len(VIDEO_ANALYSIS_COLUMNS)
        assert row[1] == "TikTok"
        assert row[2] == sample_ai_response_video["video_title"]
        assert row[4] == "video"
        assert row[5] == "15000"
        assert row[6] == "45"
        assert row[7] == "30"
        assert row[8] == "72.5%"
        assert row[9] == "65%"
        assert row[11] == "7.8"
        assert "SCALE" in row[12]

    def test_calculate_age_hours_with_prefix(self):
        age = _calculate_age_hours("Posted on Feb 6, 2026, 12:53 PM")
        assert age is not None


class TestCsvValidation:
    def test_validate_headers_missing(self):
        missing = validate_normalized_csv_headers(["date", "channel", "store"])
        assert missing == ["search_impressions", "product_page_views", "installs"]

    def test_validate_headers_ok_with_human_names(self):
        missing = validate_normalized_csv_headers(
            ["Date", "Channel", "Store", "Search Impressions", "Product Page Views", "Installs"]
        )
        assert missing == []


class TestSheetFlows:
    @pytest.fixture
    def fake_spreadsheet(self):
        return FakeSpreadsheet()

    @pytest.fixture
    def fake_client(self, fake_spreadsheet):
        return FakeClient(fake_spreadsheet)

    @patch("src.services.sheets_service._get_client")
    def test_export_video_analysis_and_social_funnel(self, mock_get_client, fake_client, fake_spreadsheet, sample_ai_response_video):
        mock_get_client.return_value = fake_client
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "sheet-id"):
                assert export_video_to_sheet(sample_ai_response_video) is True

        video_sheet = fake_spreadsheet.sheets["Video Analysis"]
        funnel_sheet = fake_spreadsheet.sheets["Marketing Funnels"]

        assert video_sheet.values[0] == VIDEO_ANALYSIS_COLUMNS
        assert video_sheet.values[1][1] == "TikTok"

        assert funnel_sheet.values[0] == MARKETING_FUNNELS_COLUMNS
        rows = funnel_sheet.values[1:]
        assert any(row[1] == "TikTok Viral" and row[2] == "App Store" for row in rows)
        assert any(row[1] == "TikTok Viral" and row[2] == "Google Play" for row in rows)
        assert any(row[1] == "TOTAL" and row[2] == "App Store" for row in rows)
        assert any(row[1] == "TOTAL" and row[2] == "Google Play" for row in rows)

    @patch("src.services.sheets_service._get_client")
    def test_import_marketing_funnel_csv_rows_upserts(self, mock_get_client, fake_client, fake_spreadsheet):
        mock_get_client.return_value = fake_client
        with patch("src.services.sheets_service.GOOGLE_CREDENTIALS_JSON", "test-creds"):
            with patch("src.services.sheets_service.GOOGLE_SHEET_ID", "sheet-id"):
                first = import_marketing_funnel_csv_rows(
                    [
                        {
                            "date": "2026-05-12",
                            "channel": "Store Organic",
                            "store": "App Store",
                            "search_impressions": "150",
                            "product_page_views": "40",
                            "installs": "12",
                        }
                    ]
                )
                second = import_marketing_funnel_csv_rows(
                    [
                        {
                            "date": "2026-05-12",
                            "channel": "Store Organic",
                            "store": "App Store",
                            "search_impressions": "200",
                            "product_page_views": "50",
                            "installs": "15",
                        }
                    ]
                )

        assert first["created"] == 1
        assert second["updated"] == 1
        funnel_rows = fake_spreadsheet.sheets["Marketing Funnels"].values[1:]
        organic_rows = [row for row in funnel_rows if row[1] == "Store Organic" and row[2] == "App Store"]
        assert len(organic_rows) == 1
        assert organic_rows[0][4] == "200"
        assert organic_rows[0][5] == "50"
        assert organic_rows[0][6] == "15"


class TestQueue:
    @pytest.mark.asyncio
    async def test_queue_export_adds_item(self, sample_ai_response_video):
        test_queue = asyncio.Queue()
        with patch("src.services.sheets_service.get_sheets_queue", return_value=test_queue):
            queue_export(sample_ai_response_video)
            assert test_queue.qsize() == 1
            item = await test_queue.get()
            assert item["kind"] == "video_analysis"
            assert item["payload"] == sample_ai_response_video
