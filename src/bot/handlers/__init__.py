# Роутеры подключаются в main.py к корневому роутеру с auth middleware
from src.bot.handlers.start import router as start_router
from src.bot.handlers.image import router as image_router
from src.bot.handlers.stats import router as stats_router
from src.bot.handlers.upload import router as upload_router
from src.bot.handlers.csv_import import router as csv_router
from src.bot.handlers.funnel_sync import router as funnel_sync_router
from src.bot.handlers.funnel_screenshots import router as funnel_screenshots_router
from src.bot.handlers.chat_info import router as chat_info_router
from src.bot.handlers.marketing_daily import router as marketing_daily_router
from src.bot.handlers.public_scrape import router as public_scrape_router
from src.bot.handlers.instagram_api import router as instagram_api_router
from src.bot.handlers.scrapecreators import router as scrapecreators_router

__all__ = (
    "start_router",
    "image_router",
    "stats_router",
    "upload_router",
    "csv_router",
    "funnel_sync_router",
    "funnel_screenshots_router",
    "chat_info_router",
    "marketing_daily_router",
    "public_scrape_router",
    "instagram_api_router",
    "scrapecreators_router",
)
