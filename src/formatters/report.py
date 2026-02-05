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

    lines: list[str] = []

    # Header: Вердикт
    lines.append(f"<b>{verdict}</b>")
    lines.append("")

    # Платформа и Score
    score_str = f"{score}/100" if isinstance(score, (int, float)) else "—"
    lines.append(f"📊 <b>Платформа:</b> {platform}")
    if duration:
        lines.append(f"⏱ <b>Длительность:</b> ~{duration}с")
    lines.append(f"📈 <b>Score:</b> {score_str}")
    lines.append("")

    # --- RAW METRICS ---
    lines.append("━━━ <b>Метрики</b> ━━━")
    if metrics.get("views") is not None:
        lines.append(f"  👁 Просмотры: {_fmt_number(metrics['views'])}")
    if metrics.get("likes") is not None:
        lines.append(f"  ❤ Лайки: {_fmt_number(metrics['likes'])}")
    if metrics.get("comments") is not None:
        lines.append(f"  💬 Комментарии: {_fmt_number(metrics['comments'])}")
    if metrics.get("shares") is not None:
        lines.append(f"  🔄 Репосты: {_fmt_number(metrics['shares'])}")
    if metrics.get("saves") is not None:
        lines.append(f"  📌 Сохранения: {_fmt_number(metrics['saves'])}")
    lines.append("")

    # --- TIER 1: GATEKEEPER ---
    if tier_1:
        lines.append("━━━ <b>Tier 1: Foundation</b> ━━━")

        hook = tier_1.get("hook_3s") or {}
        if hook:
            emoji = _rating_emoji(hook.get("rating"))
            val = _fmt_pct(hook.get("value"))
            rating = hook.get("rating", "—")
            lines.append(f"  {emoji} <b>Hook (3s):</b> {val} → {rating}")
            if hook.get("note"):
                lines.append(f"     <i>{hook['note']}</i>")

        compl = tier_1.get("completion") or {}
        if compl:
            emoji = _rating_emoji(compl.get("rating"))
            val = _fmt_pct(compl.get("value"))
            rating = compl.get("rating", "—")
            bracket = compl.get("duration_bracket", "")
            bracket_str = f" ({bracket})" if bracket else ""
            lines.append(f"  {emoji} <b>Completion:</b> {val} → {rating}{bracket_str}")
            if compl.get("note"):
                lines.append(f"     <i>{compl['note']}</i>")

        awt = tier_1.get("avg_watch_time") or {}
        if awt:
            emoji = _rating_emoji(awt.get("rating"))
            val = _fmt_pct(awt.get("value"))
            rating = awt.get("rating", "—")
            lines.append(f"  {emoji} <b>Avg Watch Time:</b> {val} → {rating}")
            if awt.get("note"):
                lines.append(f"     <i>{awt['note']}</i>")

        lines.append("")

    # --- TIER 2: GROWTH ---
    if tier_2:
        lines.append("━━━ <b>Tier 2: Growth</b> ━━━")
        vol = tier_2.get("volume_condition", "—")
        vol_label = "📈 High Volume" if vol == "high_volume" else "📉 Low Volume"
        lines.append(f"  {vol_label}")

        for key, label in [("share_rate", "Share Rate"), ("save_rate", "Save Rate"), ("comment_rate", "Comment Rate")]:
            item = tier_2.get(key) or {}
            if item and item.get("value") is not None:
                emoji = _rating_emoji(item.get("rating"))
                val = _fmt_pct(item.get("value"))
                rating = item.get("rating", "—")
                lines.append(f"  {emoji} <b>{label}:</b> {val} → {rating}")

        er = tier_2.get("aggregated_er") or {}
        if er and er.get("value") is not None:
            emoji = _rating_emoji(er.get("rating"))
            val = _fmt_pct(er.get("value"))
            rating = er.get("rating", "—")
            lines.append(f"  {emoji} <b>Aggregated ER:</b> {val} → {rating}")

        lines.append("")

    # --- Expert Heuristics ---
    if heuristics:
        lines.append("━━━ <b>Expert Signals</b> ━━━")
        for h in heuristics:
            lines.append(f"  ⚡ {h}")
        lines.append("")

    # --- Analysis ---
    lines.append("━━━ <b>Анализ</b> ━━━")
    lines.append(analysis)
    lines.append("")

    # --- Recommendations ---
    if recommendations:
        lines.append("━━━ <b>Рекомендации</b> ━━━")
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"  {i}. {rec}")

    result = "\n".join(lines)

    # Telegram limit: 4096 characters
    if len(result) > 4000:
        result = result[:3990] + "\n\n<i>…обрезано</i>"

    return result
