"""
Анализ скриншотов с метриками видео через OpenRouter (Gemini 3 Flash, thinking medium).

Используется полный системный промпт с бенчмарками:
- Tier 1 (Gatekeeper): 3s retention, completion rate, avg watch time
- Tier 2 (Growth Engine): share rate, save rate, comment rate
- Expert Heuristics, Decision Tree

API: OpenRouter (OpenAI-compatible), модель google/gemini-3-flash-preview, reasoning.effort: medium.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

import requests

from src import config
from src.ai.benchmarks import BENCHMARKS_BY_CONTENT_TYPE, BENCHMARKS_CONTEXT

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"  # fallback if config not loaded

# Превращаем dict с бенчмарками в JSON-строку для контекста модели
legacy_benchmarks_json = json.dumps(BENCHMARKS_CONTEXT, indent=2, ensure_ascii=False)

# JSON schema example (plain string, not f-string to avoid escaping issues)
JSON_SCHEMA_EXAMPLE = """{
  "video_title": "string|null",
  "posted_at": "string|null",
  "platform": "tiktok|reels|other",
  "content_type": "video|carousel",
  "hook_text": "string|null",
  "hook_type": "short|medium|long",
  "video_duration_sec": number|null,
  "metrics": {
    "views": number, "likes": number, "comments": number, "shares": number, "saves": number,
    "retention_3s": number|null, "completion_rate": number|null, "avg_watch_time_pct": number|null,
    "viewed_pct": number|null, "tiktok_churn_point": "string|null"
  },
  "calculated_rates": { "share_rate": number, "save_rate": number, "aggregated_er": number },
  "tier_1_analysis": {
    "hook_3s": { "value": number, "rating": "FAIL|BORDERLINE|GOOD|SCALE", "note": "string" },
    "completion": { "value": number, "rating": "FAIL|OK|EXCELLENT", "duration_bracket": "string", "note": "string" },
    "avg_watch_time": { "value": number, "rating": "FAIL|OK|GREAT", "note": "string" }
  },
  "tier_2_analysis": {
    "volume_condition": "high_volume|low_volume",
    "share_rate": { "value": number, "rating": "string" },
    "save_rate": { "value": number, "rating": "string" },
    "comment_rate": { "value": number, "rating": "string" },
    "aggregated_er": { "value": number, "rating": "string" }
  },
  "expert_heuristics": ["List of triggered heuristic names"],
  "verdict": "🔴 KILL | ✂️ FIX | 🟡 ITERATE | 🚀 SCALE",
  "score": "0-10 float",
  "score_breakdown": {
    "hook_points": <number>,
    "body_points": <number>,
    "viral_points": <number>,
    "depth_points": <number>,
    "penalties": <number negative or 0>
  },
  "analysis": "Detailed analysis in user's language (3-5 sentences).",
  "recommendations": ["Actionable advice 1", "Actionable advice 2"]
}"""

# Build legacy SYSTEM_PROMPT via string concatenation to avoid f-string escaping issues
LEGACY_SYSTEM_PROMPT = (
    "You are **Creator Copilot**, a Senior Growth Analyst.\n"
    "Your goal: Analyze short-form video metrics (TikTok/Reels) using strict data benchmarks.\n"
    "\n"
    "## 1. CRITICAL: IMAGE CONSISTENCY PROTOCOL\n"
    "Before analysis, verify both images belong to the SAME video.\n"
    '- Check: Thumbnails, Titles, View Counts.\n'
    '- **IF MISMATCH:** STOP. Return JSON with `error: "content_mismatch"`.\n'
    "\n"
    '## 2. REFERENCE DATA (THE "BIBLE")\n'
    "Use the following JSON benchmarks for all scoring. Do NOT hallucinate thresholds.\n"
    "<BENCHMARKS>\n"
    + legacy_benchmarks_json +
    "\n</BENCHMARKS>\n"
    "\n"
    "## 3. ANALYSIS LOGIC\n"
    "Follow this EXACT order of operations for the analysis:\n"
    "\n"
    "1. **Platform Detection:**\n"
    "   - Identify the social network (TikTok, Instagram) based on UI elements, fonts, and icons.\n"
    "\n"
    "2. **Content Type Detection:**\n"
    "   - **VIDEO:** Look for 'Video analysis', 'Video views', vertical layout.\n"
    "   - **CAROUSEL:** Look for 'Post analysis', 'Photos viewed', horizontal thumbnails.\n"
    "\n"
    "3. **Date Extraction:**\n"
    "   - Locate 'Posted on ...' text (usually below the thumbnail).\n"
    "   - Extract the exact date/time string into `posted_at`. MANDATORY.\n"
    "\n"
    "4. **Core Metrics Extraction:**\n"
    "   - Extract: **Views, Likes, Comments, Shares, Saves** from the Overview image.\n"
    "   - Calculate Rates: Share Rate (Shares/Views), Save Rate (Saves/Views).\n"
    "\n"
    "5. **Hook Text Extraction (Primary):**\n"
    "   - Attempt to OCR text from the **small thumbnail** in the Overview/Metrics image.\n"
    "   - Assign to `hook_text`.\n"
    "   - Classify `hook_type` based on word count (Short 1-10, Medium 11-30, Long 30+).\n"
    "\n"
    "6. **Retention & Watch Time Analysis:**\n"
    "   - **Average Watch Time (Percentage):**\n"
    "     - **TikTok:** Look for 'On average viewers watched **X%**'. Use X directly as `avg_watch_time_pct`.\n"
    "     - **Instagram:** Look for 'Average watch time' (e.g. 3s, 1m). Calculate percentage: (Average Watch Time / Video Duration) * 100.\n"
    "     - **Carousel:** Look for 'Photos Viewed' (e.g. 2.1). Calculate: (Photos Viewed / Total Photos) * 100. If total unknown, leave null.\n"
    "   - **Retention at 3s (MANDATORY):**\n"
    "     - Locate the Retention Graph. Look for the curve value at the 3-second mark (X-axis).\n"
    "     - **FORCE ESTIMATION:** If the exact number is NOT written, you **MUST visually estimate** the percentage based on the curve's position relative to the Y-axis (0-100%).\n"
    "     - Example: If the curve drops slightly from the top, estimate ~80-95%. If it drops to half, estimate ~50%.\n"
    "     - **NEVER return null** for `retention_3s` if a retention graph is visible. Give your best visual estimate.\n"
    "   - **Hook Text (Secondary/Backup):**\n"
    "     - **IF** `hook_text` was NOT found in Step 5, try to OCR text from the thumbnail associated with the Retention Graph.\n"
    "\n"
    "7. **Scoring Algorithm (Deterministic):**\n"
    '   Calculate the final `score` by summing points strictly according to <BENCHMARKS> -> "scoring_model":\n'
    "   - **Hook Score (0..3):** Determine Hook rating -> Look up points in `scoring_model.tier_1_hook`.\n"
    "   - **Body Score (0..3):** Determine Completion/WatchTime rating -> Look up points in `scoring_model.tier_1_body`.\n"
    "   - **Viral Score (0..2):** Determine Share Rate rating -> Look up points in `scoring_model.tier_2_viral`.\n"
    "   - **Depth Score (0..2):** Determine Save+Comment rating -> Look up points in `scoring_model.tier_2_depth`.\n"
    '   - **Total score (0..10):** Sum these 4 values and apply penalties from `scoring_model.penalties`.\n'
    "\n"
    '8. **Heuristics & Decision Tree:**\n'
    '   - Check against "expert_heuristics_logic" in benchmarks.\n'
    '   - Follow "automated_decision_tree" priority for the final verdict.\n'
    "\n"
    "## 4. OUTPUT SCHEMA\n"
    "Response must be ONLY valid JSON.\n"
    "\n"
    "**Scenario A: Mismatch**\n"
    '{ "error": "content_mismatch", "reason": "Explanation..." }\n'
    "\n"
    "**Scenario B: Valid Analysis**\n"
    + JSON_SCHEMA_EXAMPLE
)

LEGACY_USER_PROMPT = """Analyze the provided images of video metrics/analytics.

You will receive TWO images:
1. Overview Metrics: engagement numbers, views, posted date/time, etc.
2. Retention Graph: audience retention visualization

Extract ALL visible numbers, graphs, and data points from BOTH images. Apply the full Metrics Bible benchmarks.
Follow the Decision Tree to arrive at the final verdict.

Identify if this is a Video or Carousel based on the header ('Video analysis' vs 'Post analysis'). Extract 'Posted on' date strictly. OCR the text on the video thumbnail for `hook_text` and classify its type based on word count.

Rules:
- Identify the platform from the UI (TikTok / Reels) - detect automatically from icons and colors.
- Extract the visible text/headline from the video screenshot as 'video_title'.
- Extract exact posted date/time, it is always located at the bottom of the small video thumbnail (e.g., 'Posted on Feb 6, 2026, 12:51 PM', 'February 6') into 'posted_at'.
- Read retention and engagement graphs if visible.
- Calculate engagement rates from raw numbers (share_rate = shares/views*100, etc.).
- Apply expert heuristics if conditions match.
- If the images do NOT show analytics (no views, no metrics, wrong content), still respond with valid JSON: set "platform" to "other", set "video_title" to null, use null for missing metrics, verdict "🟡 ITERATE", and in "analysis" briefly state what you see (e.g. "Screenshot does not show video analytics.").

Output: reply with ONLY the JSON object. No text before or after, no markdown code fences, no explanation—just the single JSON object starting with { and ending with }.
"""

REPAIR_USER_PROMPT = (
    "Your previous response was not valid for this API.\n"
    "Return ONLY one valid JSON object that matches the required schema and constraints.\n"
    "Do not include markdown, comments, prose, code fences, or extra keys."
)

# Stage 3: split responsibilities into two deterministic steps:
# 1) EXTRACT facts from screenshots
# 2) SCORE based only on extracted facts + benchmarks
EXTRACTION_SYSTEM_PROMPT = (
    "You are OCR+Data Extraction engine for short-video analytics screenshots.\n"
    "Task: extract only factual data visible on images. Do NOT score or recommend.\n"
    "If screenshots belong to different videos, return content_mismatch error.\n"
    "Follow the extraction order strictly to reduce metric confusion.\n"
)

EXTRACTION_USER_PROMPT = """Extract factual analytics fields from the provided screenshots.

Input contains:
1) Overview metrics screenshot
2) Retention screenshot
(Optional) 3) If you receive exactly 3 screenshots, the 3rd is the "retention after semantic ending" screen.

If you receive exactly 3 screenshots, you must extract the 'retention after semantic ending' metric from the 3rd screenshot. Here is exactly where to find it and how it looks: Look under the black timeline, in the 'Retention Rate' section at the bottom left. You are looking for a value in the format 0:0X (Y%). For example, if you see 0:06 (10%), it means at the 6th second, the retention is 10%. If you see 0:07 (9%), it means at the 7th second, the retention is 9%. You must extract BOTH the second (the X value) and the percentage (the Y value).

Rules:
- Return ONLY one JSON object.
- No markdown/code fences.
- Extract numbers as numbers whenever possible.
- If value is not visible, return null.
- Follow this order exactly:
  1) Consistency check (same video on both screenshots).
  2) Platform detection (TikTok vs Instagram/Reels UI).
  3) Content type detection (video/carousel).
  4) posted_at extraction (platform-specific rules below).
  5) Engagement counters extraction (views, likes, comments, shares, saves).
  6) Retention/watch metrics extraction.
  7) Hook text OCR + hook_type.
- posted_at extraction rules:
  - TikTok: use the full string under thumbnail starting with "Posted on ...", e.g. "Posted on Feb 6, 2026, 12:51 PM".
  - Instagram/Reels: use the plain date under thumbnail as shown, e.g. "February 6".
  - Do not convert or reformat dates; copy visible text exactly.
- Extract engagement counters from the same metrics block: likes, comments, shares, saves.
- Keep each metric in its own field; never merge shares/saves into one value.
- If one of engagement counters is not visible, set only that field to null (do not null all metrics).
- For `hook_type`, use only: short|medium|long (based on hook_text word count).
- For video retention graph, estimate `retention_3s` from the curve at ~3 seconds when exact value is not printed.
- If retention graph is visible, avoid leaving `retention_3s` null.
- For carousel:
  - `retention_3s` should store first-slide retention (percent who swiped from slide 1 to slide 2).
  - `viewed_pct` should store swipe-through rate (STR). If STR is not shown directly, compute from `photos_viewed / total_photos * 100`.
  - `completion_rate` should store platform completion metric if explicitly shown (especially TikTok carousel analytics).
  - `mixed_media`: set true if both photos and videos are visible in the carousel, false if clearly only one type, else null.
- For Reels/TikTok watch time:
  - if average watch time in seconds and video duration are visible, compute `avg_watch_time_pct = avg_watch_time_sec / duration_sec * 100`.
  - values above 100 are valid when users rewatch (looping). Do not cap to 100.
- `completion_rate`:
  - if explicitly shown in screenshot, extract it directly;
  - if not shown, keep null (do not invent from watch-time).
- If screenshots are from different videos, return:
  {"error":"content_mismatch","reason":"..."}
"""

SCORING_SYSTEM_PROMPT_BASE = (
    "You are a Senior Growth Analyst.\n"
    "You receive ONLY extracted facts JSON (already OCR'd). Do not invent missing data.\n"
    "Compute score deterministically from BENCHMARKS and provide verdict + recommendations.\n"
    "Use ONLY the benchmark profile selected for the detected content_type.\n"
    "For carousel, interpret retention_3s as first-slide retention (slide 1 -> 2).\n"
    "For carousel STR, prioritize viewed_pct; fallback to photos_viewed/total_photos.\n"
    "Apply penalties/bonuses and automated decision tree in BENCHMARKS exactly.\n"
    "Keep final score in range 0..10.\n"
)

SCORING_SYSTEM_PROMPT_SUFFIX = (
    "\n"
    "Few-shot style examples:\n"
    "Example A (strong): retention_3s=74, share_rate=1.8 -> verdict likely SCALE, high score.\n"
    "Example B (weak): retention_3s=44, low completion -> verdict likely KILL/FIX, low score.\n"
    "Example C (carousel): first-slide-retention=68, STR=62, save_rate=3.4 -> likely SCALE with bonuses.\n"
)

SCORING_USER_PROMPT_TEMPLATE = (
    "Use this extracted JSON as the single source of truth.\n"
    "Return ONLY valid JSON in final analysis schema.\n"
    "Extracted facts:\n"
    "{extracted_json}"
)

FUNNEL_SCREENSHOT_SYSTEM_PROMPT = (
    "You extract daily funnel metrics from 6 screenshots.\n"
    "Return ONLY valid JSON.\n"
    "The screenshots are provided in a fixed order:\n"
    "1) App Store Search Impressions\n"
    "2) App Store Product Page Views\n"
    "3) App Store Installs\n"
    "4) Google Play Product Page Views\n"
    "5) Google Play Installs\n"
    "6) Adapty Purchases (all stores)\n"
    "Read the day-specific value for the visible date row, not the total for the whole range.\n"
    "If dates across screenshots differ, set all_dates_match=false and explain mismatch_details.\n"
)

FUNNEL_SCREENSHOT_USER_PROMPT = (
    "Extract one daily batch from these funnel screenshots.\n"
    "Rules:\n"
    "- Read the per-day value for the specific visible day row or tooltip.\n"
    "- Ignore the grand total over the whole date range.\n"
    "- Return numeric values when visible.\n"
    "- If a metric is not visible, return null.\n"
    "- If Adapty purchases are shown for all stores only, put that number into all_store_purchases.\n"
    "- Normalize the shared day into YYYY-MM-DD when possible.\n"
)

FUNNEL_SCREENSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": ["string", "null"]},
        "all_dates_match": {"type": "boolean"},
        "mismatch_details": {"type": ["string", "null"]},
        "app_store_search_impressions": {"type": ["number", "null"]},
        "app_store_product_page_views": {"type": ["number", "null"]},
        "app_store_installs": {"type": ["number", "null"]},
        "google_play_product_page_views": {"type": ["number", "null"]},
        "google_play_installs": {"type": ["number", "null"]},
        "all_store_purchases": {"type": ["number", "null"]},
    },
    "required": [
        "date",
        "all_dates_match",
        "mismatch_details",
        "app_store_search_impressions",
        "app_store_product_page_views",
        "app_store_installs",
        "google_play_product_page_views",
        "google_play_installs",
        "all_store_purchases",
    ],
    "additionalProperties": False,
}


def _build_scoring_system_prompt(content_type: Any) -> str:
    normalized_content_type = _normalize_content_type(content_type)
    selected_benchmarks = BENCHMARKS_BY_CONTENT_TYPE.get(
        normalized_content_type,
        BENCHMARKS_CONTEXT,
    )
    selected_benchmarks_json = json.dumps(selected_benchmarks, indent=2, ensure_ascii=False)
    return (
        SCORING_SYSTEM_PROMPT_BASE
        + f"Selected content_type benchmark profile: {normalized_content_type}.\n"
        + "<BENCHMARKS>\n"
        + selected_benchmarks_json
        + "\n</BENCHMARKS>\n"
        + SCORING_SYSTEM_PROMPT_SUFFIX
    )


# Backward-compat aliases used in tests/imports
SYSTEM_PROMPT = LEGACY_SYSTEM_PROMPT
USER_PROMPT = EXTRACTION_USER_PROMPT

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "error": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
        "video_title": {"type": ["string", "null"]},
        "posted_at": {"type": ["string", "null"]},
        "platform": {"type": "string"},
        "content_type": {"type": "string"},
        "hook_text": {"type": ["string", "null"]},
        "hook_type": {"type": ["string", "null"]},
        "video_duration_sec": {"type": ["number", "null"]},
        "metrics": {
            "type": "object",
            "properties": {
                "views": {"type": ["number", "null"]},
                "likes": {"type": ["number", "null"]},
                "comments": {"type": ["number", "null"]},
                "shares": {"type": ["number", "null"]},
                "saves": {"type": ["number", "null"]},
                "retention_3s": {"type": ["number", "null"]},
                "completion_rate": {"type": ["number", "null"]},
                "avg_watch_time_pct": {"type": ["number", "null"]},
                "viewed_pct": {"type": ["number", "null"]},
                "photos_viewed": {"type": ["number", "null"]},
                "total_photos": {"type": ["number", "null"]},
                "tiktok_churn_point": {"type": ["string", "null"]},
                "mixed_media": {"type": ["boolean", "null"]},
                "account_avg_views": {"type": ["number", "null"]},
                "end_retention_second": {"type": ["integer", "null"]},
                "end_retention_pct": {"type": ["number", "null"]},
            },
            "required": [
                "views",
                "likes",
                "comments",
                "shares",
                "saves",
                "retention_3s",
                "completion_rate",
                "avg_watch_time_pct",
                "viewed_pct",
                "photos_viewed",
                "total_photos",
                "tiktok_churn_point",
            ],
            "additionalProperties": True,
        },
        "confidence": {
            "type": "object",
            "properties": {
                "platform": {"type": ["number", "null"]},
                "title": {"type": ["number", "null"]},
                "posted_at": {"type": ["number", "null"]},
                "metrics": {"type": ["number", "null"]},
                "retention_3s": {"type": ["number", "null"]},
                "overall": {"type": ["number", "null"]},
            },
            "required": ["platform", "title", "posted_at", "metrics", "retention_3s", "overall"],
            "additionalProperties": True,
        },
    },
    "required": [
        "platform",
        "content_type",
        "metrics",
        "video_title",
        "posted_at",
        "hook_text",
        "hook_type",
        "video_duration_sec",
    ],
    "additionalProperties": True,
}

FINAL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "video_title": {"type": ["string", "null"]},
        "posted_at": {"type": ["string", "null"]},
        "platform": {"type": "string"},
        "content_type": {"type": "string"},
        "hook_text": {"type": ["string", "null"]},
        "hook_type": {"type": ["string", "null"]},
        "video_duration_sec": {"type": ["number", "null"]},
        "metrics": {
            "type": "object",
            "properties": {
                "end_retention_second": {"type": ["integer", "null"]},
                "end_retention_pct": {"type": ["number", "null"]},
            },
            "additionalProperties": True,
        },
        "calculated_rates": {"type": "object"},
        "tier_1_analysis": {"type": "object"},
        "tier_2_analysis": {"type": "object"},
        "expert_heuristics": {"type": ["array", "null"]},
        "verdict": {"type": "string"},
        "score": {"type": ["number", "null"]},
        "score_breakdown": {"type": "object"},
        "analysis": {"type": "string"},
        "recommendations": {"type": "array"},
        "confidence": {"type": ["object", "null"]},
    },
    "required": [
        "platform",
        "content_type",
        "metrics",
        "score",
        "verdict",
        "analysis",
        "recommendations",
    ],
    "additionalProperties": True,
}


@dataclass
class AIQualityDashboard:
    """In-memory quality telemetry for AI pipeline."""

    total_calls: int = 0
    success_calls: int = 0
    mismatch_calls: int = 0
    extract_failures: int = 0
    score_failures: int = 0
    parse_failures: int = 0
    validation_failures: int = 0
    repair_attempts: int = 0
    repair_successes: int = 0
    schema_fallbacks: int = 0
    total_latency_sec: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, getattr(self, key) + value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_latency_ms = (
                round((self.total_latency_sec / self.total_calls) * 1000, 1)
                if self.total_calls
                else 0.0
            )
            success_rate = (
                round((self.success_calls / self.total_calls) * 100, 1)
                if self.total_calls
                else 0.0
            )
            return {
                "total_calls": self.total_calls,
                "success_rate_pct": success_rate,
                "mismatch_calls": self.mismatch_calls,
                "extract_failures": self.extract_failures,
                "score_failures": self.score_failures,
                "parse_failures": self.parse_failures,
                "validation_failures": self.validation_failures,
                "repair_attempts": self.repair_attempts,
                "repair_successes": self.repair_successes,
                "schema_fallbacks": self.schema_fallbacks,
                "avg_latency_ms": avg_latency_ms,
            }


QUALITY_DASHBOARD = AIQualityDashboard()


def get_ai_quality_snapshot() -> dict[str, Any]:
    """Public helper for future health checks and calibration reports."""
    return QUALITY_DASHBOARD.snapshot()


def _extract_json_object(text: str) -> str | None:
    """Находит первый полный JSON-объект { ... } в тексте (по скобкам)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    quote = None
    i = start
    while i < len(text):
        c = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\" and in_string:
            escape = True
            i += 1
            continue
        if not in_string:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
            elif c in ("'", '"'):
                in_string = True
                quote = c
        elif c == quote:
            in_string = False
        i += 1
    return None


def _parse_response(text: str) -> dict[str, Any] | None:
    """Извлекает JSON из ответа (убирает markdown-обёртку, ищет объект в тексте)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        lines = text.split("\n")
        out: list[str] = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                if in_block:
                    continue
                break
            if in_block:
                out.append(line)
        if out:
            try:
                return json.loads("\n".join(out))
            except json.JSONDecodeError:
                pass
    extracted = _extract_json_object(text)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as e:
            logger.warning("JSON from extracted block failed: %s", e)
    logger.warning("Failed to parse OpenRouter JSON. Raw (first 600 chars): %s", text[:600])
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace("%", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _normalize_posted_at(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Keep original date/time text, but remove common UI prefix.
    text = re.sub(r"^\s*posted\s+on\s+", "", text, flags=re.IGNORECASE)
    return text.strip() or None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _clamp(value: float | None, min_value: float, max_value: float) -> float | None:
    if value is None:
        return None
    return max(min_value, min(max_value, value))


def _normalize_platform(value: Any) -> str:
    text = (str(value) if value is not None else "other").strip().lower()
    if "tiktok" in text:
        return "tiktok"
    if "reel" in text or "instagram" in text:
        return "reels"
    if "youtube" in text:
        return "youtube"
    return "other"


def _normalize_content_type(value: Any) -> str:
    text = (str(value) if value is not None else "").strip().lower()
    if "carousel" in text or "post" in text:
        return "carousel"
    if "video" in text:
        return "video"
    return "video"


def _normalize_hook_type(value: Any, hook_text: Any) -> str | None:
    normalized = str(value).strip().lower() if value is not None else ""
    if normalized in {"short", "medium", "long"}:
        return normalized
    if not hook_text or not isinstance(hook_text, str):
        return None
    words = [w for w in hook_text.strip().split() if w]
    count = len(words)
    if count <= 10:
        return "short"
    if count <= 30:
        return "medium"
    return "long"


def _extract_message_content_text(result: dict[str, Any]) -> str:
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list) and all(not isinstance(part, dict) for part in content):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    out.append(str(text))
        joined = "\n".join(out).strip()
        if joined:
            return joined
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _build_payload(
    model: str,
    messages: list[dict[str, Any]],
    schema_name: str | None = None,
    schema: dict[str, Any] | None = None,
    structured_output: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": messages}
    payload["reasoning"] = {"effort": "medium"}
    if structured_output and schema_name and schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
    return payload


def _request_openrouter(
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_sec: float,
    max_retries: int,
) -> dict[str, Any] | None:
    """HTTP call with retries and fallback when json_schema is unsupported by provider."""
    current_payload = dict(payload)

    for attempt in range(max_retries):
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=current_payload,
                timeout=timeout_sec,
            )

            if response.status_code >= 400:
                response_text = getattr(response, "text", "")
                has_schema = "response_format" in current_payload
                schema_error = (
                    "response_format" in response_text.lower()
                    or "json_schema" in response_text.lower()
                    or "unsupported" in response_text.lower()
                )
                if has_schema and schema_error:
                    logger.warning("Provider rejected response_format, fallback to plain JSON mode.")
                    QUALITY_DASHBOARD.record(schema_fallbacks=1)
                    current_payload = dict(current_payload)
                    current_payload.pop("response_format", None)
                    continue

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", "unknown")
            text = getattr(exc.response, "text", "unknown")
            logger.error(
                "OpenRouter HTTP Error (attempt %s/%s): %s - %s",
                attempt + 1,
                max_retries,
                status_code,
                text,
            )
            if attempt == max_retries - 1:
                # 401 = неверный/просроченный API ключ — возвращаем спец-ответ для понятного сообщения пользователю
                if status_code == 401:
                    return {"error": "api_auth_failed", "reason": str(text)[:200]}
                return None
            time.sleep(2 ** attempt)
        except Exception as exc:
            logger.warning(
                "OpenRouter request failed (attempt %s/%s): %s",
                attempt + 1,
                max_retries,
                exc,
            )
            if attempt == max_retries - 1:
                return None
            time.sleep(2 ** attempt)

    return None


def _validate_extraction_result(result: dict[str, Any] | None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return False, ["Result is not a JSON object"]

    if result.get("error") == "content_mismatch":
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("content_mismatch requires non-empty 'reason'")
        return len(errors) == 0, errors

    required_fields = ["platform", "content_type", "metrics", "video_title", "posted_at"]
    for key in required_fields:
        if key not in result:
            errors.append(f"Missing required field: {key}")

    if not isinstance(result.get("metrics"), dict):
        errors.append("metrics must be object")
    else:
        metrics = result.get("metrics") or {}
        for key in ("views", "likes", "comments", "shares", "saves"):
            value = metrics.get(key)
            if value is not None and not _is_number(value):
                errors.append(f"metrics.{key} must be numeric or null")

    return len(errors) == 0, errors


def _validate_analysis_result(result: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Серверная валидация финального ответа AI."""
    errors: list[str] = []
    if not isinstance(result, dict):
        return False, ["Result is not a JSON object"]

    if result.get("error") == "content_mismatch":
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("content_mismatch requires non-empty 'reason'")
        return len(errors) == 0, errors

    required_top_level = [
        "platform",
        "content_type",
        "metrics",
        "score",
        "verdict",
        "analysis",
        "recommendations",
    ]
    for key in required_top_level:
        if key not in result:
            errors.append(f"Missing required field: {key}")

    content_type = result.get("content_type")
    if content_type not in {"video", "carousel", "other"}:
        errors.append("content_type must be 'video', 'carousel' or 'other'")

    platform = result.get("platform")
    if not isinstance(platform, str):
        errors.append("platform must be string")

    score = result.get("score")
    if not _is_number(score):
        errors.append("score must be numeric")
    elif not (0 <= float(score) <= 10):
        errors.append("score must be in range 0..10")

    analysis = result.get("analysis")
    if not isinstance(analysis, str) or not analysis.strip():
        errors.append("analysis must be non-empty string")

    recommendations = result.get("recommendations")
    if not isinstance(recommendations, list):
        errors.append("recommendations must be a list")

    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be object")
    else:
        for key in ("views", "likes", "comments", "shares", "saves"):
            value = metrics.get(key)
            if value is not None and not _is_number(value):
                errors.append(f"metrics.{key} must be numeric or null")

        for key in ("retention_3s", "completion_rate", "avg_watch_time_pct", "viewed_pct", "end_retention_pct"):
            value = metrics.get(key)
            if value is None:
                continue
            if not _is_number(value):
                errors.append(f"metrics.{key} must be numeric or null")
            elif key == "avg_watch_time_pct" and float(value) < 0:
                errors.append("metrics.avg_watch_time_pct must be >= 0")
            elif key not in ("avg_watch_time_pct",) and not (0 <= float(value) <= 100):
                errors.append(f"metrics.{key} must be in range 0..100")

    return len(errors) == 0, errors


def _validate_funnel_screenshot_result(result: dict[str, Any] | None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return False, ["Result is not a JSON object"]

    required_fields = [
        "date",
        "all_dates_match",
        "mismatch_details",
        "app_store_search_impressions",
        "app_store_product_page_views",
        "app_store_installs",
        "google_play_product_page_views",
        "google_play_installs",
        "all_store_purchases",
    ]
    for key in required_fields:
        if key not in result:
            errors.append(f"Missing required field: {key}")

    if not isinstance(result.get("all_dates_match"), bool):
        errors.append("all_dates_match must be boolean")

    for key in (
        "app_store_search_impressions",
        "app_store_product_page_views",
        "app_store_installs",
        "google_play_product_page_views",
        "google_play_installs",
        "all_store_purchases",
    ):
        value = result.get(key)
        if value is not None and not _is_number(value):
            errors.append(f"{key} must be numeric or null")

    return len(errors) == 0, errors


def _normalize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    source = metrics or {}
    normalized: dict[str, Any] = {}

    integer_metrics = ("views", "likes", "comments", "shares", "saves")
    for key in integer_metrics:
        value = _to_float(source.get(key))
        normalized[key] = int(round(value)) if value is not None and value >= 0 else 0

    bounded_percent_metrics = ("retention_3s", "completion_rate", "viewed_pct", "end_retention_pct")
    for key in bounded_percent_metrics:
        value = _to_float(source.get(key))
        normalized[key] = _clamp(value, 0.0, 100.0)
    # avg_watch_time_pct может быть > 100 из-за повторных просмотров (looping)
    avg_watch_pct = _to_float(source.get("avg_watch_time_pct"))
    normalized["avg_watch_time_pct"] = max(0.0, avg_watch_pct) if avg_watch_pct is not None else None

    normalized["photos_viewed"] = _to_float(source.get("photos_viewed"))
    normalized["total_photos"] = _to_float(source.get("total_photos"))
    normalized["mixed_media"] = _to_bool(source.get("mixed_media"))
    normalized["account_avg_views"] = _to_float(source.get("account_avg_views"))
    end_sec = _to_float(source.get("end_retention_second"))
    normalized["end_retention_second"] = int(round(end_sec)) if end_sec is not None and end_sec >= 0 else None
    churn_point = source.get("tiktok_churn_point")
    normalized["tiktok_churn_point"] = str(churn_point) if churn_point is not None else None

    # Guardrail: positive retention and zero average watch time contradict each other.
    # In such cases OCR/model usually failed to read watch time, so keep it missing
    # instead of writing a misleading 0%.
    retention_value = normalized.get("retention_3s")
    if (
        normalized.get("avg_watch_time_pct") is not None
        and float(normalized["avg_watch_time_pct"]) <= 0
        and retention_value is not None
        and float(retention_value) > 0
    ):
        normalized["avg_watch_time_pct"] = None

    return normalized


def _calculate_rates(metrics: dict[str, Any]) -> dict[str, float]:
    views = float(metrics.get("views") or 0)
    likes = float(metrics.get("likes") or 0)
    comments = float(metrics.get("comments") or 0)
    shares = float(metrics.get("shares") or 0)
    saves = float(metrics.get("saves") or 0)

    if views <= 0:
        return {
            "like_rate": 0.0,
            "share_rate": 0.0,
            "save_rate": 0.0,
            "comment_rate": 0.0,
            "aggregated_er": 0.0,
        }

    return {
        "like_rate": round((likes / views) * 100, 3),
        "share_rate": round((shares / views) * 100, 3),
        "save_rate": round((saves / views) * 100, 3),
        "comment_rate": round((comments / views) * 100, 3),
        "aggregated_er": round(((likes + comments + shares + saves) / views) * 100, 3),
    }


def _normalize_calculated_rates(rates: dict[str, Any] | None, metrics: dict[str, Any]) -> dict[str, float]:
    base = _calculate_rates(metrics)
    source = rates or {}
    normalized = {
        "like_rate": _to_float(source.get("like_rate")),
        "share_rate": _to_float(source.get("share_rate")),
        "save_rate": _to_float(source.get("save_rate")),
        "comment_rate": _to_float(source.get("comment_rate")),
        "aggregated_er": _to_float(source.get("aggregated_er")),
    }

    # Backward compatibility for providers that return engagement_rate instead of aggregated_er
    if normalized["aggregated_er"] is None:
        normalized["aggregated_er"] = _to_float(source.get("engagement_rate"))

    for key, value in normalized.items():
        if value is None:
            normalized[key] = base[key]
        else:
            normalized[key] = max(0.0, float(value))
    return normalized


def _normalize_extraction_result(extracted: dict[str, Any]) -> dict[str, Any]:
    if extracted.get("error") == "content_mismatch":
        return {
            "error": "content_mismatch",
            "reason": extracted.get("reason") or "Images appear to be from different videos.",
        }

    metrics = _normalize_metrics(extracted.get("metrics"))
    normalized_content_type = _normalize_content_type(extracted.get("content_type"))
    if normalized_content_type == "carousel":
        photos_viewed = _to_float(metrics.get("photos_viewed"))
        total_photos = _to_float(metrics.get("total_photos"))
        if photos_viewed is not None and total_photos and total_photos > 0:
            # For carousel, derive STR from photos viewed when needed.
            derived_pct = _clamp((photos_viewed / total_photos) * 100.0, 0.0, 100.0)
            if metrics.get("viewed_pct") is None:
                metrics["viewed_pct"] = derived_pct
            if metrics.get("avg_watch_time_pct") is None:
                metrics["avg_watch_time_pct"] = derived_pct

    confidence = extracted.get("confidence")
    if not isinstance(confidence, dict):
        confidence = {}

    return {
        "video_title": extracted.get("video_title"),
        "posted_at": _normalize_posted_at(extracted.get("posted_at")),
        "platform": _normalize_platform(extracted.get("platform")),
        "content_type": normalized_content_type,
        "hook_text": extracted.get("hook_text"),
        "hook_type": _normalize_hook_type(extracted.get("hook_type"), extracted.get("hook_text")),
        "video_duration_sec": _to_float(extracted.get("video_duration_sec")),
        "metrics": metrics,
        "confidence": {
            "platform": _clamp(_to_float(confidence.get("platform")), 0.0, 1.0),
            "title": _clamp(_to_float(confidence.get("title")), 0.0, 1.0),
            "posted_at": _clamp(_to_float(confidence.get("posted_at")), 0.0, 1.0),
            "metrics": _clamp(_to_float(confidence.get("metrics")), 0.0, 1.0),
            "retention_3s": _clamp(_to_float(confidence.get("retention_3s")), 0.0, 1.0),
            "overall": _clamp(_to_float(confidence.get("overall")), 0.0, 1.0),
        },
    }


def _normalize_final_result(final_result: dict[str, Any], extracted: dict[str, Any], model: str) -> dict[str, Any]:
    output = dict(final_result)

    if output.get("error") == "content_mismatch":
        return output

    extracted_metrics = extracted.get("metrics") if isinstance(extracted.get("metrics"), dict) else {}
    output_metrics = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
    merged_metrics = dict(extracted_metrics)
    merged_metrics.update(output_metrics)
    merged_metrics = _normalize_metrics(merged_metrics)

    output["metrics"] = merged_metrics
    output["platform"] = _normalize_platform(output.get("platform") or extracted.get("platform"))
    output["content_type"] = _normalize_content_type(
        output.get("content_type") or extracted.get("content_type")
    )
    output["video_title"] = output.get("video_title") or extracted.get("video_title")
    output["hook_text"] = output.get("hook_text") or extracted.get("hook_text")
    output["hook_type"] = _normalize_hook_type(
        output.get("hook_type") or extracted.get("hook_type"),
        output.get("hook_text") or extracted.get("hook_text"),
    )
    output["posted_at"] = _normalize_posted_at(output.get("posted_at") or extracted.get("posted_at"))
    output["video_duration_sec"] = _to_float(
        output.get("video_duration_sec") or extracted.get("video_duration_sec")
    )
    output["score"] = _clamp(_to_float(output.get("score")), 0.0, 10.0) or 0.0
    output["calculated_rates"] = _normalize_calculated_rates(output.get("calculated_rates"), merged_metrics)
    output["aggregated_er"] = output["calculated_rates"].get("aggregated_er")
    if not isinstance(output.get("recommendations"), list):
        output["recommendations"] = []
    if not output["recommendations"]:
        output["recommendations"] = ["Перепроверь hook и первые 3 секунды, затем протестируй вариацию."]

    output["title"] = output.get("title") or output.get("video_title") or output.get("hook_text")
    output["confidence"] = output.get("confidence") or extracted.get("confidence")
    output.setdefault("tier_1_analysis", {})
    output.setdefault("tier_2_analysis", {})
    output.setdefault("score_breakdown", {})
    output.setdefault("expert_heuristics", [])
    output.setdefault("analysis", "Automated analysis completed.")
    output.setdefault("verdict", "🟡 ITERATE")

    if output.get("content_type") == "carousel":
        photos_viewed = _to_float(merged_metrics.get("photos_viewed"))
        total_photos = _to_float(merged_metrics.get("total_photos"))
        if photos_viewed is not None and total_photos and total_photos > 0:
            derived_pct = _clamp((photos_viewed / total_photos) * 100.0, 0.0, 100.0)
            if merged_metrics.get("viewed_pct") is None:
                merged_metrics["viewed_pct"] = derived_pct
            if merged_metrics.get("avg_watch_time_pct") is None:
                merged_metrics["avg_watch_time_pct"] = derived_pct

    # Guardrail: не даём завышать score при полном отсутствии социального отклика.
    views = float(merged_metrics.get("views") or 0)
    likes = float(merged_metrics.get("likes") or 0)
    comments = float(merged_metrics.get("comments") or 0)
    shares = float(merged_metrics.get("shares") or 0)
    saves = float(merged_metrics.get("saves") or 0)
    social_actions = likes + comments + shares + saves
    aggregated_er = float(output["calculated_rates"].get("aggregated_er") or 0.0)

    guardrails_applied: list[str] = []
    if views >= 200 and social_actions <= 2:
        capped = min(float(output["score"]), 4.8)
        if capped < float(output["score"]):
            output["score"] = round(capped, 2)
            guardrails_applied.append("very_low_social_actions_cap")
    elif views >= 200 and aggregated_er < 1.0 and float(output["calculated_rates"].get("share_rate") or 0) == 0:
        capped = min(float(output["score"]), 5.5)
        if capped < float(output["score"]):
            output["score"] = round(capped, 2)
            guardrails_applied.append("low_er_no_shares_cap")

    if guardrails_applied and "SCALE" in str(output.get("verdict", "")).upper():
        output["verdict"] = "🟡 ITERATE (FIX ENGAGEMENT)"

    output["quality_meta"] = {
        "pipeline": "extract_then_score_v1",
        "model": model,
        "calibration_ready": True,
        "guardrails_applied": guardrails_applied,
    }
    return output


def _run_ai_step(
    *,
    step_name: str,
    model: str,
    headers: dict[str, str],
    messages: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    validator: Callable[[dict[str, Any] | None], tuple[bool, list[str]]],
    timeout_sec: float,
    max_retries: int,
    structured_output: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    payload = _build_payload(
        model=model,
        messages=messages,
        schema_name=schema_name,
        schema=schema,
        structured_output=structured_output,
    )
    result = _request_openrouter(
        payload=payload,
        headers=headers,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
    )
    if not result:
        return None, None
    if isinstance(result, dict) and result.get("error") == "api_auth_failed":
        return result, None

    raw_text = _extract_message_content_text(result)
    parsed = _parse_response(raw_text)
    if parsed is None:
        QUALITY_DASHBOARD.record(parse_failures=1)

    is_valid, errors = validator(parsed)
    if is_valid:
        return parsed, raw_text

    QUALITY_DASHBOARD.record(validation_failures=1, repair_attempts=1)
    logger.warning(
        "Invalid %s payload from AI. Trying one repair pass. Errors: %s",
        step_name,
        "; ".join(errors[:5]),
    )
    repair_prompt = (
        f"{REPAIR_USER_PROMPT}\n"
        f"Step: {step_name}\n"
        f"Validation errors: {'; '.join(errors[:10])}"
    )
    repair_messages = messages + [
        {"role": "assistant", "content": raw_text},
        {"role": "user", "content": repair_prompt},
    ]
    repair_payload = _build_payload(
        model=model,
        messages=repair_messages,
        schema_name=schema_name,
        schema=schema,
        structured_output=structured_output,
    )
    repair_result = _request_openrouter(
        payload=repair_payload,
        headers=headers,
        timeout_sec=timeout_sec,
        max_retries=1,
    )
    if not repair_result:
        return None, raw_text

    repair_raw_text = _extract_message_content_text(repair_result)
    repair_parsed = _parse_response(repair_raw_text)
    if repair_parsed is None:
        QUALITY_DASHBOARD.record(parse_failures=1)
    repair_valid, repair_errors = validator(repair_parsed)
    if repair_valid:
        QUALITY_DASHBOARD.record(repair_successes=1)
        return repair_parsed, repair_raw_text

    logger.warning(
        "Repair for %s still invalid. Errors: %s",
        step_name,
        "; ".join(repair_errors[:5]),
    )
    return None, repair_raw_text or raw_text


def analyze_funnel_screenshots(
    images_list: list[bytes],
    mime_type: str = "image/jpeg",
) -> tuple[dict[str, Any] | None, str | None]:
    model = getattr(config, "OPENROUTER_MODEL", None) or DEFAULT_MODEL
    logger.info("OpenRouter funnel AI call: model=%s reasoning_effort=medium", model)
    api_key = getattr(config, "OPENROUTER_API_KEY", "")
    timeout_sec = float(getattr(config, "OPENROUTER_TIMEOUT_SEC", 120.0))
    max_retries = int(getattr(config, "OPENROUTER_MAX_RETRIES", 3))
    structured_output = bool(getattr(config, "OPENROUTER_USE_STRUCTURED_OUTPUT", True))

    if not api_key:
        logger.error("OPENROUTER_API_KEY is empty")
        return None, None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-stats-bot",
        "X-Title": "Video Stats Bot",
    }

    content: list[dict[str, Any]] = [{"type": "text", "text": FUNNEL_SCREENSHOT_USER_PROMPT}]
    for img_bytes in images_list:
        b64_str = base64.b64encode(img_bytes).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64_str}"},
            }
        )

    messages = [
        {"role": "system", "content": FUNNEL_SCREENSHOT_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    result, raw_text = _run_ai_step(
        step_name="funnel_screenshots",
        model=model,
        headers=headers,
        messages=messages,
        schema_name="funnel_screenshot_batch",
        schema=FUNNEL_SCREENSHOT_SCHEMA,
        validator=_validate_funnel_screenshot_result,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        structured_output=structured_output,
    )
    if isinstance(result, dict) and result.get("error") == "api_auth_failed":
        return result, raw_text
    if not result:
        return None, raw_text
    return result, raw_text


def analyze_screenshot(
    images_list: list[bytes],
    mime_type: str = "image/jpeg",
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Отправляет скриншоты в OpenRouter (Gemini 3 Flash, thinking medium).

    Args:
        images_list: Список байтов изображений (2 изображения: [Overview Metrics, Retention Graph]).
        mime_type: MIME-тип изображений (по умолчанию image/jpeg).

    Returns:
        Tuple (parsed_result, raw_response_text).
        parsed_result: распарсенный JSON или None.
        raw_response_text: сырой текст ответа для дебага.
    """
    # Model from config (OPENROUTER_MODEL) or fallback to DEFAULT_MODEL
    model = getattr(config, "OPENROUTER_MODEL", None) or DEFAULT_MODEL
    logger.info("OpenRouter AI call: model=%s reasoning_effort=medium", model)
    api_key = getattr(config, "OPENROUTER_API_KEY", "")
    timeout_sec = float(getattr(config, "OPENROUTER_TIMEOUT_SEC", 120.0))
    max_retries = int(getattr(config, "OPENROUTER_MAX_RETRIES", 3))
    structured_output = bool(getattr(config, "OPENROUTER_USE_STRUCTURED_OUTPUT", True))
    quality_log_every = max(1, int(getattr(config, "AI_QUALITY_LOG_EVERY_N", 25)))

    start_ts = time.monotonic()
    QUALITY_DASHBOARD.record(total_calls=1)

    def _maybe_log_quality_snapshot() -> None:
        if QUALITY_DASHBOARD.total_calls % quality_log_every == 0:
            logger.info("AI quality snapshot: %s", QUALITY_DASHBOARD.snapshot())

    if not api_key:
        logger.error("OPENROUTER_API_KEY is empty")
        QUALITY_DASHBOARD.record(extract_failures=1)
        _maybe_log_quality_snapshot()
        return None, None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-stats-bot",
        "X-Title": "Video Stats Bot",
    }

    extraction_content: list[dict[str, Any]] = [{"type": "text", "text": EXTRACTION_USER_PROMPT}]
    for img_bytes in images_list:
        b64_str = base64.b64encode(img_bytes).decode("utf-8")
        extraction_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64_str}"},
            }
        )

    extraction_messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": extraction_content},
    ]

    extracted_raw_result, extraction_raw_text = _run_ai_step(
        step_name="extraction",
        model=model,
        headers=headers,
        messages=extraction_messages,
        schema_name="video_extraction",
        schema=EXTRACTION_SCHEMA,
        validator=_validate_extraction_result,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        structured_output=structured_output,
    )
    if isinstance(extracted_raw_result, dict) and extracted_raw_result.get("error") == "api_auth_failed":
        return extracted_raw_result, extraction_raw_text or ""
    if not extracted_raw_result:
        QUALITY_DASHBOARD.record(extract_failures=1)
        elapsed = time.monotonic() - start_ts
        QUALITY_DASHBOARD.record(total_latency_sec=elapsed)
        _maybe_log_quality_snapshot()
        return None, extraction_raw_text

    extracted = _normalize_extraction_result(extracted_raw_result)
    if extracted.get("error") == "content_mismatch":
        QUALITY_DASHBOARD.record(mismatch_calls=1, success_calls=1)
        elapsed = time.monotonic() - start_ts
        QUALITY_DASHBOARD.record(total_latency_sec=elapsed)
        _maybe_log_quality_snapshot()
        return extracted, extraction_raw_text

    scoring_input = json.dumps(extracted, ensure_ascii=False)
    scoring_system_prompt = _build_scoring_system_prompt(extracted.get("content_type"))
    scoring_messages = [
        {"role": "system", "content": scoring_system_prompt},
        {
            "role": "user",
            "content": SCORING_USER_PROMPT_TEMPLATE.format(extracted_json=scoring_input),
        },
    ]

    scored_raw_result, scoring_raw_text = _run_ai_step(
        step_name="scoring",
        model=model,
        headers=headers,
        messages=scoring_messages,
        schema_name="video_scoring",
        schema=FINAL_ANALYSIS_SCHEMA,
        validator=_validate_analysis_result,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        structured_output=structured_output,
    )
    if not scored_raw_result:
        QUALITY_DASHBOARD.record(score_failures=1)
        elapsed = time.monotonic() - start_ts
        QUALITY_DASHBOARD.record(total_latency_sec=elapsed)
        _maybe_log_quality_snapshot()
        return None, scoring_raw_text or extraction_raw_text

    final_result = _normalize_final_result(scored_raw_result, extracted, model)
    is_valid, final_errors = _validate_analysis_result(final_result)
    if not is_valid:
        logger.warning("Final normalized result is invalid: %s", "; ".join(final_errors[:5]))
        QUALITY_DASHBOARD.record(score_failures=1, validation_failures=1)
        elapsed = time.monotonic() - start_ts
        QUALITY_DASHBOARD.record(total_latency_sec=elapsed)
        _maybe_log_quality_snapshot()
        return None, scoring_raw_text or extraction_raw_text

    QUALITY_DASHBOARD.record(success_calls=1)
    elapsed = time.monotonic() - start_ts
    QUALITY_DASHBOARD.record(total_latency_sec=elapsed)
    _maybe_log_quality_snapshot()

    raw_bundle = json.dumps(
        {"extraction_raw": extraction_raw_text, "scoring_raw": scoring_raw_text},
        ensure_ascii=False,
    )
    return final_result, raw_bundle
