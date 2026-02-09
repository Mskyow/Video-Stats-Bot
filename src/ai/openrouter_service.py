"""
Анализ скриншотов с метриками видео и каруселей через OpenRouter (Gemini 3 Flash).

Функционал:
- Авто-определение типа контента (Video vs Carousel) по UI-маркерам.
- OCR текста хука с миниатюры.
- Адаптивная система оценки (Retention для видео, Save Rate для каруселей).
"""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import requests

from src import config

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"

# ---------------------------------------------------------------------------
# System Prompt: Optimized & Strict
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are **Creator Copilot** — a Senior Growth Analyst.

## MISSION
Analyze social media metrics screenshots to determine content performance.
You must distinct between **VIDEO** (Reels/TikTok/Shorts) and **CAROUSEL** (Photo Mode/Slides).

## 1. CLASSIFICATION PROTOCOL (STRICT)
Look at the UI elements to determine `content_type`:

**TYPE: CAROUSEL** (If ANY of these exist):
- Text "Photos viewed"
- Text "Post analysis"
- Text "Photo mode"
- Pagination indicators (e.g., "1/5", dots at bottom)
- "Inspiration" tab visible in header

**TYPE: VIDEO** (If ANY of these exist):
- Text "Average watch time"
- Text "Video analysis"
- Text "Watched full video"
- Text "Total play time"
- A retention graph curve

## 2. DATA EXTRACTION RULES
- **OCR Hook Text (CRITICAL):** Look at the thumbnail image (usually at the top). Read the main text/headline overlaid on the image. Field: `hook_text`.
- **OCR Title:** Look for the caption/description text below the image. Field: `video_title`.
- **Date Extraction (CRITICAL):** Look strictly BELOW the video thumbnail/preview image for the date.
  - **TikTok:** Extract date and time (format usually "MM-DD HH:MM"). Field: `posted_at`.
  - **Instagram:** Extract date (usually "Month DD"). Field: `posted_at`.
- **Hook Type Classification (by word count):** 
  After extracting `hook_text`, COUNT THE WORDS and assign `hook_type` STRICTLY by these rules:
  - **Short Hook:** 1-12 words (Goal: Stop Scroll). Assign: "short"
  - **Medium Hook:** 13-30 words (Goal: Curiosity Gap). Assign: "medium"
  - **Long Hook:** 31+ words (Goal: Storytelling). Assign: "long"
  Field: `hook_type`
- **Average Watch Time (TikTok Fix):** Look specifically for the text block "On average, viewers watched **X%** of your video" inside the Retention section. If found, extract the percentage value. Field: `avg_watch_time_pct`.
- **Carousel Retention (Analog):** For CAROUSEL content ONLY — analyze the retention graph. Estimate the percentage of users who reached the **3rd slide/photo**. Field: `retention_3s` (use this field as analog to video's 3-second retention).
- **Metrics:** Extract all visible numbers. If a metric is missing (e.g., retention on a carousel), set to `null`.
- **Calculated Rates:** Always calculate `aggregated_er = (likes + comments + shares + saves) / views * 100`.

## 3. BENCHMARKS & SCORING

### MODE A: VIDEO SCORING
*Focus: Attention span & Hooks.*
- **Retention (3s):** <55% (FAIL), 55-70% (OK), >70% (GOOD).
- **Avg Watch Time:** <3s (FAIL), 3-5s (OK), >6s (EXCELLENT).
- **Verdict Logic:**
  - If Retention 3s < 55% -> 🔴 KILL HOOK
  - If Retention OK but Avg Watch Time Low -> ✂️ FIX BODY
  - If High Retention + Low Views -> 🟡 ITERATE

### MODE B: CAROUSEL SCORING
*Focus: Depth & Engagement Intensity.*

**1. Completion / Swipe-Through (Photos Viewed / Total):**
- Formula: `photos_viewed / total_photos`
- **FAIL (Kill):** < 45% (Cover/First slides failed)
- **OK (Iterate):** 45% – 65%
- **GREAT (Scale):** >= 65% (Strong UGC target)

**2. Engagement Rate (ER View):**
- Formula: `(likes + comments + shares + saves) / views`
- **FAIL (Kill):** < 3%
- **OK (Iterate):** 3% – 6%
- **GREAT (Scale):** >= 6% (8-10% is Top Tier)

**3. Viral Potential (Share + Save Rate):**
- Formula: `(shares + saves) / views`
- **FAIL (Kill):** < 2.5%
- **OK (Iterate):** 3% – 6%
- **GREAT (Scale):** >= 6% (Viral Zone for app content)

**4. Discussion Depth (Comment Share):**
- Formula: `comments / (likes + comments + shares + saves)`
- **FAIL (Kill):** < 5%
- **OK (Iterate):** 5% – 15%
- **GREAT (Scale):** >= 15% (Target for strong product interest)

**5. Time Efficiency (AVD Ratio):**
- *If Avg Watch Time available:* Compare to (Total Slides * 3.5s).
- **FAIL:** < 0.4
- **OK:** 0.4 – 0.65
- **GREAT:** >= 0.65

**Verdict Logic (Carousel):**
- If Completion < 45% -> 🔴 KILL HOOK (Cover failed)
- If Share+Save Rate >= 6% -> 🚀 SCALE HARD (High Value)
- If ER < 3% but Completion OK -> ✂️ FIX VALUE (Add CTA)
- If Comment Share >= 15% -> 🟢 ITERATE (High Interest)

## 4. OUTPUT FORMAT (JSON ONLY)
Respond with this exact JSON structure. No markdown, no conversation.

{
  "content_type": "video" | "carousel",
  "hook_text": "<text found on the thumbnail image>",
  "hook_type": "short" | "medium" | "long",
  "video_title": "<caption text or null>",
  "posted_at": "<extracted date string or null>",
  "platform": "tiktok" | "instagram" | "youtube" | "other",
  "metrics": {
    "views": <number>,
    "likes": <number>,
    "comments": <number>,
    "shares": <number>,
    "saves": <number>,
    "retention_3s": <number_pct or null>,
    "avg_watch_time_sec": <number or null>,
    "avg_watch_time_pct": <number_pct or null>,
    "photos_viewed": <number or null>,
    "total_photos": <number or null>
  },
  "score": <0-100>,
  "verdict": "🔴 KILL HOOK" | "✂️ FIX BODY" | "🟡 ITERATE" | "🚀 SCALE HARD",
  "analysis": "<Short analysis in Russian. Explain WHY based on the content type metrics.>",
  "recommendations": ["<Actionable tip 1>", "<Actionable tip 2>"]
}
"""

USER_PROMPT = """Analyze these screenshots.
Current Date: {current_time_str}

**Tasks:**
1. Determine `content_type` (Video vs Carousel) based on UI markers ("Video analysis" vs "Post analysis").
2. Extract `hook_text` strictly from the image thumbnail.
3. Extract `video_title` from the caption.
4. Fill all metrics.
5. Provide a verdict based on the specific logic for that content type.

Return ONLY JSON.
"""


def _extract_json_object(text: str) -> str | None:
    """Находит первый полный JSON-объект { ... } в тексте."""
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
    """Извлекает и валидирует JSON из ответа."""
    if not text:
        return None
    
    # Попытка 1: Прямой парсинг
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Попытка 2: Извлечение из Markdown ```json ... ```
    if "```" in text:
        lines = text.split("\n")
        out = []
        in_block = False
        for line in lines:
            if "```" in line:
                in_block = not in_block
                continue
            if in_block:
                out.append(line)
        if out:
            try:
                return json.loads("\n".join(out))
            except json.JSONDecodeError:
                pass

    # Попытка 3: Поиск по скобкам
    extracted = _extract_json_object(text)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    logger.error("Failed to parse JSON response. Raw length: %d", len(text))
    return None


def analyze_screenshot(
    images_list: list[bytes],
    mime_type: str = "image/jpeg",
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Отправляет скриншоты в OpenRouter.
    """
    model = (config.OPENROUTER_MODEL or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    api_key = config.OPENROUTER_API_KEY

    current_time_str = datetime.now(ZoneInfo("Europe/Minsk")).strftime("%Y-%m-%d %H:%M:%S GMT+3")
    user_prompt_formatted = USER_PROMPT.format(current_time_str=current_time_str)

    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt_formatted}]

    for image_bytes in images_list:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"
        content.append({"type": "image_url", "image_url": {"url": data_uri}})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "reasoning": {"effort": "medium"}, # Включаем thinking для сложных кейсов
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
            timeout=60,
        )
        resp.raise_for_status()
        
        data = resp.json()
        choice = data.get("choices", [])[0]
        raw_text = choice.get("message", {}).get("content", "")
        
        parsed = _parse_response(raw_text)
        
        # Небольшой пост-процессинг для надежности
        if parsed:
            # Fallback для video_title, если OCR не сработал, но есть hook_text
            if not parsed.get("video_title") and parsed.get("hook_text"):
                parsed["video_title"] = parsed["hook_text"]
                
        return parsed, raw_text

    except Exception as e:
        logger.exception("AI Analysis failed: %s", e)
        return None, None