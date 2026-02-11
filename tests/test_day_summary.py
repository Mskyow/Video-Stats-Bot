"""
Tests for evidence-driven day summary module.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.ai.day_summary import (
    _build_compact_dataset,
    _build_evidence,
    _build_hook_clusters,
    _build_summary_prompt,
    _cache,
    _parse_summary_response,
    _render_summary_payload,
    _validate_summary_payload,
    generate_day_summary,
)


@pytest.fixture
def sample_videos() -> list[dict]:
    return [
        {
            "id": "v1",
            "platform": "tiktok",
            "hook_text": "3 способа заработать на ai без бюджета",
            "score": 8.7,
            "verdict": "🚀 SCALE",
            "video_duration_sec": 14,
            "raw_ai_response": "big raw block should be ignored",
            "analysis": "long analysis should not be used in compact dataset",
            "metrics": {
                "hook_type": "short",
                "views": 14000,
                "retention_3s": 77.0,
                "completion_rate": 62.0,
                "share_rate": 2.1,
                "save_rate": 2.8,
                "comment_rate": 0.4,
                "aggregated_er": 9.2,
            },
        },
        {
            "id": "v2",
            "platform": "instagram reels",
            "hook_text": "3 способа заработать на AI уже сегодня",
            "score": 8.2,
            "verdict": "🚀 SCALE",
            "video_duration_sec": 16,
            "metrics": {
                "hook_type": "short",
                "views": 9000,
                "retention_3s": 73.0,
                "completion_rate": 59.0,
                "share_rate": 1.9,
                "save_rate": 2.4,
                "comment_rate": 0.5,
                "aggregated_er": 8.6,
            },
        },
        {
            "id": "v3",
            "platform": "tiktok",
            "hook_text": "ошибка воронки которая режет просмотры",
            "score": 6.1,
            "verdict": "🟡 ITERATE",
            "video_duration_sec": 22,
            "metrics": {
                "hook_type": "medium",
                "views": 5200,
                "retention_3s": 61.0,
                "completion_rate": 43.0,
                "share_rate": 1.0,
                "save_rate": 1.5,
                "comment_rate": 0.2,
                "aggregated_er": 6.4,
            },
        },
        {
            "id": "v4",
            "platform": "tiktok",
            "hook_text": "длинная история без сильного начала",
            "score": 3.8,
            "verdict": "🔴 KILL",
            "video_duration_sec": 35,
            "metrics": {
                "hook_type": "long",
                "views": 2100,
                "retention_3s": 44.0,
                "completion_rate": 27.0,
                "share_rate": 0.3,
                "save_rate": 0.7,
                "comment_rate": 0.1,
                "aggregated_er": 3.1,
            },
        },
    ]


@pytest.fixture
def minimal_videos() -> list[dict]:
    return [
        {
            "id": "single",
            "platform": "tiktok",
            "hook_text": "минимум данных",
            "score": 7.0,
            "verdict": "🟡 ITERATE",
            "metrics": {"hook_type": "short", "views": 3000},
        }
    ]


class TestCompactDataset:
    def test_compact_dataset_excludes_noisy_fields(self, sample_videos: list[dict]) -> None:
        rows = _build_compact_dataset(sample_videos)
        assert len(rows) == 4
        first = rows[0]
        assert "raw_ai_response" not in first
        assert "analysis" not in first
        assert first["hook_text"]
        assert first["platform"] in {"TikTok", "Instagram", "Other", "YouTube Shorts"}
        assert "_hook_tokens" in first

    def test_hook_clusters_detect_similar_hooks(self, sample_videos: list[dict]) -> None:
        rows = _build_compact_dataset(sample_videos)
        clusters = _build_hook_clusters(rows, min_cluster_size=2, similarity_threshold=0.3)
        assert clusters
        assert clusters[0]["size"] >= 2
        assert "avg_score" in clusters[0]
        assert "examples" in clusters[0]

    def test_evidence_contains_group_metrics(self, sample_videos: list[dict]) -> None:
        rows = _build_compact_dataset(sample_videos)
        evidence = _build_evidence(rows)
        assert evidence["overview"]["total_videos"] == 4
        assert evidence["group_metrics"]["hook_type"]
        assert evidence["group_metrics"]["platform"]
        assert evidence["top_examples"]
        assert evidence["compact_videos"]

    def test_prompt_contains_evidence_blocks(self, sample_videos: list[dict]) -> None:
        rows = _build_compact_dataset(sample_videos)
        evidence = _build_evidence(rows)
        prompt = _build_summary_prompt(evidence)
        assert "EVIDENCE OVERVIEW" in prompt
        assert "EVIDENCE GROUP METRICS" in prompt
        assert "COMPACT DATASET (ALL DAY VIDEOS)" in prompt
        assert "Верни строго JSON-объект" in prompt


class TestSummaryPayloadValidation:
    def test_validate_payload_requires_numbers(self) -> None:
        payload = {
            "overview": "Общая картина без цифр",
            "best_hooks": ["Сработало хорошо"],
            "worked_today": ["Есть паттерны"],
            "best_cases": ["Есть кейс"],
            "recommendations": ["Делай больше", "Тестируй еще"],
            "evidence_refs": ["cluster"],
        }
        valid, errors = _validate_summary_payload(payload)
        assert not valid
        assert any("numeric evidence" in err for err in errors)

    def test_parse_summary_response_json(self) -> None:
        raw = """
        {
          "overview": "За сутки 4 видео, avg score 6.7/10.",
          "best_hooks": ["Short hooks: retention 75% > 70%"],
          "worked_today": ["Share rate 1.9% > 1.5%"],
          "best_cases": ["Case A: score 8.7, retention 77%"],
          "recommendations": ["Scale short hooks with share 1.9%.", "Rework long hooks below 58%."],
          "evidence_refs": ["hook_type.short", "hook_type.long"]
        }
        """
        parsed = _parse_summary_response(raw)
        assert parsed is not None
        assert "Общая картина" in parsed
        assert "Лучшие хуки" in parsed
        assert "Лучшие кейсы за сегодня" in parsed
        assert "Что делать сегодня" in parsed

    def test_render_summary_escapes_untrusted_html(self) -> None:
        payload = {
            "overview": "За сутки retention 3s <58% в 2 роликах.",
            "best_hooks": ["Hook <strong>alpha</strong> дал 72%."],
            "worked_today": ["Share rate >1.5% в 3 видео."],
            "best_cases": ["Кейс: score 8.7 и retention <60%."],
            "recommendations": ["Фикси группы с retention <58%.", "Тестируй 2 варианта."],
            "evidence_refs": ["hook_type.short"],
        }

        rendered = _render_summary_payload(payload)
        assert "&lt;58%" in rendered
        assert "&lt;strong&gt;alpha&lt;/strong&gt;" in rendered
        assert "<b>📊 Общая картина:</b>" in rendered


class TestGenerateDaySummary:
    def setup_method(self) -> None:
        _cache.clear()

    @pytest.mark.asyncio
    @patch("src.ai.day_summary.config")
    async def test_returns_none_when_disabled(self, mock_config, sample_videos: list[dict]) -> None:
        mock_config.ENABLE_DAY_SUMMARY = False
        result = await generate_day_summary(sample_videos)
        assert result is None

    @pytest.mark.asyncio
    @patch("src.ai.day_summary.config")
    async def test_returns_none_for_few_videos(self, mock_config, minimal_videos: list[dict]) -> None:
        mock_config.ENABLE_DAY_SUMMARY = True
        result = await generate_day_summary(minimal_videos, min_videos=3)
        assert result is None

    @pytest.mark.asyncio
    @patch("src.ai.day_summary._call_openrouter_for_summary")
    @patch("src.ai.day_summary.config")
    async def test_uses_fallback_on_llm_failure(self, mock_config, mock_call, sample_videos: list[dict]) -> None:
        mock_config.ENABLE_DAY_SUMMARY = True
        mock_config.DAY_SUMMARY_MAX_TOKENS = 1200
        mock_config.DAY_SUMMARY_TEMPERATURE = 0.4
        mock_config.DAY_SUMMARY_MODEL = "google/gemini-3-flash-preview"
        mock_call.return_value = None

        summary = await generate_day_summary(sample_videos)
        assert summary is not None
        assert "Общая картина" in summary
        assert "Что делать сегодня" in summary

    @pytest.mark.asyncio
    @patch("src.ai.day_summary._call_openrouter_for_summary")
    @patch("src.ai.day_summary.config")
    async def test_caching_works(self, mock_config, mock_call, sample_videos: list[dict]) -> None:
        mock_config.ENABLE_DAY_SUMMARY = True
        mock_config.DAY_SUMMARY_MAX_TOKENS = 1200
        mock_config.DAY_SUMMARY_TEMPERATURE = 0.4
        mock_config.DAY_SUMMARY_MODEL = "google/gemini-3-flash-preview"
        mock_call.return_value = json_response = (
            '{"overview":"За сутки 4 видео, avg score 6.7/10.",'
            '"best_hooks":["Short hooks retention 75% > 70%."],'
            '"worked_today":["Share rate 1.9% > 1.5%."],'
            '"best_cases":["Case A score 8.7 with 77% retention."],'
            '"recommendations":["Scale short hooks with share 1.9%.","Rework long hooks below 58%."],'
            '"evidence_refs":["hook_type.short","hook_type.long"]}'
        )

        first = await generate_day_summary(sample_videos, cache_ttl_sec=3600)
        second = await generate_day_summary(sample_videos, cache_ttl_sec=3600)
        assert first is not None
        assert second is not None
        assert first == second
        assert mock_call.call_count == 1
        assert json_response is not None
