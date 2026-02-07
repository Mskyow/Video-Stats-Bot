"""
Handlers for statistics and reporting commands (/day_stats, /all_stats).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.videos import get_videos_by_date_range, get_global_stats

logger = logging.getLogger(__name__)

router = Router(name="stats")


@router.message(Command("day_stats"))
async def cmd_day_stats(message: Message, supabase_client) -> None:
    """
    Fetch videos for the last 24h (or today).
    Group by platform.
    Format:
    📊 Report for 07.02
    📱 TikTok
    🟢 [94] "Title" (Scale)
       └ Hook: Short | 15s
    🔴 [30] "Title" (Kill)
    ...
    """
    user_id = message.from_user.id
    logger.info("User %s requested /day_stats", user_id)

    # Calculate date range for "today" or last 24h
    # Using last 24h for broader coverage or start of day?
    # Requirement: "Fetch videos for the last 24h"
    now = datetime.utcnow()
    yesterday = now - timedelta(hours=24)
    
    start_date = yesterday.isoformat()
    end_date = now.isoformat()

    videos = get_videos_by_date_range(supabase_client, start_date, end_date)

    if not videos:
        await message.answer("No videos analyzed in the last 24 hours.")
        return

    # Group by platform
    grouped: dict[str, list] = {}
    for v in videos:
        plat = (v.get("platform") or "Other").capitalize()
        # Normalize platform names
        if "tiktok" in plat.lower():
            plat = "TikTok"
        elif "reels" in plat.lower():
            plat = "Reels"
        elif "youtube" in plat.lower():
            plat = "YouTube Shorts"
        
        grouped.setdefault(plat, []).append(v)

    # Format output
    lines = [f"📊 Report for {now.strftime('%d.%m')}"]

    for plat, v_list in grouped.items():
        lines.append(f"\n📱 <b>{plat}</b>")
        for v in v_list:
            score = int(v.get("score") or 0)
            verdict = v.get("verdict") or ""
            title = v.get("title") or "No Title"
            hook_type = "Unknown"
            
            # Extract hook_type from metrics if available
            metrics = v.get("metrics") or {}
            if "hook_type" in metrics:
                 hook_type = metrics["hook_type"]
            
            duration = v.get("video_duration_sec")
            dur_str = f"{duration}s" if duration else "?"

            # Icon based on score/verdict
            icon = "⚪"
            if "KILL" in verdict.upper():
                icon = "🔴"
            elif "SCALE" in verdict.upper():
                icon = "🟢"
            elif "ITERATE" in verdict.upper():
                icon = "🟡"
            
            # Short verdict for display
            short_verdict = "Iterate"
            if "KILL" in verdict.upper():
                short_verdict = "Kill"
            elif "SCALE" in verdict.upper():
                short_verdict = "Scale"

            lines.append(f"{icon} [{score}] \"{title}\" ({short_verdict})")
            lines.append(f"   └ Hook: {hook_type} | {dur_str}")

    report_text = "\n".join(lines)
    # Telegram message limit is 4096 chars. If report is long, split it.
    # For now, assuming it fits.
    if len(report_text) > 4000:
        report_text = report_text[:4000] + "\n... (truncated)"

    await message.answer(report_text)


@router.message(Command("all_stats"))
async def cmd_all_stats(message: Message, supabase_client) -> None:
    """
    Fetch and display global stats.
    """
    user_id = message.from_user.id
    logger.info("User %s requested /all_stats", user_id)

    stats = get_global_stats(supabase_client)
    if not stats:
        await message.answer("Could not fetch global stats.")
        return

    total = stats.get("total_count", 0)
    avg = stats.get("avg_score", 0.0)
    high_watch = stats.get("high_watch_time_count", 0)
    high_retention = stats.get("high_retention_count", 0)

    text = (
        "📈 <b>Global Stats</b>\n\n"
        f"Total Videos Analyzed: <b>{total}</b>\n"
        f"Average Score: <b>{avg}</b>\n"
        f"High Watch Time (>60%): <b>{high_watch}</b>\n"
        f"High Retention (>70%): <b>{high_retention}</b>"
    )
    await message.answer(text)
