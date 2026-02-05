"""
Точка входа: запуск бота aiogram 3.x (long polling или webhook).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties

from src import config
from src.bot.handlers import image_router, start_router
from src.bot.middlewares import AuthMiddleware
from src.db.supabase_client import get_client

logger = logging.getLogger(__name__)


def _setup_dispatch(dp: Dispatcher) -> None:
    """Регистрирует роутеры и middleware."""
    root = Router(name="root")
    supabase = get_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    root.message.middleware(AuthMiddleware(supabase))
    root.include_routers(start_router, image_router)
    dp.include_router(root)


async def main() -> None:
    config.setup_logging()
    logger.info("Starting Video Stats Bot (aiogram 3.x)")

    bot = Bot(
        token=config.TG_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    _setup_dispatch(dp)

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
