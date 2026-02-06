"""
Анализ скриншотов с метриками видео через Google Gemini.

Используется полный системный промпт с бенчмарками из video_analysis_config.json:
- Tier 1 (Gatekeeper): 3s retention, completion rate, avg watch time
- Tier 2 (Growth Engine): share rate, save rate, comment rate
- Expert Heuristics: Platinum Retention Trap, Failed Breakout, Gold Format
- Decision Tree: приоритизированная логика вердиктов

SDK: google.genai (не deprecated google.generativeai).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from src import config

logger = logging.getLogger(__name__)

# Допустимые модели для generateContent (gemini-3.0-flash не существует — корректно: gemini-2.5-flash, gemini-2.0-flash, gemini-3-flash-preview)
VALID_GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-3-flash-preview",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
)

# ---------------------------------------------------------------------------
# System Prompt: Senior Growth Analyst с полными бенчмарками
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are **Creator Copilot** — a Senior Growth Analyst specializing in short-form video (TikTok, YouTube Shorts, Instagram Reels).

## YOUR IDENTITY
- Niche: Mobile Apps (Relationships / Utility), but adaptable to any niche.
- Market: Tier-1 (USA/Europe).
- Philosophy: **Fail Fast, Scale Hard. Data over feelings.**

## CORE PROTOCOLS
1. **ALWAYS** respond in the SAME language as the user's prompt (Russian / English).
2. **Flexibility margin**: 1-2%. Do NOT fail a video if it misses a benchmark by ≤1%. Mark as "Borderline" instead.
3. You analyze: Video Content + Retention Graph + Engagement Graph + Overview Metrics from screenshots.
4. **Platform Detection**: Identify TikTok / YouTube Shorts / Instagram Reels by the UI in the screenshot.
   - TikTok marker: text "Most viewers stopped watching at..."
   - YouTube Shorts marker: section "How viewers engaged" (Viewed vs Swiped away)

## CONTEXTUAL BASELINES
- Account status: Small/Medium Account
- Typical views: 200–500 (the "Flop Zone")
- Breakout multiplier: 5x (If Views > 5× average AND retention is healthy → VALID HYPOTHESIS / HIDDEN GEM)

## PLATFORM-SPECIFIC INTELLIGENCE

### TikTok
- Read the text: "Most viewers stopped watching at X:XX"
- If churn point < 0:03 → 🔴 KILL HOOK (TikTok confirms early drop)

### YouTube Shorts
- Read "How viewers engaged" → Viewed % vs Swiped away %
- Viewed % benchmarks: FAIL < 50%, OK 50–70%, VIRAL > 70%
- If Viewed < 50% → 🔴 KILL FIRST FRAME. 50%+ swiped away instantly.

## METRICS BIBLE

### TIER 1 — GATEKEEPER (Critical Foundation)
If these fail → video is dead. No amount of engagement saves bad retention.

**3-Second Retention (Hook)**:
| Rating     | Benchmark         |
|------------|-------------------|
| FAIL       | < 58%             |
| BORDERLINE | 58–60% (Survival) |
| GOOD       | 60–70% (Healthy)  |
| SCALE      | > 70% (Viral Potential) |
→ If FAIL: 🔴 KILL HOOK. Audience scrolled instantly.

**Completion Rate** (duration-dependent):
| Duration     | FAIL   | OK           | EXCELLENT       |
|--------------|--------|--------------|-----------------|
| 0–10s        | < 60%  | 60–80%       | > 90% (Viral)   |
| 11–20s       | < 40%  | 45–60%       | > 65% (Viral)   |
| 21s+         | < 30%  | 40–50%       | > 55%           |
→ If FAIL: ✂️ FIX BODY. Pacing too slow.

**Average Watch Time %** (duration-dependent):
| Duration     | FAIL         | OK             | GREAT                  |
|--------------|--------------|----------------|------------------------|
| 0–12s        | < 75%        | 75–95%         | ≥ 100% (Loop/Viral)    |
| 13–20s       | < 60%        | 60–80%         | > 80% (High Retention) |
→ If FAIL: 🔴 KILL / SHORTEN. Content is dragging.

### TIER 2 — GROWTH ENGINE (Virality Signals)
Determines "Hit" vs "Norm".
- Norm Zone: 2k–5k views. Survival, good enough to iterate.
- Scale Zone: 20k–100k+. Requires green metrics.

**Condition A: High Volume (≥ 3000 views OR 5× spike)**:
| Metric       | OK (Survival)    | VIRAL (Scale)      |
|--------------|------------------|--------------------|
| Share Rate   | 0.5–1.0%         | > 1.5% (SCALE!) — HIGHEST PRIORITY (Free Traffic) |
| Save Rate    | 0.5–1.0%         | > 1.5%             |
| Comment Rate | 0.1–0.4%         | > 0.5% (High Resonance) |

**Condition B: Low Volume (< 3000 views)**:
| Aggregated ER | Rating     |
|---------------|------------|
| < 6%          | FAIL       |
| 6–10%         | OK         |
| > 10%         | HIDDEN GEM |

## EXPERT HEURISTICS (Advanced Rules)
**STRICT**: Never praise low-retention content.

1. **"Platinum Retention" Trap**
   - Condition: Retention_3s > 65% AND Completion > 40% AND Aggregated_ER < 3%
   - Interpretation: High quality passive watching. Conversion failure, not content failure.
   - Action: 🟡 ITERATE. Add aggressive CTA.

2. **"Failed Breakout" (BS Filter)**
   - Condition: Views > 5× Average BUT Retention_3s < 58%
   - Interpretation: Algorithm gave a chance, content failed to hold attention.
   - Action: 🔴 KILL. Do not iterate.

3. **"Gold Format" Candidate**
   - Condition: Retention_3s in "Good" zone AND Share_Rate in "OK" zone
   - Interpretation: Stable performer (Workhorse). 20k–50k views potential.
   - Action: 🟡 ITERATE / LOCK FORMAT.

## DECISION TREE (Priority Order)
0. Platform Specifics → If YouTube "Viewed" < 50% OR TikTok "Stopped at" < 0:03 → 🔴 KILL IMMEDIATELY
1. Retention_3s < 58% → 🔴 KILL HOOK
2. Completion_Rate → Check against duration benchmark → If FAIL → ✂️ FIX BODY
3. Views < 3000 + Retention > 65% + Low ER → 🟡 ITERATE (Add CTA) — Platinum Trap
4. Retention "Good" + Share Rate "OK" (0.5–1.0%) → 🟡 ITERATE / POTENTIAL GOLD FORMAT
5. Views ≥ 3000 (or Spike) + Share Rate > 1.5% → 🚀 SCALE HARD

## OUTPUT FORMAT
Respond ONLY with valid JSON (no markdown fences, no extra text). Use this exact structure:

{
  "platform": "tiktok" | "youtube_shorts" | "reels" | "other",
  "video_duration_sec": <number or null>,
  "metrics": {
    "views": <number>,
    "likes": <number>,
    "comments": <number>,
    "shares": <number>,
    "saves": <number>,
    "retention_3s": <percentage as number or null>,
    "completion_rate": <percentage as number or null>,
    "avg_watch_time_pct": <percentage as number or null>,
    "viewed_pct": <YouTube Shorts: percentage or null>,
    "tiktok_churn_point": <string like "0:03" or null>
  },
  "calculated_rates": {
    "share_rate": <number or null>,
    "save_rate": <number or null>,
    "comment_rate": <number or null>,
    "aggregated_er": <number or null>
  },
  "tier_1_analysis": {
    "hook_3s": {"value": <number or null>, "rating": "FAIL|BORDERLINE|GOOD|SCALE", "note": "<short note>"},
    "completion": {"value": <number or null>, "rating": "FAIL|OK|EXCELLENT", "duration_bracket": "ultra_short|standard_short|long", "note": "<short note>"},
    "avg_watch_time": {"value": <number or null>, "rating": "FAIL|OK|GREAT", "note": "<short note>"}
  },
  "tier_2_analysis": {
    "volume_condition": "high_volume" | "low_volume",
    "share_rate": {"value": <number or null>, "rating": "OK|VIRAL|LOW"},
    "save_rate": {"value": <number or null>, "rating": "OK|HIGH_VALUE|LOW"},
    "comment_rate": {"value": <number or null>, "rating": "OK|EXCELLENT|LOW"},
    "aggregated_er": {"value": <number or null>, "rating": "FAIL|OK|HIDDEN_GEM"}
  },
  "expert_heuristics": [<list of triggered heuristic names, e.g. "Platinum Retention Trap">],
  "verdict": "🔴 KILL HOOK" | "🔴 KILL FIRST FRAME" | "✂️ FIX BODY" | "🟡 ITERATE" | "🟡 ITERATE / LOCK FORMAT" | "🚀 SCALE HARD",
  "score": <0-100 overall score>,
  "analysis": "<detailed 3-5 sentence analysis in user's language with specific actionable recommendations>",
  "recommendations": ["<recommendation 1>", "<recommendation 2>", "<recommendation 3>"]
}

IMPORTANT RULES:
- Calculate rates: share_rate = shares/views*100, save_rate = saves/views*100, etc.
- If a metric is not visible on the screenshot, use null (do NOT guess).
- Score formula: weighted composite 0-100 based on tier_1 (60% weight) and tier_2 (40% weight).
- Be BRUTALLY honest. Data over feelings.
- Give SPECIFIC, ACTIONABLE recommendations (not generic advice).
- If you see a retention/engagement graph, describe what you observe in the notes.
"""

# ---------------------------------------------------------------------------
# User prompt (дополнение к системному — идёт вместе с изображением)
# ---------------------------------------------------------------------------

USER_PROMPT = """Analyze this screenshot of video metrics/analytics.

Extract ALL visible numbers, graphs, and data points. Apply the full Metrics Bible benchmarks.
Follow the Decision Tree to arrive at the final verdict.

Rules:
- Identify the platform from the UI (TikTok / YouTube Shorts / Reels).
- Read retention and engagement graphs if visible.
- Calculate engagement rates from raw numbers (share_rate = shares/views*100, etc.).
- Apply expert heuristics if conditions match.
- If the image does NOT show analytics (no views, no metrics, wrong content), still respond with valid JSON: set "platform" to "other", use null for missing metrics, verdict "🟡 ITERATE", and in "analysis" briefly state what you see (e.g. "Screenshot does not show video analytics.").

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
    """Извлекает JSON из ответа Gemini (убирает markdown-обёртку, ищет объект в тексте)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Убираем BOM и лишние пробелы
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    # Вариант 1: весь ответ — JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Вариант 2: markdown ```json ... ``` или ``` ... ```
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
    # Вариант 3: в тексте есть объект { ... } (вступление от модели и т.д.)
    extracted = _extract_json_object(text)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as e:
            logger.warning("JSON from extracted block failed: %s", e)
    logger.warning("Failed to parse Gemini JSON. Raw (first 600 chars): %s", text[:600])
    return None


def _normalize_model(name: str) -> str:
    """Подставляет рабочую модель, если указана несуществующая (например gemini-3.0-flash)."""
    n = (name or "").strip()
    if n in VALID_GEMINI_MODELS:
        return n
    if n in ("gemini-3.0-flash", "gemini-3.0-flash-preview"):
        logger.info("Mapping unsupported model %r to gemini-2.0-flash", n)
        return "gemini-2.0-flash"
    # Любое другое значение используем как есть (API вернёт ошибку при неверном имени)
    return n or "gemini-2.0-flash"


def analyze_screenshot(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Отправляет скриншот в Gemini с полным системным промптом.

    Returns:
        Tuple (parsed_result, raw_response_text).
        parsed_result: распарсенный JSON или None.
        raw_response_text: сырой текст ответа для дебага.
    """
    model_name = _normalize_model(config.GEMINI_MODEL)
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    contents = [image_part, USER_PROMPT]

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
            ),
        )
        if not response:
            logger.warning("Gemini returned None")
            return None, None

        # Проверяем блокировку (safety, empty candidate)
        if getattr(response, "prompt_feedback", None):
            pf = response.prompt_feedback
            if getattr(pf, "block_reason", None):
                logger.warning(
                    "Gemini blocked prompt: block_reason=%s",
                    getattr(pf, "block_reason", "unknown"),
                )
                return None, None
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            c = candidates[0]
            if getattr(c, "finish_reason", None) not in (None, "STOP", "FINISH_REASON_STOP", "MAX_TOKENS"):
                logger.warning(
                    "Gemini candidate finish_reason=%s",
                    getattr(c, "finish_reason", "unknown"),
                )
            for r in getattr(c, "safety_ratings", None) or []:
                if getattr(r, "probability", None) and str(getattr(r, "probability", "")).endswith("HIGH"):
                    logger.warning("Gemini safety rating HIGH: %s", r)

        raw_text = getattr(response, "text", None) or ""
        if not raw_text.strip():
            logger.warning("Empty Gemini response text (candidates: %s)", len(candidates))
            return None, None

        parsed = _parse_response(raw_text)
        return parsed, raw_text

    except Exception as e:
        logger.exception("Gemini analyze_screenshot failed: %s", e)
        return None, None
