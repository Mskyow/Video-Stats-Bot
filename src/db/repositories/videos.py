"""
Вставка результатов анализа в таблицу videos.
Реализация — при добавлении логики AI и форматтера.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


def insert_video(
    client: Client | None,
    user_id: int,
    platform: str | None,
    metrics: dict[str, Any],
    score: float,
    analysis: str | None,
) -> dict[str, Any] | None:
    """
    Вставляет запись в таблицу videos.
    Возвращает вставленную строку или None при ошибке.
    Вызов из async-кода: await asyncio.to_thread(insert_video, ...).
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip insert_video")
        return None
    try:
        payload = {
            "user_id": user_id,
            "platform": platform,
            "metrics": metrics,
            "score": score,
            "analysis": analysis,
        }
        resp = client.table("videos").insert(payload).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0]
        return None
    except Exception as e:
        logger.exception("insert_video failed: %s", e)
        return None
