# Роутеры подключаются в main.py к корневому роутеру с auth middleware
from src.bot.handlers.start import router as start_router
from src.bot.handlers.image import router as image_router
from src.bot.handlers.stats import router as stats_router
from src.bot.handlers.upload import router as upload_router
from src.bot.handlers.csv_import import router as csv_router
from src.bot.handlers.funnel_sync import router as funnel_sync_router

__all__ = ("start_router", "image_router", "stats_router", "upload_router", "csv_router", "funnel_sync_router")
