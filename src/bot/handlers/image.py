"""
Приём медиа-группы (альбома скриншотов) → Batch AI анализ → Сводный отчёт → Сохранение.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.ai.openrouter_service import analyze_screenshot
from src.bot.states import UploadMode
from src.config import GOOGLE_SHEET_ID
from src.db.repositories.users import is_user_authorized
from src.db.repositories.videos import insert_video
from src.db.supabase_client import get_supabase
from src.services.sheets_service import queue_export_to_sheet

router = Router(name="image")
logger = logging.getLogger(__name__)


@dataclass
class VideoProcessingResult:
    """Результат обработки одного видео."""

    index: int
    video_title: str | None = None
    success: bool = False
    score: int = 0
    rating_label: str = "N/A"
    error_message: str | None = None
    ai_result: dict[str, Any] | None = None
    raw_response: str | None = None
    is_duplicate: bool = False  # Если видео дубликат (данные не изменились)


@router.message(F.photo)
async def handle_photo(
    message: Message, bot: Bot, state: FSMContext, album: list[Message] | None = None
) -> None:
    """
    Обрабатывает альбом скриншотов (или одиночное фото).

    Алгоритм:
    1. Проверяем авторизацию пользователя
    2. Проверяем, активен ли режим загрузки (UploadMode.active)
    3. Сортируем сообщения альбома по ID.
    4. Скачиваем все фото параллельно.
    5. Отправляем batch-запрос в AI для анализа всех скриншотов.
    6. Сохраняем успешные результаты в БД/Google Sheets.
    7. Формируем единый сводный отчет.
    """
    # Проверяем авторизацию
    user = message.from_user
    if user:
        supabase = get_supabase()
        if not is_user_authorized(supabase, user.id):
            await message.answer(
                "🔒 <b>Доступ ограничен.</b>\n\n"
                "Для анализа видео сначала авторизуйся:\n"
                "<code>/start КОДОВОЕ_СЛОВО</code>"
            )
            return

    # Проверяем, активен ли режим загрузки
    current_state = await state.get_state()
    if current_state != UploadMode.active:
        await message.answer(
            "📸 Чтобы загружать скриншоты, сначала активируй режим загрузки:\n\n"
            "<code>/upload</code> — войти в режим загрузки\n\n"
            "После этого я буду готов принимать твои скриншоты статистики."
        )
        return

    # Если middleware не передал album, используем само сообщение как список из 1
    messages = album or [message]

    # Сортировка сообщений по ID
    messages.sort(key=lambda m: m.message_id)

    if not messages:
        return

    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer(
        f"⏳ Анализирую пакет из {len(messages)} скриншотов... (~15-30 сек)"
    )

    # 1. Параллельное скачивание всех фото
    try:
        images_bytes = await _download_all_photos(messages, bot)
    except Exception as e:
        logger.exception("Failed to download photos: %s", e)
        await processing_msg.edit_text(f"❌ Ошибка при скачивании фото: {str(e)[:100]}")
        return

    if not images_bytes:
        await processing_msg.edit_text("❌ Не удалось скачать ни одного фото.")
        return

    # 2. Batch AI анализ (один запрос на все скриншоты)
    try:
        ai_results, raw_response = await asyncio.to_thread(
            analyze_screenshot,
            images_bytes,
            mime_type="image/jpeg",
        )
    except Exception as e:
        logger.exception("AI analysis failed: %s", e)
        await processing_msg.edit_text(f"❌ Ошибка AI анализа: {str(e)[:100]}")
        return

    if not ai_results:
        await processing_msg.edit_text(
            "⚠️ Не удалось распознать данные из скриншотов. Попробуй ещё раз с более чёткими фото."
        )
        return

    # 3. Обработка результатов и сохранение
    results: list[VideoProcessingResult] = []
    saved_count = 0
    failed_count = 0
    duplicate_count = 0

    user_id = message.from_user.id if message.from_user else 0
    supabase = get_supabase()

    for idx, ai_result in enumerate(ai_results, start=1):
        result = _convert_ai_result_to_processing_result(idx, ai_result, raw_response)

        if result.success:
            try:
                # Сохранение в Supabase
                if supabase and user_id:
                    db_result = await asyncio.to_thread(
                        insert_video,
                        supabase,
                        user_id,
                        result.ai_result,
                        result.raw_response,
                    )

                    # Проверяем, является ли видео дубликатом
                    if (
                        db_result
                        and db_result.get("skipped")
                        and db_result.get("duplicate")
                    ):
                        result.is_duplicate = True
                        duplicate_count += 1
                        logger.info(
                            "Video %d is duplicate, skipping Google Sheets export", idx
                        )
                    else:
                        # Видео сохранено (NEW или UPDATE)
                        saved_count += 1

                        # Экспорт в Google Sheets (не делаем для дубликатов)
                        if GOOGLE_SHEET_ID:
                            await queue_export_to_sheet(result.ai_result)
                else:
                    # Если нет supabase, считаем что сохранение не удалось
                    result.success = False
                    result.error_message = "❌ Нет подключения к базе данных"
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to save video {idx}: {e}")
                result.success = False
                result.error_message = f"❌ Ошибка сохранения: {str(e)[:40]}"
                failed_count += 1
        else:
            failed_count += 1

        results.append(result)

    # 4. Формирование отчета
    report_text = build_summary_report(
        results, saved_count, failed_count, duplicate_count, len(messages)
    )

    # Клавиатура с ссылкой на Google Sheet
    keyboard = None
    if GOOGLE_SHEET_ID:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Открыть таблицу", url=sheet_url)]
            ]
        )

    # Отправка отчета
    try:
        await processing_msg.edit_text(report_text, reply_markup=keyboard)
    except Exception:
        await message.answer(report_text, reply_markup=keyboard)


async def _download_all_photos(messages: list[Message], bot: Bot) -> list[bytes]:
    """Параллельно скачивает все фото из списка сообщений."""

    async def download_single(message: Message) -> bytes | None:
        """Скачивает фото из одного сообщения."""
        try:
            if not message.photo:
                return None

            # Берем фото наилучшего качества
            photo = message.photo[-1]
            file_id = photo.file_id

            file_info = await bot.get_file(file_id)
            if not file_info.file_path:
                raise ValueError("No file path")

            downloaded = await bot.download_file(file_info.file_path)
            if downloaded is None:
                raise ValueError("Empty response")

            return (
                downloaded.read()
                if hasattr(downloaded, "read")
                else bytes(downloaded)
            )
        except Exception as e:
            logger.warning(f"Failed to download photo from message {message.message_id}: {e}")
            return None

    # Параллельное скачивание всех фото
    download_tasks = [download_single(msg) for msg in messages]
    downloaded = await asyncio.gather(*download_tasks, return_exceptions=True)

    # Фильтруем успешные результаты
    images_bytes: list[bytes] = []
    for result in downloaded:
        if isinstance(result, bytes) and result:
            images_bytes.append(result)
        elif isinstance(result, Exception):
            logger.warning(f"Download task failed: {result}")

    return images_bytes


def _convert_ai_result_to_processing_result(
    index: int, ai_result: dict[str, Any], raw_response: str | None
) -> VideoProcessingResult:
    """Конвертирует результат AI в VideoProcessingResult."""

    if not ai_result or not isinstance(ai_result, dict):
        return VideoProcessingResult(
            index=index,
            success=False,
            error_message="⚠️ Пустой или некорректный результат AI",
        )

    title = ai_result.get("video_title") or ai_result.get("hook_text") or f"Video #{index}"
    score = ai_result.get("score", 0)

    # Определение рейтинга для краткости (Kill/Iterate/Scale)
    verdict = ai_result.get("verdict", "")
    rating_label = "N/A"
    if "KILL" in verdict:
        rating_label = "Kill"
    elif "ITERATE" in verdict:
        rating_label = "Iterate"
    elif "SCALE" in verdict:
        rating_label = "Scale"

    return VideoProcessingResult(
        index=index,
        success=True,
        video_title=title,
        score=score,
        rating_label=rating_label,
        ai_result=ai_result,
        raw_response=raw_response,
    )


def build_summary_report(
    results: list[VideoProcessingResult],
    saved: int,
    failed: int,
    duplicates: int,
    total_images: int,
) -> str:
    """Формирует итоговое сообщение для пользователя."""

    total = len(results)
    lines = [
        f"✅ Готово: {total} видео (скриншотов: {total_images})",
        f"💾 Сохранено: {saved} | ♻️ Дубликатов: {duplicates} | ❌ Ошибок: {failed}",
        "",
    ]

    for res in results:
        if res.success:
            platform = (
                res.ai_result.get("platform", "Unknown").capitalize()
                if res.ai_result
                else "Video"
            )
            title = res.video_title or "Untitled"
            if len(title) > 18:
                title = title[:18] + "…"

            icon = "🟢"
            if res.rating_label == "Kill":
                icon = "🔴"
            elif res.rating_label == "Iterate":
                icon = "🟡"

            line = f"{icon} {res.index}. [{platform}] {title} — {res.score}/100"

            # Добавляем пометку для дубликатов
            if res.is_duplicate:
                line += " ♻️ Данные не изменились"

            lines.append(line)
        else:
            error_msg = res.error_message or "Ошибка"
            lines.append(f"⚠️ {res.index}. {error_msg}")

    return "\n".join(lines)
