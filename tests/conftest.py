"""
Pytest configuration and shared fixtures.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

# Add src to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# =============================================================================
# FIXTURES FOR AI/OPENROUTER SERVICE
# =============================================================================

@pytest.fixture
def sample_ai_response_video() -> dict[str, Any]:
    """
    Sample AI response for video content type.
    Matches the expected structure from SYSTEM_PROMPT.
    """
    return {
        "content_type": "video",
        "hook_text": "3 способа заработать на нейросетях",
        "hook_type": "short",
        "video_title": "Полное руководство по AI в 2024",
        "posted_at": "2024-01-15 14:30:00",
        "platform": "tiktok",
        "metrics": {
            "views": 15000,
            "likes": 1200,
            "comments": 45,
            "shares": 30,
            "saves": 80,
            "retention_3s": 72.5,
            "avg_watch_time_sec": 8.5,
            "avg_watch_time_pct": 65.0,
            "photos_viewed": None,
            "total_photos": None,
        },
        "score": 7.8,
        "verdict": "🚀 SCALE HARD",
        "analysis": "Отличный хук с высоким retention. Видео набирает хорошую аудиторию.",
        "recommendations": [
            "Продолжай в том же духе",
            "Попробуй увеличить частоту публикаций"
        ],
        "raw_response": '{"some": "json"}'
    }


@pytest.fixture
def sample_ai_response_carousel() -> dict[str, Any]:
    """
    Sample AI response for carousel content type.
    """
    return {
        "content_type": "carousel",
        "hook_text": "10 инструментов для продуктивности которые изменят твою жизнь навсегда",
        "hook_type": "medium",
        "video_title": "Инструменты продуктивности 2024",
        "posted_at": "2024-01-20",
        "platform": "instagram",
        "metrics": {
            "views": 8500,
            "likes": 680,
            "comments": 52,
            "shares": 45,
            "saves": 120,
            "retention_3s": None,
            "avg_watch_time_sec": None,
            "avg_watch_time_pct": 58.0,
            "photos_viewed": 3.2,
            "total_photos": 5,
        },
        "score": 6.5,
        "verdict": "🟡 ITERATE",
        "analysis": "Хороший ER, но completion rate можно улучшить. Пробуй более цепляющие слайды.",
        "recommendations": [
            "Усиль CTA на последних слайдах",
            "Добавь интерактивные элементы"
        ],
        "raw_response": '{"some": "carousel_json"}'
    }


@pytest.fixture
def sample_ai_response_kill() -> dict[str, Any]:
    """
    Sample AI response with KILL verdict (poor performance).
    """
    return {
        "content_type": "video",
        "hook_text": "Как я провел выходные",
        "hook_type": "short",
        "video_title": "Мои выходные",
        "posted_at": "2024-01-10",
        "platform": "tiktok",
        "metrics": {
            "views": 500,
            "likes": 12,
            "comments": 0,
            "shares": 1,
            "saves": 2,
            "retention_3s": 35.0,
            "avg_watch_time_sec": 2.1,
            "avg_watch_time_pct": 25.0,
            "photos_viewed": None,
            "total_photos": None,
        },
        "score": 2.5,
        "verdict": "🔴 KILL HOOK",
        "analysis": "Низкий retention указывает на слабый хук. Нужно полностью переделать начало.",
        "recommendations": [
            "Начни с боли аудитории",
            "Используй pattern interrupt"
        ]
    }


@pytest.fixture
def sample_ai_batch_response() -> list[dict[str, Any]]:
    """
    Sample batch response with multiple videos.
    """
    return [
        {
            "content_type": "video",
            "hook_text": "Видео 1 - тестовый хук",
            "hook_type": "short",
            "video_title": "Тестовое видео 1",
            "posted_at": "2024-01-15",
            "platform": "tiktok",
            "metrics": {
                "views": 10000,
                "likes": 800,
                "comments": 30,
                "shares": 20,
                "saves": 50,
                "retention_3s": 70.0,
                "avg_watch_time_pct": 60.0,
            },
            "score": 7.5,
            "verdict": "🚀 SCALE HARD",
            "analysis": "Хорошие показатели",
            "recommendations": ["Масштабируй"]
        },
        {
            "content_type": "carousel",
            "hook_text": "Вторая история о продуктивности с более длинным текстом для проверки",
            "hook_type": "long",
            "video_title": "Тестовый карусель 2",
            "posted_at": "2024-01-16",
            "platform": "instagram",
            "metrics": {
                "views": 5000,
                "likes": 400,
                "comments": 25,
                "shares": 15,
                "saves": 60,
                "avg_watch_time_pct": 45.0,
                "photos_viewed": 2.8,
                "total_photos": 6,
            },
            "score": 5.5,
            "verdict": "✂️ FIX BODY",
            "analysis": "Нужны правки",
            "recommendations": ["Улучши середину"]
        }
    ]


@pytest.fixture
def mock_openrouter_response() -> Mock:
    """
    Mock for successful OpenRouter API response.
    """
    mock = Mock()
    mock.status_code = 200
    mock.raise_for_status = Mock()
    mock.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps([
                    {
                        "content_type": "video",
                        "hook_text": "Test hook",
                        "hook_type": "short",
                        "video_title": "Test video",
                        "posted_at": "2024-01-15",
                        "platform": "tiktok",
                        "metrics": {
                            "views": 1000,
                            "likes": 100,
                            "comments": 10,
                            "shares": 5,
                            "saves": 15,
                            "retention_3s": 70.0,
                            "avg_watch_time_pct": 60.0,
                        },
                        "score": 7.0,
                        "verdict": "🟡 ITERATE",
                        "analysis": "Test analysis",
                        "recommendations": ["Test recommendation"]
                    }
                ])
            }
        }]
    }
    return mock


# =============================================================================
# FIXTURES FOR DATABASE/SUPABASE
# =============================================================================

@pytest.fixture
def mock_supabase_client() -> MagicMock:
    """
    Mock Supabase client for database testing.
    """
    mock_client = MagicMock()
    
    # Mock table operations
    mock_table = MagicMock()
    mock_client.table.return_value = mock_table
    
    # Mock select chain
    mock_select_result = MagicMock()
    mock_select_result.execute.return_value = MagicMock(data=[])
    mock_table.select.return_value = mock_select_result
    
    # Mock insert chain
    mock_insert_result = MagicMock()
    mock_insert_result.execute.return_value = MagicMock(data=[{"id": "test-uuid-123"}])
    mock_table.insert.return_value = mock_insert_result
    
    # Mock eq chain
    mock_eq_result = MagicMock()
    mock_eq_result.execute.return_value = MagicMock(data=[])
    mock_table.eq.return_value = mock_eq_result
    
    return mock_client


@pytest.fixture
def sample_db_video_record() -> dict[str, Any]:
    """
    Sample video record as stored in database.
    """
    return {
        "id": "test-uuid-123",
        "user_id": 123456789,
        "platform": "tiktok",
        "title": "Test Video Title",
        "metrics": {
            "views": 15000,
            "likes": 1200,
            "comments": 45,
            "shares": 30,
            "saves": 80,
            "retention_3s": 72.5,
            "avg_watch_time_pct": 65.0,
            "hook_type": "short",
            "age_hours": 48.5,
        },
        "score": 7.8,
        "analysis": "Отличный хук с высоким retention.",
        "verdict": "🚀 SCALE HARD",
        "hook_score": "good",
        "detailed_analysis": {
            "tier_1": {"hook_3s": {"rating": "good", "value": 72.5}},
            "tier_2": {},
            "expert_heuristics": [],
            "recommendations": ["Продолжай в том же духе"]
        },
        "video_duration_sec": None,
        "content_type": "video",
        "hook_text": "3 способа заработать на нейросетях",
        "created_at": "2024-01-15T10:00:00Z",
        "raw_ai_response": None,
    }


@pytest.fixture
def sample_existing_video_for_update() -> dict[str, Any]:
    """
    Sample existing video record that should trigger UPDATE status.
    """
    return {
        "id": "existing-uuid-456",
        "user_id": 123456789,
        "title": "Test Video Title",
        "platform": "tiktok",
        "metrics": {
            "views": 10000,  # Old views
            "likes": 800,
            "comments": 30,
            "shares": 20,
            "saves": 50,
            "posted_at": "2024-01-15",
        },
        "created_at": "2024-01-15T10:00:00Z",
    }


@pytest.fixture
def sample_existing_video_duplicate() -> dict[str, Any]:
    """
    Sample existing video record that should trigger DUPLICATE status.
    """
    return {
        "id": "existing-uuid-789",
        "user_id": 123456789,
        "title": "Test Video Title",
        "platform": "tiktok",
        "metrics": {
            "views": 15000,  # Same views - no growth
            "likes": 800,
            "comments": 30,
            "shares": 20,
            "saves": 50,
            "posted_at": "2024-01-15",
        },
        "created_at": "2024-01-15T10:00:00Z",
    }


# =============================================================================
# FIXTURES FOR GOOGLE SHEETS
# =============================================================================

@pytest.fixture
def sample_sheets_row_data() -> dict[str, Any]:
    """
    Sample data formatted for Google Sheets export.
    """
    return {
        "processed_at": "2024-01-15 14:30:00",
        "posted_at": "2024-01-15",
        "content_type": "Video",
        "age_hours": "48.0",
        "platform": "Tiktok",
        "video_title": "3 способа заработать на нейросетях",
        "hook_type": "short",
        "score": 7.8,
        "verdict": "🚀 SCALE HARD",
        "views": 15000,
        "likes": 1200,
        "comments": 45,
        "shares": 30,
        "retention_3s": "72.5%",
        "avg_watch_time": "65.0%",
        "er": "9.0%",
    }


@pytest.fixture
def mock_gspread_client() -> MagicMock:
    """
    Mock gspread client for Google Sheets testing.
    """
    mock_client = MagicMock()
    mock_spreadsheet = MagicMock()
    mock_worksheet = MagicMock()
    
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_spreadsheet.worksheet.return_value = mock_worksheet
    mock_worksheet.append_row = MagicMock()
    
    return mock_client


# =============================================================================
# ENVIRONMENT FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def setup_test_env():
    """
    Set up test environment variables.
    """
    original_env = dict(os.environ)
    
    # Set test environment variables
    os.environ["OPENROUTER_API_KEY"] = "test-api-key"
    os.environ["OPENROUTER_MODEL"] = "test-model"
    os.environ["SUPABASE_URL"] = "https://test.supabase.co"
    os.environ["SUPABASE_KEY"] = "test-key"
    os.environ["GOOGLE_SHEET_ID"] = "test-sheet-id"
    os.environ["GOOGLE_SHEET_WORKSHEET_NAME"] = "TestSheet"
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)
