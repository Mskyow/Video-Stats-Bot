"""
Точка входа: запуск бота aiogram 3.x (long polling или webhook).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties

from src import config
from src.bot.handlers import image_router, start_router, stats_router
from src.bot.middlewares import AuthMiddleware
from src.db.supabase_client import get_client

logger = logging.getLogger(__name__)


def _setup_dispatch(dp: Dispatcher, bot: Bot) -> None:
    """Регистрирует роутеры и middleware."""
    root = Router(name="root")
    supabase = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    root.message.middleware(AuthMiddleware(supabase, bot))
    root.include_routers(start_router, image_router, stats_router)
    dp.include_router(root)


async def main() -> None:
    config.setup_logging()
    logger.info("Starting Video Stats Bot (aiogram 3.x)")

    bot = Bot(
        token=config.TG_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    _setup_dispatch(dp, bot)

    if config.WEBHOOK_URL:
        # Webhook: нужен запущенный HTTP-сервер и заданный WEBHOOK_URL
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        from aiohttp import web

        app = web.Application()
        webhook_request = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_request.register(app, path=config.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        await bot.set_webhook(config.WEBHOOK_URL.rstrip("/") + config.WEBHOOK_PATH)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        logger.info("Webhook server listening on 0.0.0.0:8080")
        await asyncio.Event().wait()
    else:
        # Scheduler for daily reports
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from datetime import datetime, timedelta, timezone as dt_timezone
        from src.db.repositories.videos import get_videos_by_date_range

        scheduler = AsyncIOScheduler()
        
        async def send_daily_report_job(bot_instance: Bot):
            """
            Sends daily report to REPORT_CHAT_ID.
            Reuses logic similar to /day_stats but for the specific chat.
            """
            chat_id = config.REPORT_CHAT_ID
            if not chat_id:
                logger.warning("REPORT_CHAT_ID not set, skipping daily report.")
                return

            logger.info("Sending daily report to %s", chat_id)
            
            # Logic similar to /day_stats
            supabase = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            
            now_utc = datetime.now(dt_timezone.utc)
            minsk_offset = timedelta(hours=3)
            now_minsk = now_utc + minsk_offset

            yesterday = now_utc - timedelta(hours=24)
            start_date = yesterday.isoformat()
            end_date = now_utc.isoformat()
            
            videos = get_videos_by_date_range(supabase, start_date, end_date)
            
            if not videos:
                try:
                     await bot_instance.send_message(chat_id, "📊 Daily Report: No videos analyzed in the last 24 hours.")
                except Exception as e:
                     logger.warning(f"Failed to send empty report: {e}")
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

            lines = [f"📊 Daily Report for {now_minsk.strftime('%d.%m')} (Minsk Time)"]

            for plat, v_list in grouped.items():
                lines.append(f"\n📱 <b>{plat}</b>")
                for v in v_list:
                    score = int(v.get("score") or 0)
                    verdict = v.get("verdict") or ""
                    title = v.get("title") or "No Title"
                    metrics = v.get("metrics") or {}
                    hook_type = metrics.get("hook_type") or "Unknown"
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
            if len(report_text) > 4000:
                report_text = report_text[:4000] + "\n... (truncated)"

            try:
                await bot_instance.send_message(chat_id, report_text)
                logger.info("Daily report sent successfully.")
            except Exception as e:
                logger.exception("Failed to send daily report: %s", e)

        # Schedule job at 12:00 UTC (15:00 Minsk/Moscow time)
        scheduler.add_job(
            send_daily_report_job,
            CronTrigger(hour=12, minute=0, timezone="UTC"),
            kwargs={"bot_instance": bot}
        )
        scheduler.start()
        logger.info("Scheduler started. Daily report at 12:00 UTC (15:00 Minsk).")

        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
