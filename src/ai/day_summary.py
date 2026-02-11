"""
Evidence-driven AI day summary for /day_stats.

This module builds a compact dataset from Supabase video rows, computes
deterministic correlations and hook clusters, then asks the LLM to produce
an actionable summary constrained by facts and thresholds.
"""
from __future__ import annotations

import hashlib
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
        "overview": {"type": "string"},
        "best_hooks": {"type": "array", "items": {"type": "string"}},
        "worked_today": {"type": "array", "items": {"type": "string"}},
        "best_cases": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "overview",
        "best_hooks",
        "worked_today",
        "best_cases",
        "recommendations",
        "evidence_refs",
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


def _extract_compact_video(video: dict[str, Any]) -> dict[str, Any]:
    metrics = video.get("metrics") or {}
    hook_text = _trim_text(video.get("hook_text") or video.get("title") or "", limit=140)
    score = _safe_float(video.get("score"))
    views = _safe_int(metrics.get("views") or metrics.get("view_count"))

    row = {
        "id": str(video.get("id") or ""),
        "platform": _normalize_platform(video.get("platform")),
        "score": round(score, 3) if score is not None else None,
        "verdict": _normalize_verdict(video.get("verdict")),
        "hook_text": hook_text,
        "hook_type": _normalize_hook_type(metrics.get("hook_type")),
        "video_duration_sec": _safe_int(video.get("video_duration_sec")),
        "views": views,
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
    clusters_json = json.dumps(evidence.get("hook_clusters", []), ensure_ascii=False, indent=2)
    top_examples_json = json.dumps(evidence.get("top_examples", []), ensure_ascii=False, indent=2)
    low_examples_json = json.dumps(evidence.get("low_examples", []), ensure_ascii=False, indent=2)
    compact_videos_json = json.dumps(evidence.get("compact_videos", []), ensure_ascii=False, indent=2)

    return f"""Ты — Senior Growth Analyst для short-form контента.
Твоя задача: дать day summary ТОЛЬКО на основе фактов ниже.

ВНИМАНИЕ: Используй только benchmark-цифры из BENCHMARK_SNAPSHOT (ниже).
Не выдумывай пороги и не подменяй значения.

BENCHMARK_SNAPSHOT (из src/ai/benchmarks.py):
{benchmark_json}

EVIDENCE OVERVIEW:
{overview_json}

EVIDENCE GROUP METRICS:
{group_metrics_json}

EVIDENCE PATTERNS:
{patterns_json}

EVIDENCE HOOK CLUSTERS:
{clusters_json}

TOP EXAMPLES:
{top_examples_json}

LOW EXAMPLES:
{low_examples_json}

COMPACT DATASET (ALL DAY VIDEOS):
{compact_videos_json}

Верни строго JSON-объект по схеме (структура должна быть стабильной изо дня в день):
{{
  "overview": "1-2 предложения с цифрами",
  "best_hooks": ["какие хуки показали лучший результат и почему (с цифрами)"],
  "worked_today": ["что лучше всего сработало сегодня (с цифрами)"],
  "best_cases": ["лучшие кейсы за день (конкретные примеры и метрики)"],
  "recommendations": ["что делать сегодня: метрика -> действие", "2-3 пункта"],
  "evidence_refs": ["ссылка на конкретный факт/кластер/группу с цифрами", "еще 1-2 ссылки"]
}}

Ограничения:
- Никакого markdown, только JSON.
- Каждый блок должен ссылаться на числа из evidence.
- Не выдумывай метрики или пороги.
- Если данных мало, явно пиши "недостаточно данных".
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
                    "Ты Senior Growth Analyst. Используешь только предоставленные факты и пороги. "
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

    required_fields = (
        "overview",
        "best_hooks",
        "worked_today",
        "best_cases",
        "recommendations",
        "evidence_refs",
    )
    errors: list[str] = []
    for key in required_fields:
        if key not in payload:
            errors.append(f"Missing field: {key}")

    if errors:
        return False, errors

    if not isinstance(payload.get("overview"), str) or not payload["overview"].strip():
        errors.append("overview must be non-empty string")

    for list_key in ("best_hooks", "worked_today", "best_cases", "recommendations", "evidence_refs"):
        items = payload.get(list_key)
        if not isinstance(items, list) or not items:
            errors.append(f"{list_key} must be non-empty list")
            continue
        for item in items:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{list_key} contains empty item")
                break

    if isinstance(payload.get("recommendations"), list) and len(payload["recommendations"]) < 2:
        errors.append("recommendations must include at least 2 items")

    joined_text = " ".join(
        [str(payload.get("overview", ""))]
        + [str(x) for x in payload.get("best_hooks", [])]
        + [str(x) for x in payload.get("worked_today", [])]
        + [str(x) for x in payload.get("best_cases", [])]
        + [str(x) for x in payload.get("recommendations", [])]
    )
    if not re.search(r"\d", joined_text):
        errors.append("summary must include numeric evidence")

    return len(errors) == 0, errors


def _render_summary_payload(payload: dict[str, Any]) -> str:
    best_hooks_lines = [f"• {item.strip()}" for item in payload.get("best_hooks", [])]
    worked_today_lines = [f"• {item.strip()}" for item in payload.get("worked_today", [])]
    best_cases_lines = [f"• {item.strip()}" for item in payload.get("best_cases", [])]
    recommendations_lines = [f"• {item.strip()}" for item in payload.get("recommendations", [])]

    sections = [
        "<b>📊 Общая картина:</b>",
        payload.get("overview", "").strip(),
        "",
        "<b>🎣 Лучшие хуки:</b>",
        "\n".join(best_hooks_lines) if best_hooks_lines else "• недостаточно данных",
        "",
        "<b>✅ Что сработало лучше всего:</b>",
        "\n".join(worked_today_lines) if worked_today_lines else "• недостаточно данных",
        "",
        "<b>🏆 Лучшие кейсы за сегодня:</b>",
        "\n".join(best_cases_lines) if best_cases_lines else "• недостаточно данных",
        "",
        "<b>💡 Что делать сегодня:</b>",
        "\n".join(recommendations_lines) if recommendations_lines else "• недостаточно данных",
    ]
    return "\n".join(sections).strip()


def _fallback_summary(evidence: dict[str, Any]) -> str:
    """
    Deterministic fallback summary when LLM response is invalid/unavailable.
    """
    overview = evidence.get("overview", {})
    hook_stats = evidence.get("group_metrics", {}).get("hook_type", [])
    clusters = evidence.get("hook_clusters", [])

    total = int(overview.get("total_videos") or 0)
    avg_score = overview.get("avg_score")
    avg_retention = overview.get("median_retention_3s")
    avg_share = overview.get("avg_share_rate")

    best_group = None
    worst_group = None
    if hook_stats:
        ranked = [
            row for row in hook_stats if row.get("count", 0) >= 2 and row.get("avg_score") is not None
        ]
        ranked.sort(key=lambda row: float(row["avg_score"]), reverse=True)
        if ranked:
            best_group = ranked[0]
            worst_group = ranked[-1]

    cluster_line = "недостаточно данных по похожим хукам"
    if clusters:
        top_cluster = clusters[0]
        cluster_line = (
            f"Кластер '{top_cluster['label']}' (n={top_cluster['size']}) показывает "
            f"avg score {top_cluster.get('avg_score')} и median retention {top_cluster.get('median_retention_3s')}%."
        )

    best_hooks_lines: list[str] = []
    worked_today_lines: list[str] = []
    best_cases_lines: list[str] = []
    recommendation_lines: list[str] = []

    if best_group:
        best_hooks_lines.append(
            f"Hook type '{best_group['group']}' (n={best_group['count']}) даёт avg score "
            f"{best_group.get('avg_score')} и avg share {best_group.get('avg_share_rate')}%."
        )
    if avg_share is not None:
        worked_today_lines.append(f"Средний share rate по дню: {avg_share}% (ориентир viral: >1.5%).")
    if not best_hooks_lines:
        best_hooks_lines.append("Недостаточно данных для устойчивого вывода по лучшим хукам.")

    worked_today_lines.append(cluster_line)
    if not worked_today_lines:
        worked_today_lines.append("Недостаточно данных для устойчивого вывода по победившим паттернам.")

    if worst_group and worst_group is not best_group:
        best_cases_lines.append(
            f"Лучшая группа против слабой: '{best_group['group'] if best_group else 'n/a'}' "
            f"vs '{worst_group['group']}' по avg score."
        )
    else:
        best_cases_lines.append("Недостаточно данных для сравнительного кейса по группам.")

    recommendation_lines.append("Масштабируй форматы, где retention 3s >70% и share rate >1.5%.")
    recommendation_lines.append("Переделывай hook в группах с retention 3s <58%, не меняя всё видео целиком.")
    recommendation_lines.append("Тестируй 2-3 вариации лучших hook-кластеров и отслеживай completion rate.")

    overview_line = (
        f"За сутки {total} видео, средний score {avg_score if avg_score is not None else 'н/д'}/10, "
        f"median retention 3s {avg_retention if avg_retention is not None else 'н/д'}%."
    )

    payload = {
        "overview": overview_line,
        "best_hooks": best_hooks_lines,
        "worked_today": worked_today_lines,
        "best_cases": best_cases_lines,
        "recommendations": recommendation_lines,
        "evidence_refs": ["deterministic_fallback"],
    }
    return _render_summary_payload(payload)


def _parse_summary_response(response_text: str) -> str | None:
    """
    Parse raw model response and return rendered summary if valid.
    """
    payload = _parse_summary_payload(response_text)
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
        payload = _parse_summary_payload(raw_response)
        valid, errors = _validate_summary_payload(payload)
        if valid and payload is not None:
            summary = _render_summary_payload(payload)
        else:
            logger.warning("Invalid summary payload, fallback to deterministic summary: %s", "; ".join(errors))

    if not summary:
        summary = _fallback_summary(evidence)

    _cache[cache_key] = (summary, time.time())
    _cleanup_cache(cache_ttl_sec)
    return summary
