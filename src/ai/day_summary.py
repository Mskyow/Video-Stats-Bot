"""
Evidence-driven AI day summary for /day_stats.

This module builds a compact dataset from Supabase video rows, computes
deterministic correlations and hook clusters, then asks the LLM to produce
an actionable summary constrained by facts and thresholds.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from typing import Any

import requests

from src import config
from src.ai.benchmarks import CAROUSEL_BENCHMARKS_CONTEXT, VIDEO_BENCHMARKS_CONTEXT

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"

# In-memory cache: key -> (rendered_summary_html, timestamp)
_cache: dict[str, tuple[str, float]] = {}

_HOOK_STOPWORDS = {
    "и",
    "в",
    "на",
    "с",
    "по",
    "для",
    "как",
    "что",
    "это",
    "а",
    "но",
    "не",
    "за",
    "из",
    "к",
    "у",
    "о",
    "или",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "you",
    "your",
    "to",
    "of",
    "in",
    "on",
}

_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview_text": {
            "type": "string",
            "description": (
                "2-3 предложения живым языком: что произошло за день, "
                "какое видео лучшее и чем выделяется. Не дублируй сырые данные."
            ),
        },
        "top_hooks_list": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
            "description": (
                "До 3 лучших хуков с кратким пояснением почему они сильные."
            ),
        },
        "patterns_analysis": {
            "type": "string",
            "description": (
                "1-3 предложения с выводами: что сработало и почему. "
                "Интерпретируй данные, не пересказывай цифры."
            ),
        },
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
            "description": (
                "1-3 конкретных приземлённых next steps: масштабировать хуки, "
                "добавить CTA, переснять слабые секунды и т.д."
            ),
        },
    },
    "required": [
        "overview_text",
        "top_hooks_list",
        "patterns_analysis",
        "action_items",
    ],
    "additionalProperties": False,
}


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace("%", "").replace(",", ".")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _safe_int(value: Any) -> int | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _trim_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(10, limit - 1)].rstrip() + "…"


def _normalize_platform(raw_platform: Any) -> str:
    platform = str(raw_platform or "other").strip().lower()
    if "tiktok" in platform:
        return "TikTok"
    if "instagram" in platform or "reel" in platform:
        return "Instagram"
    if "youtube" in platform or "shorts" in platform:
        return "YouTube Shorts"
    return "Other"


def _normalize_verdict(raw_verdict: Any) -> str:
    verdict = str(raw_verdict or "UNKNOWN").upper()
    if "SCALE" in verdict:
        return "SCALE"
    if "KILL" in verdict:
        return "KILL"
    if "ITERATE" in verdict or "FIX" in verdict:
        return "ITERATE"
    return "UNKNOWN"


def _normalize_hook_type(raw_hook_type: Any) -> str:
    hook_type = str(raw_hook_type or "").strip().lower()
    if hook_type in {"short", "medium", "long"}:
        return hook_type
    return "unknown"


def _normalize_hook_tokens(hook_text: str) -> set[str]:
    normalized = re.sub(r"[^\w\s]", " ", hook_text.lower())
    tokens = {token for token in normalized.split() if len(token) > 2}
    return {token for token in tokens if token not in _HOOK_STOPWORDS}


def _jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 3)


def _percent_delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline == 0:
        return None
    return round(((value - baseline) / abs(baseline)) * 100.0, 2)


def _compute_confidence_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample_size = len(rows)
    if sample_size <= 0:
        return {
            "label": "низкая",
            "sample_size": 0,
            "score_coverage_pct": 0.0,
            "retention_coverage_pct": 0.0,
            "reason": "Нет данных.",
        }

    score_count = sum(1 for row in rows if _safe_float(row.get("score")) is not None)
    retention_count = sum(1 for row in rows if _safe_float(row.get("retention_3s")) is not None)
    score_coverage_pct = round((score_count / sample_size) * 100.0, 1)
    retention_coverage_pct = round((retention_count / sample_size) * 100.0, 1)
    min_coverage = min(score_coverage_pct, retention_coverage_pct)

    if sample_size >= 12 and min_coverage >= 75.0:
        label = "высокая"
    elif sample_size >= 6 and min_coverage >= 50.0:
        label = "средняя"
    else:
        label = "низкая"

    reason = (
        f"n={sample_size}, покрытие score={score_coverage_pct:.1f}%, "
        f"retention_3s={retention_coverage_pct:.1f}%."
    )
    return {
        "label": label,
        "sample_size": sample_size,
        "score_coverage_pct": score_coverage_pct,
        "retention_coverage_pct": retention_coverage_pct,
        "reason": reason,
    }


def _has_numeric_kpi(text: str) -> bool:
    lowered = text.lower()
    has_number = bool(re.search(r"\d", lowered))
    has_metric = bool(
        re.search(
            r"(retention|score|views|completion|share|save|ctr|er|удерж|просмотр|лайк|коммент)",
            lowered,
        )
    )
    return has_number and has_metric


def _sanitize_summary_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    top_hooks_raw = payload.get("top_hooks_list")
    top_hooks_list = []
    if isinstance(top_hooks_raw, list):
        top_hooks_list = [str(item).strip() for item in top_hooks_raw if str(item).strip()]

    action_items_raw = payload.get("action_items")
    action_items = []
    if isinstance(action_items_raw, list):
        action_items = [str(item).strip() for item in action_items_raw if str(item).strip()]

    return {
        "overview_text": str(payload.get("overview_text", "")).strip(),
        "top_hooks_list": top_hooks_list,
        "patterns_analysis": str(payload.get("patterns_analysis", "")).strip(),
        "action_items": action_items,
    }


def _enhance_summary_payload(payload: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    """
    Fill in missing fields from evidence without injecting raw technical suffixes.
    The AI prompt now handles natural-language generation; this function only
    provides fallback data when the model left a field empty.
    """
    enhanced = dict(payload)

    if not str(enhanced.get("overview_text", "")).strip():
        enhanced["overview_text"] = "Недостаточно данных для общей картины."

    top_hooks = enhanced.get("top_hooks_list")
    if not isinstance(top_hooks, list) or not top_hooks:
        top_hooks = []
        if evidence:
            top_hooks_data = evidence.get("top_hooks_data") or []
            for hook in top_hooks_data[:3]:
                hook_text = _trim_text(hook.get("hook_text") or "", limit=90)
                if not hook_text:
                    continue
                retention = _safe_float(hook.get("retention_3s"))
                if retention is not None:
                    top_hooks.append(f"{hook_text} — retention {retention:.0f}%")
                else:
                    top_hooks.append(hook_text)
        if not top_hooks:
            top_hooks = ["Недостаточно данных по хукам."]
    enhanced["top_hooks_list"] = top_hooks

    if not str(enhanced.get("patterns_analysis", "")).strip():
        enhanced["patterns_analysis"] = "Недостаточно данных для анализа паттернов."

    action_items = enhanced.get("action_items")
    if not isinstance(action_items, list) or not action_items:
        action_items = [
            "Масштабируй лучший хук дня — сними 2-3 вариации.",
            "Добавь CTA в конец роликов, чтобы поднять share и comment rate.",
        ]
    enhanced["action_items"] = action_items
    return enhanced


def _extract_compact_video(video: dict[str, Any]) -> dict[str, Any]:
    metrics = video.get("metrics") or {}
    hook_text = _trim_text(video.get("hook_text") or video.get("title") or "", limit=140)
    score = _safe_float(video.get("score"))
    views = _safe_int(metrics.get("views") or metrics.get("view_count"))
    likes = _safe_int(metrics.get("likes") or metrics.get("like_count"))
    comments = _safe_int(metrics.get("comments") or metrics.get("comment_count"))

    row = {
        "id": str(video.get("id") or ""),
        "platform": _normalize_platform(video.get("platform")),
        "score": round(score, 3) if score is not None else None,
        "verdict": _normalize_verdict(video.get("verdict")),
        "hook_text": hook_text,
        "hook_type": _normalize_hook_type(metrics.get("hook_type")),
        "video_duration_sec": _safe_int(video.get("video_duration_sec")),
        "views": views,
        "likes": likes,
        "comments": comments,
        "retention_3s": _safe_float(metrics.get("retention_3s")),
        "completion_rate": _safe_float(metrics.get("completion_rate")),
        "share_rate": _safe_float(metrics.get("share_rate")),
        "save_rate": _safe_float(metrics.get("save_rate")),
        "comment_rate": _safe_float(metrics.get("comment_rate")),
        "aggregated_er": _safe_float(metrics.get("aggregated_er")),
    }
    row["_hook_tokens"] = _normalize_hook_tokens(hook_text)
    return row


def _build_compact_dataset(videos: list[dict[str, Any]], max_videos: int = 60) -> list[dict[str, Any]]:
    """
    Convert DB rows into a compact and token-friendly dataset.
    """
    compact_rows = [_extract_compact_video(video) for video in videos]
    compact_rows.sort(
        key=lambda row: (
            -1e9 if row.get("score") is None else -float(row["score"]),
            -1 if row.get("views") is None else -int(row["views"]),
        )
    )
    return compact_rows[:max(1, max_videos)]


def _aggregate_group_metrics(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key) or "unknown")].append(row)

    result: list[dict[str, Any]] = []
    for group_name, group_rows in grouped.items():
        scores = [float(row["score"]) for row in group_rows if row.get("score") is not None]
        retentions = [float(row["retention_3s"]) for row in group_rows if row.get("retention_3s") is not None]
        share_rates = [float(row["share_rate"]) for row in group_rows if row.get("share_rate") is not None]
        save_rates = [float(row["save_rate"]) for row in group_rows if row.get("save_rate") is not None]
        completion_rates = [
            float(row["completion_rate"]) for row in group_rows if row.get("completion_rate") is not None
        ]
        views = [float(row["views"]) for row in group_rows if row.get("views") is not None]

        result.append(
            {
                "group": group_name,
                "count": len(group_rows),
                "avg_score": _mean(scores),
                "median_retention_3s": _median(retentions),
                "avg_share_rate": _mean(share_rates),
                "avg_save_rate": _mean(save_rates),
                "avg_completion_rate": _mean(completion_rates),
                "avg_views": _mean(views),
            }
        )

    result.sort(
        key=lambda item: (
            -item["count"],
            -1e9 if item["avg_score"] is None else -float(item["avg_score"]),
        )
    )
    return result


def _build_hook_clusters(
    rows: list[dict[str, Any]],
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.40,
) -> list[dict[str, Any]]:
    """
    Build simple hook text clusters using token Jaccard similarity.
    """
    candidates = [row for row in rows if row.get("_hook_tokens")]
    clusters: list[dict[str, Any]] = []

    for row in candidates:
        tokens = set(row["_hook_tokens"])
        best_idx = -1
        best_score = 0.0
        for idx, cluster in enumerate(clusters):
            score = _jaccard_similarity(tokens, cluster["tokens_union"])
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx != -1 and best_score >= similarity_threshold:
            cluster = clusters[best_idx]
            cluster["rows"].append(row)
            cluster["tokens_union"].update(tokens)
            cluster["token_counter"].update(tokens)
        else:
            clusters.append(
                {
                    "rows": [row],
                    "tokens_union": set(tokens),
                    "token_counter": Counter(tokens),
                }
            )

    rendered_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        rows_in_cluster = cluster["rows"]
        if len(rows_in_cluster) < min_cluster_size:
            continue

        scores = [float(row["score"]) for row in rows_in_cluster if row.get("score") is not None]
        retentions = [
            float(row["retention_3s"]) for row in rows_in_cluster if row.get("retention_3s") is not None
        ]
        shares = [float(row["share_rate"]) for row in rows_in_cluster if row.get("share_rate") is not None]
        saves = [float(row["save_rate"]) for row in rows_in_cluster if row.get("save_rate") is not None]
        top_tokens = [token for token, _ in cluster["token_counter"].most_common(4)]
        examples = [
            _trim_text(row.get("hook_text") or row.get("id") or "", limit=90) for row in rows_in_cluster[:3]
        ]

        rendered_clusters.append(
            {
                "label": ", ".join(top_tokens) if top_tokens else "mixed hooks",
                "size": len(rows_in_cluster),
                "avg_score": _mean(scores),
                "median_retention_3s": _median(retentions),
                "avg_share_rate": _mean(shares),
                "avg_save_rate": _mean(saves),
                "examples": examples,
            }
        )

    rendered_clusters.sort(
        key=lambda item: (
            -item["size"],
            -1e9 if item["avg_score"] is None else -float(item["avg_score"]),
        )
    )
    return rendered_clusters


def _sanitize_rows_for_prompt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "hook_text": row.get("hook_text"),
            "platform": row.get("platform"),
            "score": row.get("score"),
            "verdict": row.get("verdict"),
            "hook_type": row.get("hook_type"),
            "video_duration_sec": row.get("video_duration_sec"),
            "views": row.get("views"),
            "likes": row.get("likes"),
            "comments": row.get("comments"),
            "retention_3s": row.get("retention_3s"),
            "completion_rate": row.get("completion_rate"),
            "share_rate": row.get("share_rate"),
            "save_rate": row.get("save_rate"),
            "comment_rate": row.get("comment_rate"),
            "aggregated_er": row.get("aggregated_er"),
        }
        for row in rows
    ]


def _build_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _hero_video_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
        views_value = _safe_float(row.get("views"))
        score_value = _safe_float(row.get("score"))
        return (
            1 if views_value is not None else 0,
            views_value if views_value is not None else -1e12,
            score_value if score_value is not None else -1e12,
        )

    def _resolve_duration_group(row: dict[str, Any]) -> str | None:
        duration_sec = _safe_float(row.get("video_duration_sec"))
        if duration_sec is not None:
            return "short" if duration_sec < 30 else "long"

        for flag_key, label in (("is_short", "short"), ("is_long", "long")):
            flag_value = row.get(flag_key)
            if isinstance(flag_value, bool) and flag_value:
                return label
            if isinstance(flag_value, str) and flag_value.strip().lower() in {"1", "true", "yes"}:
                return label

        duration_hint = str(
            row.get("duration_bucket")
            or row.get("duration_type")
            or row.get("duration_category")
            or ""
        ).strip().lower()
        if "short" in duration_hint:
            return "short"
        if "long" in duration_hint:
            return "long"

        hook_type_hint = str(row.get("hook_type") or "").strip().lower()
        if hook_type_hint in {"short", "long"}:
            return hook_type_hint
        return None

    total_videos = len(rows)
    scores = [float(row["score"]) for row in rows if row.get("score") is not None]
    retentions = [float(row["retention_3s"]) for row in rows if row.get("retention_3s") is not None]
    shares = [float(row["share_rate"]) for row in rows if row.get("share_rate") is not None]
    saves = [float(row["save_rate"]) for row in rows if row.get("save_rate") is not None]
    completions = [float(row["completion_rate"]) for row in rows if row.get("completion_rate") is not None]
    views = [float(row["views"]) for row in rows if row.get("views") is not None]

    hook_type_stats = _aggregate_group_metrics(rows, "hook_type")
    platform_stats = _aggregate_group_metrics(rows, "platform")
    verdict_stats = _aggregate_group_metrics(rows, "verdict")
    hook_clusters = _build_hook_clusters(rows)

    strong_hook_types = [
        item
        for item in hook_type_stats
        if item["count"] >= 2 and item["avg_score"] is not None and item["avg_score"] >= 7.0
    ]
    weak_hook_types = [
        item
        for item in hook_type_stats
        if item["count"] >= 2 and item["avg_score"] is not None and item["avg_score"] <= 5.0
    ]

    hero_video_row = max(rows, key=_hero_video_sort_key, default=None)
    hero_video = None
    if hero_video_row is not None:
        hero_video = {
            key: value for key, value in hero_video_row.items() if not str(key).startswith("_")
        }

    top_hooks_ranked = sorted(
        [row for row in rows if _safe_float(row.get("retention_3s")) is not None],
        key=lambda row: float(_safe_float(row.get("retention_3s")) or -1e12),
        reverse=True,
    )
    top_hooks_data = [
        {
            "hook_text": row.get("hook_text"),
            "retention_3s": _safe_float(row.get("retention_3s")),
        }
        for row in top_hooks_ranked[:3]
    ]
    hero_score = _safe_float((hero_video or {}).get("score"))
    hero_retention = _safe_float((hero_video or {}).get("retention_3s"))
    overall_score_median = _median(scores)
    overall_retention_median = _median(retentions)
    baseline_snapshot = {
        "overall_median_score": overall_score_median,
        "overall_median_retention_3s": overall_retention_median,
        "hero_score_delta_pct": _percent_delta(hero_score, overall_score_median),
        "hero_retention_delta_pct": _percent_delta(hero_retention, overall_retention_median),
    }

    duration_groups: dict[str, list[dict[str, Any]]] = {"short": [], "long": []}
    for row in rows:
        duration_group = _resolve_duration_group(row)
        if duration_group in duration_groups:
            duration_groups[duration_group].append(row)

    short_vs_long = {}
    for duration_group, duration_rows in duration_groups.items():
        duration_scores = [
            float(row["score"]) for row in duration_rows if row.get("score") is not None
        ]
        duration_retentions = [
            float(row["retention_3s"]) for row in duration_rows if row.get("retention_3s") is not None
        ]
        avg_score = _mean(duration_scores)
        short_vs_long[duration_group] = {
            "count": len(duration_rows),
            "avg_score": avg_score,
            "median_retention_3s": _median(duration_retentions),
            "avg_score_delta_vs_overview_pct": _percent_delta(avg_score, _mean(scores)),
        }

    top_examples = rows[:5]
    low_examples = sorted(
        [row for row in rows if row.get("score") is not None],
        key=lambda row: float(row["score"]),
    )[:5]

    return {
        "overview": {
            "total_videos": total_videos,
            "avg_score": _mean(scores),
            "median_retention_3s": _median(retentions),
            "avg_share_rate": _mean(shares),
            "avg_save_rate": _mean(saves),
            "avg_completion_rate": _mean(completions),
            "avg_views": _mean(views),
        },
        "group_metrics": {
            "hook_type": hook_type_stats,
            "platform": platform_stats,
            "verdict": verdict_stats,
        },
        "patterns": {
            "strong_hook_types": strong_hook_types,
            "weak_hook_types": weak_hook_types,
        },
        "hero_video": hero_video,
        "top_hooks_data": top_hooks_data,
        "short_vs_long": short_vs_long,
        "baseline": baseline_snapshot,
        "confidence": _compute_confidence_snapshot(rows),
        "hook_clusters": hook_clusters[:8],
        "top_examples": _sanitize_rows_for_prompt(top_examples),
        "low_examples": _sanitize_rows_for_prompt(low_examples),
        "compact_videos": _sanitize_rows_for_prompt(rows),
    }


def _build_summary_prompt(evidence: dict[str, Any]) -> str:
    """
    Build an evidence-first prompt with deterministic facts and thresholds.
    """
    benchmark_snapshot = _build_benchmark_snapshot()
    benchmark_json = json.dumps(benchmark_snapshot, ensure_ascii=False, indent=2)
    overview_json = json.dumps(evidence.get("overview", {}), ensure_ascii=False, indent=2)
    group_metrics_json = json.dumps(evidence.get("group_metrics", {}), ensure_ascii=False, indent=2)
    patterns_json = json.dumps(evidence.get("patterns", {}), ensure_ascii=False, indent=2)
    hero_video_json = json.dumps(evidence.get("hero_video", {}), ensure_ascii=False, indent=2)
    top_hooks_data_json = json.dumps(evidence.get("top_hooks_data", []), ensure_ascii=False, indent=2)
    short_vs_long_json = json.dumps(evidence.get("short_vs_long", {}), ensure_ascii=False, indent=2)
    baseline_json = json.dumps(evidence.get("baseline", {}), ensure_ascii=False, indent=2)
    confidence_json = json.dumps(evidence.get("confidence", {}), ensure_ascii=False, indent=2)
    clusters_json = json.dumps(evidence.get("hook_clusters", []), ensure_ascii=False, indent=2)
    top_examples_json = json.dumps(evidence.get("top_examples", []), ensure_ascii=False, indent=2)
    low_examples_json = json.dumps(evidence.get("low_examples", []), ensure_ascii=False, indent=2)
    compact_videos_json = json.dumps(evidence.get("compact_videos", []), ensure_ascii=False, indent=2)

    return f"""Ты — AI-креатор-копайлот. Твоя задача: дать краткую дневную сводку
на основе данных ниже. Пиши на русском, живым языком — как ассистент,
который рассказывает автору, что произошло за день.

НЕ дублируй сырые данные (Views=..., Likes=..., count=...). Вместо этого
интерпретируй цифры и дай выводы. Используй числа для подкрепления мыслей,
но не превращай текст в таблицу.

--- ДАННЫЕ ДЛЯ АНАЛИЗА ---

BENCHMARK_SNAPSHOT:
{benchmark_json}

OVERVIEW:
{overview_json}

GROUP METRICS:
{group_metrics_json}

PATTERNS:
{patterns_json}

HERO VIDEO:
{hero_video_json}

TOP HOOKS:
{top_hooks_data_json}

SHORT VS LONG:
{short_vs_long_json}

BASELINE:
{baseline_json}

CONFIDENCE:
{confidence_json}

HOOK CLUSTERS:
{clusters_json}

TOP EXAMPLES:
{top_examples_json}

LOW EXAMPLES:
{low_examples_json}

ALL DAY VIDEOS:
{compact_videos_json}

--- ИНСТРУКЦИИ ПО ВЫХОДУ ---

Верни строго JSON-объект (без markdown) по схеме:
{{
  "overview_text": "<2-3 предложения>",
  "top_hooks_list": ["<хук + краткий комментарий>"],
  "patterns_analysis": "<2-3 предложения>",
  "action_items": ["<совет на простом языке>"]
}}

Как заполнять каждое поле:

1. overview_text — Кратко расскажи, что произошло за день: сколько видео
   проанализировано, какое видео стало лучшим и чем оно выделяется (просмотры,
   скор, тема хука). Если данных много, упомяни это как плюс для надёжности
   выводов. 2-3 предложения, живым языком.

2. top_hooks_list — Перечисли до 3 лучших хуков. Для каждого напиши сам текст
   хука и кратко поясни, почему он сильный (тема, retention, что зацепило).
   Пример: "Estratégia de 3 etapas... — отличный retention 88%, тема отношений
   всегда цепляет".

3. patterns_analysis — Сделай вывод: что сработало и почему. Не пересказывай
   цифры — интерпретируй их. SHORT VS LONG — это про тип хука (короткий,
   средний, длинный), а не про длину видео. Примеры выводов: "Короткие хуки
   дали заметно лучший retention, чем длинные" или "Тема отношений стабильно
   держит аудиторию на 3й секунде". 1-3 предложения с инсайтами.

4. action_items — Дай 1-3 конкретных, приземлённых next steps. Что именно
   делать: масштабировать лучшие хуки, добавить CTA для увеличения share/comment
   rate, переснять слабые первые 3 секунды и т.д. Без абстракций — конкретные
   действия. Пример: "Масштабируй хук про отношения — сними 2-3 вариации",
   "Добавь CTA в конец роликов, чтобы поднять share и comment rate".

Ограничения:
- Никакого markdown, только JSON.
- Не выдумывай метрики — используй только предоставленные данные.
- Если данных мало, честно скажи об этом.
- Пиши кратко и по делу, без воды.
"""


def _build_benchmark_snapshot() -> dict[str, Any]:
    """
    Build compact benchmark snapshot directly from src/ai/benchmarks.py,
    so day summary always uses the same threshold source as core analysis.
    """
    video = VIDEO_BENCHMARKS_CONTEXT
    carousel = CAROUSEL_BENCHMARKS_CONTEXT

    return {
        "video": {
            "hook_3s": (video.get("tier_1_gatekeeper_retention") or {}).get("hook_3s", {}),
            "completion_rate_by_duration": (
                (video.get("tier_1_gatekeeper_retention") or {}).get("completion_rate_by_duration", {})
            ),
            "avg_watch_time_percentage": (
                (video.get("tier_1_gatekeeper_retention") or {}).get("avg_watch_time_percentage", {})
            ),
            "tier_2_growth_engine_engagement": video.get("tier_2_growth_engine_engagement", {}),
            "expert_heuristics_logic": video.get("expert_heuristics_logic", []),
            "automated_decision_tree": video.get("automated_decision_tree", {}),
        },
        "carousel": {
            "first_slide_hook": (
                (
                    (carousel.get("tier_1_gatekeeper_retention") or {}).get("first_slide_hook", {})
                ).get("thresholds", {})
            ),
            "swipe_through_rate": (
                (
                    (carousel.get("tier_1_gatekeeper_retention") or {}).get("swipe_through_rate", {})
                ).get("thresholds_by_slide_count", {})
            ),
            "save_rate": (
                (
                    (carousel.get("tier_2_growth_engine_engagement") or {}).get("save_rate", {})
                ).get("thresholds", {})
            ),
            "automated_decision_tree": carousel.get("automated_decision_tree", {}),
        },
    }


def _extract_message_text(result: dict[str, Any]) -> str:
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
            elif isinstance(part, str):
                chunks.append(part)
        return "\n".join(chunks).strip()
    return str(content)


def _request_openrouter(
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_sec: float,
    max_retries: int,
) -> dict[str, Any] | None:
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
                body = (response.text or "").lower()
                if "response_format" in body or "json_schema" in body:
                    current_payload.pop("response_format", None)
                    logger.warning("Provider rejected structured output for day summary; fallback plain mode.")
                    continue

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "OpenRouter day summary request failed (attempt %s/%s): %s",
                attempt + 1,
                max_retries,
                exc,
            )
            if attempt == max_retries - 1:
                return None
            time.sleep(2**attempt)
    return None


def _build_payload(
    prompt: str,
    model_name: str,
    max_tokens: int,
    temperature: float,
    structured_output: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — AI-креатор-копайлот, дружественный ассистент для автора коротких видео. "
                    "Пиши на русском языке, в естественном разговорном тоне — как будто кратко "
                    "рассказываешь коллеге, что произошло за день. "
                    "НЕ выводи сырые данные в формате key=value. Интерпретируй цифры и давай "
                    "краткие выводы на 2-3 предложения в каждом разделе. "
                    "Используй только предоставленные факты — не выдумывай метрики. "
                    "Возвращаешь строгий JSON без markdown."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    payload["reasoning"] = {"effort": "medium"}

    if structured_output:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "day_summary_payload",
                "strict": True,
                "schema": _SUMMARY_SCHEMA,
            },
        }
    return payload


def _call_openrouter_for_summary(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.7,
    structured_output: bool = True,
) -> str | None:
    """
    Call OpenRouter for day summary generation.
    Returns raw model text (expected JSON).
    """
    api_key = getattr(config, "OPENROUTER_API_KEY", "")
    if not api_key:
        logger.error("OPENROUTER_API_KEY not configured")
        return None

    # Strict model policy for day summary.
    model_name = DEFAULT_MODEL
    timeout_sec = float(getattr(config, "OPENROUTER_TIMEOUT_SEC", 30.0))
    max_retries = int(getattr(config, "OPENROUTER_MAX_RETRIES", 2))
    use_structured = bool(
        getattr(config, "OPENROUTER_USE_STRUCTURED_OUTPUT", structured_output)
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-stats-bot",
        "X-Title": "Video Stats Bot - Evidence Day Summary",
    }

    payload = _build_payload(
        prompt=prompt,
        model_name=model_name,
        max_tokens=max_tokens,
        temperature=temperature,
        structured_output=use_structured,
    )
    logger.info(
        "Day summary AI call: model=%s reasoning_effort=medium",
        model_name,
    )
    result = _request_openrouter(payload, headers, timeout_sec, max_retries)
    if not result:
        return None
    text = _extract_message_text(result)
    return text or None


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    quote = ""
    for idx in range(start, len(text)):
        ch = text[idx]
        if escaped:
            escaped = False
            continue
        if in_string and ch == "\\":
            escaped = True
            continue
        if in_string:
            if ch == quote:
                in_string = False
            continue
        if ch in {"'", '"'}:
            in_string = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def _parse_summary_payload(response_text: str) -> dict[str, Any] | None:
    if not response_text or not response_text.strip():
        return None
    text = response_text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    extracted = _extract_json_object(text)
    if extracted:
        try:
            parsed = json.loads(extracted)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _validate_summary_payload(payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["Payload is not an object"]
    errors: list[str] = []

    if not isinstance(payload.get("overview_text"), str) or not payload["overview_text"].strip():
        errors.append("overview_text should be non-empty string")

    top_hooks_list = payload.get("top_hooks_list")
    if not isinstance(top_hooks_list, list):
        errors.append("top_hooks_list must be a list")
    else:
        for item in top_hooks_list:
            if not isinstance(item, str) or not item.strip():
                errors.append("top_hooks_list contains empty item")
                break

    if not isinstance(payload.get("patterns_analysis"), str) or not payload["patterns_analysis"].strip():
        errors.append("patterns_analysis must be non-empty string")

    action_items = payload.get("action_items")
    if not isinstance(action_items, list):
        errors.append("action_items must be a list")
    else:
        for item in action_items:
            if not isinstance(item, str) or not item.strip():
                errors.append("action_items contains empty item")
                break

    non_empty_sections = sum(
        [
            1 if str(payload.get("overview_text", "")).strip() else 0,
            1 if str(payload.get("patterns_analysis", "")).strip() else 0,
            1 if isinstance(payload.get("top_hooks_list"), list) and payload.get("top_hooks_list") else 0,
            1 if isinstance(payload.get("action_items"), list) and payload.get("action_items") else 0,
        ]
    )
    if non_empty_sections == 0:
        errors.append("payload contains no meaningful content")

    return len(errors) == 0, errors


def _render_summary_payload(payload: dict[str, Any]) -> str:
    # Keep only our own trusted HTML tags (<b> section titles).
    # All dynamic content from AI/fallback is escaped to avoid Telegram HTML parse errors.
    overview_line = html.escape(str(payload.get("overview_text", "")).strip(), quote=False) or "Нет данных"

    top_hooks_lines: list[str] = []
    top_hooks_raw = payload.get("top_hooks_list")
    if isinstance(top_hooks_raw, list):
        for index, item in enumerate(top_hooks_raw, start=1):
            hook_text = html.escape(str(item).strip(), quote=False)
            if hook_text:
                top_hooks_lines.append(f"{index}. {hook_text}")

    patterns_line = html.escape(str(payload.get("patterns_analysis", "")).strip(), quote=False) or "Нет данных"

    action_items_lines: list[str] = []
    action_items_raw = payload.get("action_items")
    if isinstance(action_items_raw, list):
        for item in action_items_raw:
            action_text = html.escape(str(item).strip(), quote=False)
            if action_text:
                action_items_lines.append(f"• {action_text}")

    sections = [
        "<b>🤖 AI-сводка дня</b>",
        "",
        "<b>📊 Общая картина:</b>",
        overview_line,
        "",
        "<b>🎣 Лучшие хуки:</b>",
        "\n".join(top_hooks_lines) if top_hooks_lines else "Нет данных",
        "",
        "<b>🏆 Что сработало:</b>",
        patterns_line,
        "",
        "<b>💡 Next steps:</b>",
        "\n".join(action_items_lines) if action_items_lines else "Нет данных",
    ]
    return "\n".join(sections).strip()


def _format_human_number(value: int | float | None) -> str:
    """Format a number for human-readable display (e.g. 261227 -> '261К')."""
    if value is None:
        return "н/д"
    num = float(value)
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}М"
    if num >= 1_000:
        return f"{num / 1_000:.0f}К"
    return str(int(num))


def _fallback_summary(evidence: dict[str, Any]) -> str:
    """
    Deterministic fallback summary when LLM response is invalid/unavailable.
    Produces human-readable text instead of raw data dumps.
    """
    hero_video = evidence.get("hero_video") or {}
    top_hooks_data = evidence.get("top_hooks_data") or []
    short_vs_long = evidence.get("short_vs_long") or {}
    overview = evidence.get("overview") or {}

    views = _safe_int(hero_video.get("views"))
    likes = _safe_int(hero_video.get("likes"))
    score = _safe_float(hero_video.get("score"))
    total_videos = overview.get("total_videos") or 0

    overview_parts: list[str] = []
    if total_videos:
        overview_parts.append(f"За день проанализировано {total_videos} видео.")
    if views is not None and score is not None:
        overview_parts.append(
            f"Лучшее видео набрало {_format_human_number(views)} просмотров"
            f"{' и ' + _format_human_number(likes) + ' лайков' if likes else ''}"
            f" (скор {score:.1f}/10)."
        )
    elif views is not None:
        overview_parts.append(
            f"Лучшее видео набрало {_format_human_number(views)} просмотров."
        )
    overview_line = " ".join(overview_parts) if overview_parts else "Недостаточно данных для общей картины."

    top_hooks_list: list[str] = []
    for hook in top_hooks_data[:3]:
        hook_text = _trim_text(hook.get("hook_text") or "", limit=90)
        if not hook_text:
            continue
        retention = _safe_float(hook.get("retention_3s"))
        if retention is not None:
            top_hooks_list.append(f"{hook_text} — retention {retention:.0f}%")
        else:
            top_hooks_list.append(hook_text)
    if not top_hooks_list:
        top_hooks_list.append("Недостаточно данных по хукам.")

    short_info = short_vs_long.get("short") or {}
    long_info = short_vs_long.get("long") or {}
    baseline = evidence.get("baseline") or {}
    baseline_score = _safe_float(baseline.get("overall_median_score"))
    baseline_retention = _safe_float(baseline.get("overall_median_retention_3s"))

    patterns_parts: list[str] = []
    short_count = short_info.get("count", 0)
    long_count = long_info.get("count", 0)
    short_avg = _safe_float(short_info.get("avg_score"))
    long_avg = _safe_float(long_info.get("avg_score"))
    short_retention = _safe_float(short_info.get("median_retention_3s"))
    long_retention = _safe_float(long_info.get("median_retention_3s"))

    if short_count and long_count and short_avg is not None and long_avg is not None:
        if short_avg > long_avg:
            patterns_parts.append(
                f"Короткие хуки показали себя лучше длинных (скор {short_avg:.1f} vs {long_avg:.1f})."
            )
        elif long_avg > short_avg:
            patterns_parts.append(
                f"Длинные хуки обошли короткие по скору ({long_avg:.1f} vs {short_avg:.1f})."
            )
        else:
            patterns_parts.append("Короткие и длинные хуки показали примерно одинаковый результат.")
    elif short_count and not long_count:
        patterns_parts.append("Сегодня все хуки — короткие.")
        if short_avg is not None:
            patterns_parts.append(f"Средний скор — {short_avg:.1f}.")
    elif long_count and not short_count:
        patterns_parts.append("Сегодня все хуки — длинные.")

    if short_retention is not None and long_retention is not None:
        if short_retention > long_retention + 5:
            patterns_parts.append(
                f"Retention у коротких хуков заметно выше ({short_retention:.0f}% vs {long_retention:.0f}%)."
            )
        elif long_retention > short_retention + 5:
            patterns_parts.append(
                f"Длинные хуки дали лучший retention ({long_retention:.0f}% vs {short_retention:.0f}%)."
            )

    if not patterns_parts:
        if baseline_retention is not None:
            patterns_parts.append(f"Медиана retention — {baseline_retention:.0f}%.")
        else:
            patterns_parts.append("Недостаточно данных для выводов.")

    patterns_line = " ".join(patterns_parts)

    hero_hook = _trim_text(hero_video.get("hook_text") or "", limit=60)
    action_items: list[str] = []
    if hero_hook:
        action_items.append(
            f"Масштабируй лучший хук — сними 2-3 вариации на тему «{hero_hook}»."
        )
    else:
        action_items.append("Сними 2-3 вариации лучшего хука дня.")
    action_items.append(
        "Добавь CTA в конец роликов, чтобы поднять share и comment rate."
    )

    payload = {
        "overview_text": overview_line,
        "top_hooks_list": top_hooks_list,
        "patterns_analysis": patterns_line,
        "action_items": action_items,
    }
    return _render_summary_payload(payload)


def _parse_summary_response(response_text: str) -> str | None:
    """
    Parse raw model response and return rendered summary if valid.
    """
    payload = _sanitize_summary_payload(_parse_summary_payload(response_text))
    is_valid, _ = _validate_summary_payload(payload)
    if not is_valid or payload is None:
        return None
    return _render_summary_payload(payload)


def _build_cache_key(
    compact_rows: list[dict[str, Any]],
    model_name: str,
    max_tokens: int,
    temperature: float,
) -> str:
    cache_rows = _sanitize_rows_for_prompt(compact_rows)
    fingerprint = hashlib.sha256(
        json.dumps(cache_rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return f"day_summary_v3_{len(compact_rows)}_{fingerprint}_{model_name}_{max_tokens}_{temperature}"


def _cleanup_cache(cache_ttl_sec: int) -> None:
    now = time.time()
    stale_keys = [
        key for key, (_, ts) in _cache.items() if now - ts > max(60, cache_ttl_sec * 2)
    ]
    for key in stale_keys:
        _cache.pop(key, None)


def _aggregate_videos_data(videos: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Backward-compatible helper kept for tests/internals.
    """
    compact = _build_compact_dataset(videos)
    evidence = _build_evidence(compact)
    return {
        "total_videos": evidence.get("overview", {}).get("total_videos", 0),
        "compact_videos": compact,
        "evidence": evidence,
    }


async def generate_day_summary(
    videos: list[dict[str, Any]],
    cache_ttl_sec: int = 3600,
    min_videos: int = 3,
) -> str | None:
    """
    Generate evidence-driven AI day summary from Supabase rows.
    """
    if not bool(getattr(config, "ENABLE_DAY_SUMMARY", True)):
        logger.debug("Day summary disabled in config")
        return None

    compact_rows = _build_compact_dataset(videos)
    if len(compact_rows) < min_videos:
        logger.info(
            "Not enough videos for summary: %d (min: %d)",
            len(compact_rows),
            min_videos,
        )
        return None

    max_tokens = int(getattr(config, "DAY_SUMMARY_MAX_TOKENS", 1500))
    temperature = float(getattr(config, "DAY_SUMMARY_TEMPERATURE", 0.7))
    model_name = DEFAULT_MODEL

    cache_key = _build_cache_key(compact_rows, model_name, max_tokens, temperature)
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[1] < cache_ttl_sec):
        logger.debug("Returning cached day summary")
        return cached[0]

    evidence = _build_evidence(compact_rows)
    prompt = _build_summary_prompt(evidence)

    logger.info("Calling OpenRouter for evidence day summary")
    raw_response = _call_openrouter_for_summary(
        prompt=prompt,
        model=model_name,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    summary = None
    if raw_response:
        payload = _sanitize_summary_payload(_parse_summary_payload(raw_response))
        valid, errors = _validate_summary_payload(payload)
        if valid and payload is not None:
            summary = _render_summary_payload(_enhance_summary_payload(payload, evidence))
        else:
            logger.warning("Invalid summary payload, fallback to deterministic summary: %s", "; ".join(errors))

    if not summary:
        summary = _fallback_summary(evidence)

    _cache[cache_key] = (summary, time.time())
    _cleanup_cache(cache_ttl_sec)
    return summary
