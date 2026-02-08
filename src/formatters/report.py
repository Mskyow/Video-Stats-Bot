"""
Форматирование детального отчёта по метрикам видео для Telegram.

Вывод включает:
- Платформу и вердикт
- Основные метрики
- Tier 1 анализ (Hook, Completion, Watch Time)
- Tier 2 анализ (Engagement Rates)
- Expert Heuristics (если сработали)
- Рекомендации
"""
from __future__ import annotations

from typing import Any


def _fmt_number(value: Any) -> str:
    """Форматирует число с разделителем тысяч."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            value = int(value)
        return f"{value:,}".replace(",", " ")
    return str(value)


def _fmt_pct(value: Any) -> str:
    """Форматирует процент."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.1f}%"
    return str(value)


def _rating_emoji(rating: str | None) -> str:
    """Эмодзи для рейтинга."""
    if not rating:
        return "⚪"
    rating = rating.upper()
    if rating in ("FAIL", "LOW"):
        return "🔴"
    if rating in ("BORDERLINE",):
        return "🟠"
    if rating in ("OK", "SURVIVAL"):
        return "🟡"
    if rating in ("GOOD", "HIGH_VALUE"):
        return "🟢"
    if rating in ("SCALE", "VIRAL", "EXCELLENT", "GREAT", "HIDDEN_GEM"):
        return "💚"
    return "⚪"


def _get_benchmark_label(metric_type: str, value: float | None) -> str:
    """Возвращает оценку уровня метрики (Плохо/Норм/Хорошо)."""
    if value is None:
        return ""

    benchmarks = {
        "hook": [(55, "Плохо"), (70, "Норм"), (100, "Хорошо")],
        "completion": [(40, "Плохо"), (70, "Норм"), (100, "Хорошо")],
        "watch_time": [(30, "Плохо"), (50, "Норм"), (100, "Хорошо")],
        "er": [(5, "Плохо"), (10, "Норм"), (100, "Хорошо")],
        "share_rate": [(0.5, "Плохо"), (1.5, "Норм"), (100, "Хорошо")],
        "save_rate": [(1, "Плохо"), (3, "Норм"), (100, "Хорошо")],
        "comment_rate": [(0.3, "Плохо"), (1, "Норм"), (100, "Хорошо")],
    }

    for threshold, label in benchmarks.get(metric_type, []):
        if value < threshold:
            return label
    return "Хорошо"


def format_report(data: dict[str, Any]) -> str:
    """
    Формирует детальный отчёт из результата AI.
    Ограничивает длину для Telegram (до 4096 символов).
    """
    platform = (data.get("platform") or "unknown").upper().replace("_", " ")
    verdict = data.get("verdict") or "—"
    score = data.get("score")
    analysis = data.get("analysis") or "—"
    metrics = data.get("metrics") or {}
    tier_1 = data.get("tier_1_analysis") or {}
    tier_2 = data.get("tier_2_analysis") or {}
    heuristics = data.get("expert_heuristics") or []
    recommendations = data.get("recommendations") or []
    duration = data.get("video_duration_sec")
    age_hours = metrics.get("age_hours")

    lines: list[str] = []

    # Форматирование возраста видео
    if age_hours is not None:
        if age_hours < 1:
            age_str = "🕒 Fresh"
        else:
            age_str = f"🕒 {age_hours:.1f}ч назад"
    else:
        age_str = ""

    # Заголовок с вердиктом и оценкой
    score_str = f"{score}/100" if isinstance(score, (int, float)) else "—"
    lines.append(f"<b>{verdict}</b> | {score_str}")

    # Платформа, длительность и возраст
    header_parts = [f"📊 {platform}"]
    if duration:
        header_parts.append(f"~{duration}с")
    if age_str:
        header_parts.append(age_str)
    lines.append(" | ".join(header_parts))
    lines.append("")

    # Метрики в одной строке если возможно
    metric_parts = []
    if metrics.get("views") is not None:
        metric_parts.append(f"👁 {_fmt_number(metrics['views'])}")
    if metrics.get("likes") is not None:
        metric_parts.append(f"❤ {_fmt_number(metrics['likes'])}")
    if metrics.get("comments") is not None:
        metric_parts.append(f"💬 {_fmt_number(metrics['comments'])}")
    if metrics.get("shares") is not None:
        metric_parts.append(f"🔄 {_fmt_number(metrics['shares'])}")
    if metrics.get("saves") is not None:
        metric_parts.append(f"📌 {_fmt_number(metrics['saves'])}")
    if metric_parts:
        lines.append(" | ".join(metric_parts))
        lines.append("")

    # Tier 1: Основные метрики с бенчмарками
    if tier_1:
        lines.append("<b>Основные метрики:</b>")
        lines.append("<code>Норма: 🔴 Плохо | 🟡 Норм | 🟢 Хорошо</code>")

        hook = tier_1.get("hook_3s") or {}
        if hook:
            emoji = _rating_emoji(hook.get("rating"))
            val = _fmt_pct(hook.get("value"))
            label = _get_benchmark_label("hook", hook.get("value"))
            lines.append(f"  {emoji} Hook (3с): {val} — {label}")

        compl = tier_1.get("completion") or {}
        if compl:
            emoji = _rating_emoji(compl.get("rating"))
            val = _fmt_pct(compl.get("value"))
            label = _get_benchmark_label("completion", compl.get("value"))
            lines.append(f"  {emoji} Досмотр: {val} — {label}")

        awt = tier_1.get("avg_watch_time") or {}
        if awt:
            emoji = _rating_emoji(awt.get("rating"))
            val = _fmt_pct(awt.get("value"))
            label = _get_benchmark_label("watch_time", awt.get("value"))
            lines.append(f"  {emoji} Среднее время: {val} — {label}")

        lines.append("")

    # Tier 2: Вовлечение с бенчмарками
    if tier_2:
        lines.append("<b>Вовлечение (ER):</b>")
        lines.append("<code>Норма: 🔴 Плохо | 🟡 Норм | 🟢 Хорошо</code>")

        er = tier_2.get("aggregated_er") or {}
        if er and er.get("value") is not None:
            emoji = _rating_emoji(er.get("rating"))
            val = _fmt_pct(er.get("value"))
            label = _get_benchmark_label("er", er.get("value"))
            lines.append(f"  {emoji} Общий ER: {val} — {label}")

        for key, label_name, bench_key in [
            ("share_rate", "Репосты", "share_rate"),
            ("save_rate", "Сохранения", "save_rate"),
            ("comment_rate", "Комменты", "comment_rate"),
        ]:
            item = tier_2.get(key) or {}
            if item and item.get("value") is not None:
                emoji = _rating_emoji(item.get("rating"))
                val = _fmt_pct(item.get("value"))
                label = _get_benchmark_label(bench_key, item.get("value"))
                lines.append(f"  {emoji} {label_name}: {val} — {label}")

        lines.append("")

    # Сигналы
    if heuristics:
        lines.append("<b>Сигналы:</b>")
        for h in heuristics:
            lines.append(f"  • {h}")
        lines.append("")

    # Анализ — оборачиваем в blockquote для визуального выделения
    if analysis and analysis != "—":
        lines.append("<b>Вывод:</b>")
        lines.append(f"<blockquote>{analysis}</blockquote>")
    else:
        lines.append("<b>Вывод:</b> —")

    # Рекомендации
    if recommendations:
        lines.append("")
        lines.append("<b>Что делать:</b>")
        for rec in recommendations:
            lines.append(f"  → {rec}")

    result = "\n".join(lines)

    # Telegram limit: 4096 characters
    if len(result) > 4000:
        result = result[:3990] + "\n\n<i>…обрезано</i>"

    return result
