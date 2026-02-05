"""
Форматирование отчёта по метрикам для сообщения в Telegram.
"""
from __future__ import annotations

from typing import Any


def format_report(data: dict[str, Any]) -> str:
    """
    Формирует читаемое сообщение из результата AI (platform, metrics, score, analysis).
    """
    platform = (data.get("platform") or "unknown").upper()
    metrics = data.get("metrics") or {}
    score = data.get("score")
    analysis = data.get("analysis") or "—"

    views = metrics.get("views")
    likes = metrics.get("likes")
    comments = metrics.get("comments")
    shares = metrics.get("shares")
    saves = metrics.get("saves")

    lines = [
        f"📊 <b>Платформа:</b> {platform}",
        "",
        "<b>Метрики:</b>",
    ]
    if views is not None:
        lines.append(f"  👁 Просмотры: {views:,}".replace(",", " "))
    if likes is not None:
        lines.append(f"  ❤ Лайки: {likes:,}".replace(",", " "))
    if comments is not None:
        lines.append(f"  💬 Комментарии: {comments:,}".replace(",", " "))
    if shares is not None:
        lines.append(f"  🔄 Репосты: {shares:,}".replace(",", " "))
    if saves is not None:
        lines.append(f"  📌 Сохранения: {saves:,}".replace(",", " "))

    if score is not None:
        lines.append("")
        lines.append(f"📈 <b>Score:</b> {score:.2f}" if isinstance(score, (int, float)) else f"📈 <b>Score:</b> {score}")

    lines.append("")
    lines.append(f"💡 <b>Вывод:</b> {analysis}")

    return "\n".join(lines)
