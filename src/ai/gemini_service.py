"""
Анализ скриншота с метриками видео через Gemini.
Возвращает платформу, метрики, score и краткий анализ.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import google.generativeai as genai

from src import config

logger = logging.getLogger(__name__)

PROMPT = """На изображении — скриншот с метриками видео (TikTok, Reels, YouTube Shorts и т.п.).
Распознай все видимые цифры: просмотры, лайки, комментарии, репосты, сохранения (если есть).
Определи платформу по интерфейсу (tiktok, reels, shorts, other).
Посчитай Score по формуле: Score = (лайки * 2 + комментарии * 3 + репосты * 4 + сохранения * 2) / (просмотры / 1000 + 1). Если каких-то метрик нет — используй 0.
Дай краткий вывод в 1–2 предложения: насколько контент заходит, что выделяется.

Ответь ТОЛЬКО валидным JSON без markdown и лишнего текста, в таком формате:
{
  "platform": "tiktok|reels|shorts|other",
  "metrics": {
    "views": число,
    "likes": число,
    "comments": число,
    "shares": число,
    "saves": число
  },
  "score": число (округли до 2 знаков),
  "analysis": "краткий вывод текстом"
}
"""


def _parse_response(text: str) -> dict[str, Any] | None:
    """Достаёт JSON из ответа (на случай если модель обернула в markdown)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse Gemini JSON: %s", e)
        return None


def analyze_screenshot(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any] | None:
    """
    Отправляет изображение в Gemini, возвращает распознанные метрики и анализ.
    Вызывать из async-кода через asyncio.to_thread(analyze_screenshot, ...).
    """
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)

    part = {"inline_data": {"mime_type": mime_type, "data": image_bytes}}
    contents = [part, PROMPT]

    try:
        response = model.generate_content(contents)
        if not response or not response.text:
            logger.warning("Empty or blocked Gemini response")
            return None
        return _parse_response(response.text)
    except Exception as e:
        logger.exception("Gemini analyze_screenshot failed: %s", e)
        return None
