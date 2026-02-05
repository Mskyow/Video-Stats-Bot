"""
Приём изображения (скриншот с метриками) → AI → отчёт пользователю и сохранение в БД.
"""
from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, F, Router
from aiogram.types import Message

from src.ai.gemini_service import analyze_screenshot
from src.db.repositories.videos import insert_video
from src.db.supabase_client import get_supabase
from src.formatters.report import format_report

router = Router(name="image")
logger = logging.getLogger(__name__)


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    """Скачиваем фото, отправляем в Gemini, форматируем отчёт и сохраняем в БД."""
    await message.answer("Обрабатываю скриншот…")

    # Берём самое большое разрешение
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    data = await bot.download_file(file.file_path)
    if data is None:
        await message.answer("Не удалось скачать изображение. Попробуй ещё раз.")
        return

    # download_file без destination возвращает BinaryIO (BytesIO)
    image_bytes = data.read() if hasattr(data, "read") else bytes(data)

    # Вызов Gemini в отдельном потоке
    result = await asyncio.to_thread(
        analyze_screenshot,
        image_bytes,
        mime_type="image/jpeg",
    )

    if not result:
        await message.answer(
            "Не удалось разобрать метрики по скрину. Убедись, что на изображении виден интерфейс с цифрами (просмотры, лайки и т.д.) и попробуй снова."
        )
        return

    report_text = format_report(result)

    # Сохранение в БД (если есть пользователь и Supabase)
    user_id = message.from_user.id if message.from_user else 0
    supabase = get_supabase()
    if supabase and user_id:
        await asyncio.to_thread(
            insert_video,
            supabase,
            user_id,
            result.get("platform"),
            result.get("metrics") or {},
            float(result.get("score") or 0),
            result.get("analysis"),
        )

    await message.answer(report_text)
