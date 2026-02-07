"""
Репозиторий видео: вставка результатов анализа, получение истории.

Хранит:
- Сырые метрики (views, likes, shares, saves, comments, retention и т.д.)
- Рассчитанные rates (share_rate, save_rate, comment_rate, aggregated_er)
- Tier 1 и Tier 2 анализ
- Вердикт (KILL / ITERATE / SCALE HARD)
- Hook score
- Полный текст ответа AI (для дебага)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


def insert_video(
    client: Client | None,
    user_id: int,
    result: dict[str, Any],
    raw_ai_response: str | None = None,
) -> dict[str, Any] | None:
    """
    Вставляет полный результат анализа в таблицу videos.

    Args:
        client: Supabase client
        user_id: Telegram user ID
        result: распарсенный JSON от OpenRouter (Gemini 3 Flash)
        raw_ai_response: сырой текст ответа (для дебага)

    Returns:
        Вставленная строка или None.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip insert_video")
        return None

    try:
        # Извлекаем данные из результата AI
        metrics = result.get("metrics") or {}
        calculated_rates = result.get("calculated_rates") or {}
        tier_1 = result.get("tier_1_analysis") or {}
        tier_2 = result.get("tier_2_analysis") or {}

        # Объединяем метрики и rates в одно поле
        full_metrics = {**metrics, **calculated_rates}

        # Детальный анализ: tier_1 + tier_2 + heuristics + recommendations
        detailed = {
            "tier_1": tier_1,
            "tier_2": tier_2,
            "expert_heuristics": result.get("expert_heuristics") or [],
            "recommendations": result.get("recommendations") or [],
        }

        # Hook score из tier_1
        hook_rating = None
        if tier_1.get("hook_3s"):
            hook_rating = tier_1["hook_3s"].get("rating")

        payload: dict[str, Any] = {
            "user_id": user_id,
            "platform": result.get("platform"),
            "title": result.get("title"),
            "metrics": full_metrics,
            "score": float(result.get("score") or 0),
            "analysis": result.get("analysis"),
            "verdict": result.get("verdict"),
            "hook_score": hook_rating,
            "detailed_analysis": detailed,
            "video_duration_sec": result.get("video_duration_sec"),
        }

        if raw_ai_response:
            payload["raw_ai_response"] = raw_ai_response

        resp = client.table("videos").insert(payload).execute()
        if resp.data and len(resp.data) > 0:
            logger.info(
                "Saved video analysis: user=%s, platform=%s, verdict=%s, score=%s",
                user_id,
                result.get("platform"),
                result.get("verdict"),
                result.get("score"),
            )
            return resp.data[0]
        return None

    except Exception as e:
        logger.exception("insert_video failed: %s", e)
        return None


def get_user_videos(
    client: Client | None,
    user_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Возвращает последние N анализов пользователя."""
    if client is None:
        return []
    try:
        resp = (
            client.table("videos")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.exception("get_user_videos failed: %s", e)
        return []


def get_user_stats_summary(
    client: Client | None,
    user_id: int,
) -> dict[str, Any]:
    """
    Сводная статистика пользователя:
    - Всего анализов
    - Средний score
    - Распределение вердиктов
    """
    if client is None:
        return {}
    try:
        videos = get_user_videos(client, user_id, limit=1000)
        if not videos:
            return {"total": 0}

        total = len(videos)
        scores = [v.get("score", 0) for v in videos if v.get("score") is not None]
        avg_score = sum(scores) / len(scores) if scores else 0

        verdicts: dict[str, int] = {}
        for v in videos:
            vrd = v.get("verdict") or "unknown"
            verdicts[vrd] = verdicts.get(vrd, 0) + 1

        platforms: dict[str, int] = {}
        for v in videos:
            p = v.get("platform") or "unknown"
            platforms[p] = platforms.get(p, 0) + 1

        hook_stats: dict[str, int] = {}
        for v in videos:
            h = v.get("hook_score") or "unknown"
            hook_stats[h] = hook_stats.get(h, 0) + 1

        return {
            "total": total,
            "avg_score": round(avg_score, 1),
            "verdicts": verdicts,
            "platforms": platforms,
            "hook_stats": hook_stats,
        }
    except Exception as e:
        logger.exception("get_user_stats_summary failed: %s", e)
        return {}


def get_hook_statistics(
    client: Client | None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    Возвращает накопленную статистику по хукам: количество записей по hook_score.

    Если user_id задан — только по этому пользователю; иначе по всей базе (глобально).
    Удобно для отчётов и для накопления данных в единую базу знаний по хукам.
    """
    if client is None:
        return {}
    try:
        query = (
            client.table("videos")
            .select("hook_score, score")
            .not_.is_("hook_score", "null")
        )
        if user_id is not None:
            query = query.eq("user_id", user_id)
        resp = query.execute()
        rows = resp.data or []

        hook_counts: dict[str, int] = {}
        scores_by_hook: dict[str, list[float]] = {}
        for row in rows:
            h = row.get("hook_score") or "unknown"
            hook_counts[h] = hook_counts.get(h, 0) + 1
            sc = row.get("score")
            if sc is not None:
                scores_by_hook.setdefault(h, []).append(float(sc))

        avg_by_hook: dict[str, float] = {}
        for h, vals in scores_by_hook.items():
            avg_by_hook[h] = round(sum(vals) / len(vals), 1) if vals else 0

        return {
            "total_with_hook": sum(hook_counts.values()),
            "hook_counts": hook_counts,
            "avg_score_by_hook": avg_by_hook,
        }
    except Exception as e:
        logger.exception("get_hook_statistics failed: %s", e)
        return {}
