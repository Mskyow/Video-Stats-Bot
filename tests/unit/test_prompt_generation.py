"""
Unit tests for system prompt generation (OpenRouter).

Tests cover:
- Successful import of SYSTEM_PROMPT
- Presence of benchmark JSON (tier_1_gatekeeper_retention) in the prompt
- Presence of IMAGE CONSISTENCY PROTOCOL section
"""
from __future__ import annotations

import pytest


class TestSystemPromptImport:
    """Import of SYSTEM_PROMPT must succeed without errors."""

    def test_system_prompt_imports_successfully(self):
        """Importing SYSTEM_PROMPT from src.ai.openrouter_service must not raise."""
        from src.ai.openrouter_service import SYSTEM_PROMPT  # noqa: F401

        assert SYSTEM_PROMPT is not None
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 0


class TestSystemPromptBenchmarks:
    """SYSTEM_PROMPT must contain the benchmark JSON."""

    def test_system_prompt_contains_benchmark_json(self):
        """SYSTEM_PROMPT must contain JSON with tier_1_gatekeeper_retention."""
        from src.ai.openrouter_service import SYSTEM_PROMPT

        assert "tier_1_gatekeeper_retention" in SYSTEM_PROMPT


class TestSystemPromptImageConsistency:
    """SYSTEM_PROMPT must include the IMAGE CONSISTENCY PROTOCOL section."""

    def test_system_prompt_contains_image_consistency_protocol(self):
        """SYSTEM_PROMPT must contain the IMAGE CONSISTENCY PROTOCOL section."""
        from src.ai.openrouter_service import SYSTEM_PROMPT

        assert "IMAGE CONSISTENCY PROTOCOL" in SYSTEM_PROMPT
