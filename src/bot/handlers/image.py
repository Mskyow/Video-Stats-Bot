"""
Приём изображения (скриншот с метриками) → AI анализ → отчёт пользователю → сохранение в БД.
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
    processing_msg = await message.answer("⏳ Анализирую скриншот… Это может занять несколько секунд.")

    # Берём самое большое разрешение
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    data = await bot.download_file(file.file_path)
    if data is None:
        await processing_msg.edit_text("❌ Не удалось скачать изображение. Попробуй ещё раз.")
        return

    image_bytes = data.read() if hasattr(data, "read") else bytes(data)

    # Вызов Gemini (синхронный SDK → to_thread)
    result, raw_response = await asyncio.to_thread(
        analyze_screenshot,
        image_bytes,
        mime_type="image/jpeg",
    )

    if not result:
        await processing_msg.edit_text(
            "❌ Не удалось разобрать метрики по скриншоту.\n\n"
            "Убедись, что на изображении виден интерфейс аналитики "
            "(просмотры, лайки, retention и т.д.) и попробуй снова."
        )
        return

    # Формируем отчёт
    report_text = format_report(result)

    # Сохранение в БД
    user_id = message.from_user.id if message.from_user else 0
    supabase = get_supabase()
    if supabase and user_id:
        await asyncio.to_thread(
            insert_video,
            supabase,
            user_id,
            result,
            raw_response,
        )

    # Отправляем отчёт (заменяем сообщение "анализирую")
    try:
        await processing_msg.edit_text(report_text)
    except Exception:
        # Если edit не сработал (слишком длинное), отправляем новым
        await message.answer(report_text)
