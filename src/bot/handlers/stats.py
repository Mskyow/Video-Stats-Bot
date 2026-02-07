"""
Handlers for statistics and reporting commands (/day_stats, /all_stats).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.videos import get_videos_by_date_range, get_global_stats

logger = logging.getLogger(__name__)

router = Router(name="stats")


@router.message(Command("day_stats"))
async def cmd_day_stats(message: Message, **kwargs) -> None:
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

    # Получаем supabase_client из kwargs (передан middleware)
    supabase_client = kwargs.get("supabase_client")
    if not supabase_client:
        logger.error("supabase_client not found in handler kwargs for /day_stats")
        await message.answer(
            "⚠️ <b>Ошибка сервера</b>\n\n"
            "Не удалось подключиться к базе данных. "
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
        return

    try:
        # Calculate date range for "today" or last 24h
        # Using last 24h for broader coverage
        # Minsk time is UTC+3
        now = datetime.utcnow()
        # Adjusting "today" concept to Minsk time if we wanted day boundaries,
        # but "last 24h" is relative and simpler.
        # If the user means "Show me stats for the day in Minsk time", we should adjust.
        # Current implementation: Last 24 hours from NOW (UTC).
        # To align with user expectation "standard time is Minsk (GMT+3)":
        # If they want "Today's stats" meaning "since 00:00 Minsk time", we should do that.
        # However, /day_stats usually implies "daily report", often last 24h rolling or previous calendar day.
        # Let's keep "last 24h" logic but label it clearly, or shift to Minsk timezone for display.
        
        # Let's shift the display date to Minsk time (+3 hours)
        minsk_offset = timedelta(hours=3)
        now_minsk = now + minsk_offset
        
        # 24h window remains 24h window regardless of timezone, but let's ensure we capture 
        # what they likely mean by "day stats" - typically the last full cycle.
        
        yesterday = now - timedelta(hours=24)
        start_date = yesterday.isoformat()
        end_date = now.isoformat()

        videos = get_videos_by_date_range(supabase_client, start_date, end_date)

        if not videos:
            await message.answer(
                "📊 <b>Статистика за последние 24 часа</b>\n\n"
                "За последние 24 часа не было проанализировано ни одного видео.\n\n"
                "Отправьте скриншоты метрик видео для анализа, чтобы они появились в отчете."
            )
            return

        # Group by platform
        grouped: dict[str, list] = {}
        for v in videos:
            plat = (v.get("platform") or "Other").capitalize()
            if "tiktok" in plat.lower():
                plat = "TikTok"
            elif "reels" in plat.lower():
                plat = "Reels"
            elif "youtube" in plat.lower():
                plat = "YouTube Shorts"
            
            grouped.setdefault(plat, []).append(v)

        # Format output with Minsk date
        lines = [f"📊 Report for {now_minsk.strftime('%d.%m')} (Minsk Time)"]

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
        
    except Exception as e:
        logger.exception("Error fetching day stats: %s", e)
        await message.answer(
            "⚠️ <b>Ошибка при получении статистики</b>\n\n"
            f"Произошла ошибка: {str(e)}\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )


@router.message(Command("all_stats"))
async def cmd_all_stats(message: Message, **kwargs) -> None:
    """
    Fetch and display global stats.
    """
    user_id = message.from_user.id
    logger.info("User %s requested /all_stats", user_id)

    # Получаем supabase_client из kwargs (передан middleware)
    supabase_client = kwargs.get("supabase_client")
    if not supabase_client:
        logger.error("supabase_client not found in handler kwargs for /all_stats")
        await message.answer(
            "⚠️ <b>Ошибка сервера</b>\n\n"
            "Не удалось подключиться к базе данных. "
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
        return

    try:
        stats = get_global_stats(supabase_client)
        if not stats:
            await message.answer(
                "📈 <b>Глобальная статистика</b>\n\n"
                "Не удалось получить статистику.\n\n"
                "Возможно, в базе данных еще нет записей о видео. "
                "Отправьте скриншоты метрик для анализа."
            )
            return

        total = stats.get("total_count", 0)
        avg = stats.get("avg_score", 0.0)
        high_watch = stats.get("high_watch_time_count", 0)
        high_retention = stats.get("high_retention_count", 0)

        if total == 0:
            await message.answer(
                "📈 <b>Глобальная статистика</b>\n\n"
                "В базе данных еще нет проанализированных видео.\n\n"
                "Отправьте скриншоты метрик видео для анализа, чтобы они появились в статистике."
            )
            return

        text = (
            "📈 <b>Глобальная статистика</b>\n\n"
            f"Всего видео проанализировано: <b>{total}</b>\n"
            f"Средний балл: <b>{avg:.1f}</b>\n"
            f"Высокое время просмотра (>60%): <b>{high_watch}</b>\n"
            f"Высокий Retention (>70%): <b>{high_retention}</b>"
        )
        await message.answer(text)
        
    except Exception as e:
        logger.exception("Error fetching global stats: %s", e)
        await message.answer(
            "⚠️ <b>Ошибка при получении статистики</b>\n\n"
            f"Произошла ошибка: {str(e)}\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
