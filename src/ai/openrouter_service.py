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
from typing import Any

import requests

from src import config
from src.ai.benchmarks import BENCHMARKS_CONTEXT

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"

# Превращаем dict с бенчмарками в JSON-строку для контекста модели
benchmarks_json = json.dumps(BENCHMARKS_CONTEXT, indent=2, ensure_ascii=False)

# JSON schema example (plain string, not f-string to avoid escaping issues)
JSON_SCHEMA_EXAMPLE = """{
  "video_title": "string|null",
  "posted_at": "string|null",
  "platform": "tiktok|reels|youtube_shorts|other",
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

# Build SYSTEM_PROMPT via string concatenation to avoid f-string escaping issues
SYSTEM_PROMPT = (
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
    + benchmarks_json +
    "\n</BENCHMARKS>\n"
    "\n"
    "## 3. ANALYSIS LOGIC\n"
    "1. **Content Type Detection:**\n"
    "   - **CAROUSEL:** If image contains header 'Post analysis', metric 'Photos viewed', or shows a horizontal row of multiple thumbnails.\n"
    "   - **VIDEO:** If image contains header 'Video analysis', metric 'Video views', or shows a single vertical thumbnail.\n"
    "2. **Date Extraction (MANDATORY):**\n"
    "   - Locate text starting with 'Posted on ...' (usually below the thumbnail).\n"
    "   - Extract the exact date/time string into `posted_at`. NEVER leave null.\n"
    "3. **Hook Analysis (CRITICAL):**\n"
    "   - **Source:** ALWAYS OCR the text overlay on the video thumbnail/cover. This is the `hook_text`.\n"
    "   - **Classification (Strict Word Count):**\n"
    "     - **SHORT (1-10 words):** 'The Punch'. Immediate shock, single concept. (e.g. 'Caught my wife cheating').\n"
    "     - **MEDIUM (11-30 words):** 'The Setup'. Establishes Context + Twist. (e.g. 'Surprising my wife with DNA results...').\n"
    "     - **LONG (30+ words):** 'The Story'. Full narrative arc (Who, What, Where, Why).\n"
    "5. **Data Extraction:** Extract all visible metrics (views, retention, etc.).\n"
    "6. **Scoring Algorithm (Deterministic):**\n"
    '   Calculate the final `score` by summing points strictly according to <BENCHMARKS> -> "scoring_model":\n'
    "   - **Hook Score (max 30):** Determine Hook rating -> Look up points in `scoring_model.tier_1_hook`.\n"
    "   - **Body Score (max 30):** Determine Completion/WatchTime rating -> Look up points in `scoring_model.tier_1_body`.\n"
    "   - **Viral Score (max 20):** Determine Share Rate rating -> Look up points in `scoring_model.tier_2_viral`.\n"
    "   - **Depth Score (max 20):** Determine Save+Comment rating -> Look up points in `scoring_model.tier_2_depth`.\n"
    '   - **Total:** Sum these 4 values. Apply penalties if "platinum_trap" or "marketing_hook" heuristics match.\n'
    '7. **Heuristics:** Check against "expert_heuristics_logic" in benchmarks.\n'
    '8. **Decision Tree:** Follow "automated_decision_tree" priority.\n'
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

USER_PROMPT = """Analyze the provided images of video metrics/analytics.

You will receive TWO images:
1. Overview Metrics: engagement numbers, views, posted date/time, etc.
2. Retention Graph: audience retention visualization

Extract ALL visible numbers, graphs, and data points from BOTH images. Apply the full Metrics Bible benchmarks.
Follow the Decision Tree to arrive at the final verdict.

Identify if this is a Video or Carousel based on the header ('Video analysis' vs 'Post analysis'). Extract 'Posted on' date strictly. OCR the text on the video thumbnail for `hook_text` and classify its type based on word count.

Rules:
- Identify the platform from the UI (TikTok / YouTube Shorts / Reels) - detect automatically from icons and colors.
- Extract the visible text/headline from the video screenshot as 'video_title'.
- Extract exact posted date/time, it is always located at the bottom of the small video thumbnail (e.g., 'Posted on Feb 6, 2026, 12:51 PM', 'February 6') into 'posted_at'.
- Read retention and engagement graphs if visible.
- Calculate engagement rates from raw numbers (share_rate = shares/views*100, etc.).
- Apply expert heuristics if conditions match.
- If the images do NOT show analytics (no views, no metrics, wrong content), still respond with valid JSON: set "platform" to "other", set "video_title" to null, use null for missing metrics, verdict "🟡 ITERATE", and in "analysis" briefly state what you see (e.g. "Screenshot does not show video analytics.").

Output: reply with ONLY the JSON object. No text before or after, no markdown code fences, no explanation—just the single JSON object starting with { and ending with }.
"""


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
    model = (config.OPENROUTER_MODEL or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    api_key = config.OPENROUTER_API_KEY

    # Build content array with text prompt and all images
    content: list[dict[str, Any]] = [{"type": "text", "text": USER_PROMPT}]

    for image_bytes in images_list:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"
        content.append({"type": "image_url", "image_url": {"url": data_uri}})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": content,
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 8192,
        "reasoning": {"effort": "medium"},
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-stats-bot",
    }

    try:
        resp = requests.post(
            OPENROUTER_URL,
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            logger.warning("OpenRouter returned no choices")
            return None, None

        msg = choices[0].get("message") or {}
        raw_text = (msg.get("content") or "").strip()
        if not raw_text:
            logger.warning("OpenRouter empty content in message")
            return None, None

        parsed = _parse_response(raw_text)
        return parsed, raw_text

    except requests.RequestException as e:
        logger.exception("OpenRouter request failed: %s", e)
        return None, None
    except (KeyError, TypeError, ValueError) as e:
        logger.exception("OpenRouter response parse failed: %s", e)
        return None, None

