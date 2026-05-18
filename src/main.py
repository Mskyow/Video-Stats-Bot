"""
Entry point for aiogram 3.x bot (long polling or webhook).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from src import config
from src.bot.handlers import (
    chat_info_router,
    csv_router,
    funnel_sync_router,
    image_router,
    start_router,
    stats_router,
    upload_router,
)
from src.bot.middlewares.album import AlbumMiddleware
from src.bot.middlewares.auth import AuthMiddleware
from src.db.supabase_client import get_client
from src.services.sheets_service import sheets_worker

logger = logging.getLogger(__name__)


def _setup_dispatch(dp: Dispatcher, bot: Bot) -> None:
    root = Router(name="root")
    supabase = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    root.message.middleware(AuthMiddleware(supabase, bot))
    root.callback_query.middleware(AuthMiddleware(supabase, bot))
    root.message.middleware(AlbumMiddleware(latency=0.6))

    root.include_routers(
        chat_info_router,
        csv_router,
        funnel_sync_router,
        upload_router,
        start_router,
        image_router,
        stats_router,
    )
    dp.include_router(root)


async def setup_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="upload", description="Загрузить скрины роликов"),
        BotCommand(command="upload_funnel", description="Воронка по скринам"),
        BotCommand(command="import_csv", description="Импортировать CSV воронки"),
        BotCommand(command="chat_info", description="Показать chat_id и topic_id"),
        BotCommand(command="sources", description="Статус App Store / Google Play"),
        BotCommand(command="sync_funnels", description="Статус автосбора воронки"),
        BotCommand(command="done", description="Выключить режим скринов"),
        BotCommand(command="help", description="Как пользоваться ботом"),
        BotCommand(command="stats", description="Моя статистика"),
        BotCommand(command="day_stats", description="Отчёт за 24 часа"),
        BotCommand(command="all_stats", description="Общая статистика"),
        BotCommand(command="send_report", description="Отправить отчёт в чат"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Bot commands set successfully: %s", [cmd.command for cmd in commands])
    except Exception as exc:
        logger.error("Failed to set bot commands: %s", exc)


async def send_daily_report_job(bot_instance: Bot) -> None:
    from src.bot.handlers.stats import build_day_stats_report

    chat_id = config.REPORT_CHAT_ID
    if not chat_id:
        logger.warning("REPORT_CHAT_ID not set, skipping daily report.")
        return

    logger.info("Sending scheduled daily report to chat=%s topic=%s", chat_id, config.REPORT_TOPIC_ID)

    try:
        supabase_client = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        report_text, markup = await build_day_stats_report(supabase_client)

        send_kwargs: dict = {
            "chat_id": chat_id,
            "text": report_text,
            "parse_mode": "HTML",
        }
        if markup is not None:
            send_kwargs["reply_markup"] = markup
        if config.REPORT_TOPIC_ID is not None:
            send_kwargs["message_thread_id"] = config.REPORT_TOPIC_ID

        await bot_instance.send_message(**send_kwargs)
        logger.info("Scheduled daily report sent successfully.")
    except Exception as exc:
        logger.exception("Failed to send scheduled daily report: %s", exc)


def _create_report_scheduler(bot: Bot):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_daily_report_job,
        CronTrigger(
            hour=config.REPORT_HOUR,
            minute=config.REPORT_MINUTE,
            timezone=config.REPORT_TIMEZONE,
        ),
        kwargs={"bot_instance": bot},
    )
    scheduler.start()
    logger.info(
        "Scheduler started. Daily report at %02d:%02d %s.",
        config.REPORT_HOUR,
        config.REPORT_MINUTE,
        config.REPORT_TIMEZONE,
    )
    return scheduler


async def main() -> None:
    config.setup_logging()
    logger.info("Starting Video Stats Bot (aiogram 3.x)")

    bot = Bot(
        token=config.TG_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    _setup_dispatch(dp, bot)
    await setup_bot_commands(bot)

    asyncio.create_task(sheets_worker())
    logger.info("Background sheets worker started")

    _scheduler = _create_report_scheduler(bot)

    if config.WEBHOOK_URL:
        from aiohttp import web
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

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
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
