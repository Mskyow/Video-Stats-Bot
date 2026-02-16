"""
Точка входа: запуск бота aiogram 3.x (long polling или webhook).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

from src import config
from src.bot.handlers import image_router, start_router, stats_router, upload_router
from src.bot.middlewares.album import AlbumMiddleware
from src.bot.middlewares.auth import AuthMiddleware
from src.db.supabase_client import get_client
from src.services.sheets_service import sheets_worker

logger = logging.getLogger(__name__)


def _setup_dispatch(dp: Dispatcher, bot: Bot) -> None:
    """Регистрирует роутеры и middleware."""
    root = Router(name="root")
    supabase = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    # Auth middleware FIRST (adds supabase_client to context)
    root.message.middleware(AuthMiddleware(supabase, bot))
    # Callback queries also need auth
    root.callback_query.middleware(AuthMiddleware(supabase, bot))

    # Собираем альбомы
    root.message.middleware(AlbumMiddleware(latency=0.6))

    # Важно: upload_router должен быть ПЕРЕД image_router,
    # чтобы команды /upload и /done обрабатывались первыми
    root.include_routers(upload_router, start_router, image_router, stats_router)
    dp.include_router(root)


async def setup_bot_commands(bot: Bot) -> None:
    """Настройка меню команд бота."""
    commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="upload", description="Загрузить статистику видео"),
        BotCommand(command="done", description="Завершить загрузку"),
        BotCommand(command="help", description="Справка по использованию"),
        BotCommand(command="stats", description="Моя статистика анализов"),
        BotCommand(command="day_stats", description="Отчет за последние 24 часа"),
        BotCommand(command="all_stats", description="Общая статистика по всем видео"),
        BotCommand(command="send_report", description="Отправить отчёт в рабочий чат"),
    ]

    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Bot commands set successfully: %s", [cmd.command for cmd in commands])
    except Exception as e:
        logger.error("Failed to set bot commands: %s", e)


async def send_daily_report_job(bot_instance: Bot) -> None:
    """
    Ежедневная отправка полного отчёта day_stats в REPORT_CHAT_ID / REPORT_TOPIC_ID.
    Использует ту же логику, что и команда /day_stats.
    """
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
    """
    Создаёт и запускает AsyncIOScheduler с CronTrigger для ежедневного отчёта.
    Работает одинаково и при long polling, и при webhook.
    """
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
    # FSM хранилище в памяти (при перезапуске бота состояния сбросятся)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    _setup_dispatch(dp, bot)
    await setup_bot_commands(bot)

    # Запускаем воркер Google Sheets для асинхронного экспорта
    asyncio.create_task(sheets_worker())
    logger.info("Background sheets worker started")

    # Запускаем планировщик ежедневных отчётов (работает и при webhook, и при polling)
    _scheduler = _create_report_scheduler(bot)

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
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
