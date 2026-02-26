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
import re
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import dateparser

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


def _to_optional_float(value: Any) -> float | None:
    """Безопасно преобразует значение в float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_posted_at_for_parsing(posted_at: Any) -> str | None:
    if posted_at is None:
        return None
    text = str(posted_at).strip()
    if not text:
        return None
    text = re.sub(r"^\s*posted\s+on\s+", "", text, flags=re.IGNORECASE)
    return text.strip() or None


class VideoStatus(Enum):
    """Статус проверки видео на дубликаты."""
    NEW = "new"
    UPDATE = "update"
    DUPLICATE = "duplicate"


def normalize_title(title: str | None) -> str:
    """Нормализует название для сравнения (убирает пробелы, приводит к нижнему регистру)."""
    if not title:
        return ""
    return title.lower().replace(" ", "").replace("\t", "").replace("\n", "")


def check_video_status(
    client: Client | None,
    user_id: int,
    title: str | None,
    posted_at: str | None,
    new_views: int | float | None,
) -> tuple[VideoStatus, dict[str, Any] | None]:
    """
    Проверяет статус видео: новое, обновление или дубликат.

    Args:
        client: Supabase client
        user_id: Telegram user ID
        title: Название видео
        posted_at: Дата публикации (строка)
        new_views: Количество просмотров в новых данных

    Returns:
        Кортеж (статус, старая запись или None)
        - NEW: видео не найдено
        - UPDATE: видео найдено, просмотры выросли на 2%+
        - DUPLICATE: видео найдено, просмотры не изменились
    """
    if client is None:
        return VideoStatus.NEW, None

    if not title or not posted_at:
        logger.debug("Missing title or posted_at, treating as NEW")
        return VideoStatus.NEW, None

    try:
        # Ищем последнее видео этого пользователя с таким posted_at
        resp = (
            client.table("videos")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )

        videos = resp.data or []
        normalized_new_title = normalize_title(title)

        # Ищем видео с совпадающим posted_at и названием
        matching_video = None
        for video in videos:
            video_metrics = video.get("metrics") or {}
            # posted_at может быть в метриках или как поле top-level
            video_posted_at = video_metrics.get("posted_at") or video.get("posted_at")
            video_title = video.get("title")

            if video_posted_at == posted_at:
                normalized_old_title = normalize_title(video_title)
                if normalized_old_title == normalized_new_title:
                    matching_video = video
                    break

        if not matching_video:
            return VideoStatus.NEW, None

        # Проверяем изменение просмотров
        metrics = matching_video.get("metrics") or {}
        old_views = metrics.get("views")

        if old_views is None or new_views is None:
            # Нет данных для сравнения, считаем дубликатом
            return VideoStatus.DUPLICATE, matching_video

        try:
            old_views_float = float(old_views)
            new_views_float = float(new_views)
        except (ValueError, TypeError):
            return VideoStatus.DUPLICATE, matching_video

        # Проверяем рост на 2% и более
        if old_views_float > 0:
            growth_percent = ((new_views_float - old_views_float) / old_views_float) * 100
        else:
            growth_percent = 100 if new_views_float > 0 else 0

        if growth_percent >= 2:
            logger.info(
                "Video UPDATE detected: views %s -> %s (+%.1f%%)",
                old_views_float,
                new_views_float,
                growth_percent,
            )
            return VideoStatus.UPDATE, matching_video
        else:
            logger.debug(
                "Video DUPLICATE detected: views %s -> %s (%.1f%% change)",
                old_views_float,
                new_views_float,
                growth_percent,
            )
            return VideoStatus.DUPLICATE, matching_video

    except Exception as e:
        logger.exception("check_video_status failed: %s", e)
        return VideoStatus.NEW, None


def insert_video(
    client: Client | None,
    user_id: int,
    result: dict[str, Any],
    raw_ai_response: str | None = None,
) -> dict[str, Any] | None:
    """
    Вставляет полный результат анализа в таблицу videos.
    
    Реализует Smart Deduplication:
    - Проверяет, есть ли уже такое видео (по user_id + title + posted_at)
    - Если просмотры выросли на 2%+ → сохраняет новый снимок
    - Если просмотры не изменились → возвращает маркер дубликата

    Args:
        client: Supabase client
        user_id: Telegram user ID
        result: распарсенный JSON от OpenRouter (Gemini 3 Flash)
        raw_ai_response: сырой текст ответа (для дебага)

    Returns:
        Вставленная строка, None (если ошибка), или объект с флагом skipped=True для дубликатов.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip insert_video")
        return None
    if not isinstance(result, dict):
        logger.error("insert_video expects dict result, got: %s", type(result).__name__)
        return None

    # Извлекаем данные для проверки дубликатов
    metrics = result.get("metrics") or {}
    title = result.get("title") or result.get("video_title") or result.get("hook_text")
    posted_at = result.get("posted_at")
    new_views = metrics.get("views")

    # Проверяем статус видео
    status, existing_video = check_video_status(
        client, user_id, title, posted_at, new_views
    )

    if status == VideoStatus.DUPLICATE:
        logger.info(
            "Skipping duplicate video for user %s: '%s' at %s",
            user_id,
            title,
            posted_at,
        )
        return {"skipped": True, "duplicate": True, "existing_video": existing_video}

    if status == VideoStatus.UPDATE:
        logger.info(
            "Saving update for video user %s: '%s' at %s (views changed)",
            user_id,
            title,
            posted_at,
        )

    try:
        # Извлекаем данные из результата AI
        metrics = result.get("metrics") or {}
        # Гарантируем, что engagement-метрики всегда присутствуют
        for key in ("views", "likes", "comments", "shares", "saves"):
            value = metrics.get(key)
            if value is None:
                metrics[key] = 0
        calculated_rates = result.get("calculated_rates") or {}
        tier_1_raw = result.get("tier_1_analysis")
        tier_2_raw = result.get("tier_2_analysis")
        tier_1 = tier_1_raw if isinstance(tier_1_raw, dict) else {}
        tier_2 = tier_2_raw if isinstance(tier_2_raw, dict) else {}

        # Объединяем метрики и rates в одно поле
        full_metrics = {**metrics, **calculated_rates}

        # Add hook_type to metrics if present
        if result.get("hook_type"):
            full_metrics["hook_type"] = result.get("hook_type")

        # Calculate video age in hours from posted_at using dateparser
        posted_at_str = _normalize_posted_at_for_parsing(result.get("posted_at"))
        age_hours = None
        if posted_at_str:
            try:
                posted_at_dt = dateparser.parse(posted_at_str)
                if posted_at_dt:
                    age_hours = (datetime.utcnow() - posted_at_dt).total_seconds() / 3600
                    age_hours = round(age_hours, 1)
                else:
                    logger.warning("Failed to parse posted_at '%s' with dateparser", posted_at_str)
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse posted_at '%s': %s", posted_at_str, e)
        full_metrics["age_hours"] = age_hours

        # Детальный анализ: tier_1 + tier_2 + heuristics + recommendations
        detailed = {
            "tier_1": tier_1,
            "tier_2": tier_2,
            "score_breakdown": result.get("score_breakdown") or {},
            "expert_heuristics": result.get("expert_heuristics") or [],
            "recommendations": result.get("recommendations") or [],
        }

        # Hook score из tier_1
        hook_rating = None
        hook_3s = tier_1.get("hook_3s")
        if isinstance(hook_3s, dict):
            hook_rating = hook_3s.get("rating")
        elif isinstance(hook_3s, str):
            hook_rating = hook_3s
        elif isinstance(result.get("hook_score"), str):
            hook_rating = result.get("hook_score")

        score_value = _to_optional_float(result.get("score")) or 0.0
        duration_value = _to_optional_float(result.get("video_duration_sec"))
        # В таблице поле целочисленное, поэтому нормализуем к int.
        # Это защищает insert от значений вроде "7.85".
        duration_int = int(round(duration_value)) if duration_value is not None else None

        # 3-screenshot mode: store end retention as separate columns for analytics
        end_sec_raw = metrics.get("end_retention_second")
        end_pct_raw = _to_optional_float(metrics.get("end_retention_pct"))
        end_retention_second_int = None
        if end_sec_raw is not None:
            try:
                end_retention_second_int = int(round(float(end_sec_raw)))
            except (TypeError, ValueError):
                pass
        end_retention_pct_num = end_pct_raw  # keep as float for NUMERIC column

        payload: dict[str, Any] = {
            "user_id": user_id,
            "platform": result.get("platform"),
            "title": title,
            "metrics": full_metrics,
            "score": score_value,
            "analysis": result.get("analysis"),
            "verdict": result.get("verdict"),
            "hook_score": hook_rating,
            "detailed_analysis": detailed,
            "video_duration_sec": duration_int,
            "content_type": result.get("content_type", "video"),
            "hook_text": result.get("hook_text"),
            "end_retention_second": end_retention_second_int,
            "end_retention_pct": end_retention_pct_num,
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


def get_videos_by_date_range(
    client: Client | None,
    start_date: str,
    end_date: str,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Возвращает список видео за указанный диапазон дат, отсортированных по score DESC.
    
    Args:
        client: Supabase client
        start_date: Дата начала (ISO string)
        end_date: Дата конца (ISO string)
        user_id: (Опционально) фильтр по ID пользователя. Если не задан - возвращает для всех.
    """
    if client is None:
        return []
    try:
        # Assuming created_at is a timestamp or date string
        # Supabase filtering: gte (>=) start_date, lte (<=) end_date
        
        query = (
            client.table("videos")
            .select("*")
            .gte("created_at", start_date)
            .lte("created_at", end_date)
            .order("score", desc=True)
        )
        
        if user_id:
            query = query.eq("user_id", user_id)
            
        resp = query.execute()
        return resp.data or []
    except Exception as e:
        logger.exception("get_videos_by_date_range failed: %s", e)
        return []


def _normalize_platform_name(raw_platform: Any) -> str | None:
    """Нормализует название платформы к одной из целевых для сводки."""
    if raw_platform is None:
        return None

    platform_text = str(raw_platform).lower()
    if "tiktok" in platform_text:
        return "TikTok"
    if "instagram" in platform_text or "reels" in platform_text:
        return "Instagram"
    return None


def _parse_views_value(raw_views: Any) -> int:
    """
    Преобразует просмотры к целому числу.
    Поддерживает:
    - int / float
    - строки вида "12 345", "12,345", "12.3K", "1.2M"
    """
    if raw_views is None:
        return 0

    if isinstance(raw_views, bool):
        return 0

    if isinstance(raw_views, (int, float)):
        return int(raw_views)

    if not isinstance(raw_views, str):
        return 0

    normalized = raw_views.strip().upper().replace(" ", "").replace(",", "")
    if not normalized:
        return 0

    multiplier = 1
    if normalized.endswith("K"):
        multiplier = 1000
        normalized = normalized[:-1]
    elif normalized.endswith("M"):
        multiplier = 1000000
        normalized = normalized[:-1]
    elif normalized.endswith("B"):
        multiplier = 1000000000
        normalized = normalized[:-1]

    try:
        return int(float(normalized) * multiplier)
    except (ValueError, TypeError):
        return 0


def get_global_stats(client: Client | None) -> dict[str, Any]:
    """
    Возвращает глобальную статистику по TikTok и Instagram:
    - total_count (int): общее число видео в базе
    - platforms (dict): агрегаты по платформам
      - total_videos (int)
      - avg_score (float)
      - max_views (int)
    """
    if client is None:
        return {}
    try:
        resp = client.table("videos").select("*").execute()
        videos = resp.data or []

        total_count = len(videos)

        platform_aggregates: dict[str, dict[str, Any]] = {
            "TikTok": {"total_videos": 0, "score_sum": 0.0, "score_count": 0, "max_views": 0},
            "Instagram": {"total_videos": 0, "score_sum": 0.0, "score_count": 0, "max_views": 0},
        }

        for video in videos:
            platform_name = _normalize_platform_name(video.get("platform"))
            if not platform_name:
                continue

            aggregate = platform_aggregates[platform_name]
            aggregate["total_videos"] += 1

            score = video.get("score")
            if score is not None:
                try:
                    score_value = float(score)
                    aggregate["score_sum"] += score_value
                    aggregate["score_count"] += 1
                except (ValueError, TypeError):
                    pass

            metrics = video.get("metrics") or {}
            raw_views = metrics.get("views") or metrics.get("view_count")
            views_value = _parse_views_value(raw_views)
            if views_value > aggregate["max_views"]:
                aggregate["max_views"] = views_value

        formatted_platforms: dict[str, dict[str, Any]] = {}
        for platform_name, aggregate in platform_aggregates.items():
            score_count = aggregate["score_count"]
            avg_score = (
                round(aggregate["score_sum"] / score_count, 1)
                if score_count > 0
                else 0.0
            )
            formatted_platforms[platform_name] = {
                "total_videos": aggregate["total_videos"],
                "avg_score": avg_score,
                "max_views": aggregate["max_views"],
            }

        return {
            "total_count": total_count,
            "platforms": formatted_platforms,
        }
    except Exception as e:
        logger.exception("get_global_stats failed: %s", e)
        return {}


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
