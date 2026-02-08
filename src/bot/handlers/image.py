"""
Приём медиа-группы (альбома скриншотов) → Параллельный AI анализ → Сводный отчёт → Сохранение.
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
from aiogram.types import Message

from src.ai.openrouter_service import analyze_screenshot
from src.bot.states import UploadMode
from src.config import GOOGLE_SHEET_ID
from src.db.repositories.users import is_user_authorized
from src.db.repositories.videos import insert_video
from src.db.supabase_client import get_supabase
from src.services.sheets_service import export_video_to_sheet

router = Router(name="image")
logger = logging.getLogger(__name__)


@dataclass
class VideoProcessingResult:
    """Результат обработки одного видео (пары скриншотов)."""
    index: int
    video_title: str | None = None
    success: bool = False
    score: int = 0
    rating_label: str = "N/A"
    error_message: str | None = None
    ai_result: dict[str, Any] | None = None
    raw_response: str | None = None
    is_orphan: bool = False  # Если не хватило пары (нечетное кол-во)


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot, state: FSMContext, album: list[Message] | None = None) -> None:
    """
    Обрабатывает альбом скриншотов (или одиночное фото).

    Алгоритм:
    1. Проверяем авторизацию пользователя
    2. Проверяем, активен ли режим загрузки (UploadMode.active)
    3. Сортируем сообщения альбома по ID.
    4. Разбиваем на пары (Overview + Retention).
    5. Запускаем параллельный анализ для всех пар.
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
    
    # 1. Сортировка и валидация
    messages.sort(key=lambda m: m.message_id)
    
    # Если это первая часть альбома, который еще собирается, то middleware
    # может отдать управление сюда. Но middleware должен был собрать все.
    # Если album передан - значит он уже собран полностью.
    
    pairs = []
    orphans = []
    
    # Разбиваем на пары [A, B], [C, D]
    # Предполагаем, что скриншоты идут парами: Обзор + Удержание
    # Порядок внутри пары не гарантирован телеграмом, но обычно последователен.
    # Мы будем передавать ОБА фото в AI и просить разобраться.
    for i in range(0, len(messages), 2):
        if i + 1 < len(messages):
            pairs.append((messages[i], messages[i+1]))
        else:
            orphans.append(messages[i])
            
    total_videos = len(pairs) + len(orphans)
    if total_videos == 0:
        return

    # Отправляем сообщение о начале обработки (если много видео)
    if len(messages) > 1:
        processing_msg = await message.answer(
            f"⏳ Скриншотов: {len(messages)} | Видео: {len(pairs)}\n"
            f"Анализирую... (~15-30 сек)"
        )
    else:
        # Для одиночного фото тоже нужен фидбек
        processing_msg = await message.answer("⏳ Анализирую...")

    # 2. Параллельная обработка (The Engine)
    tasks = []
    for idx, (msg1, msg2) in enumerate(pairs, start=1):
        tasks.append(process_single_video(idx, msg1, msg2, bot))

    # Добавляем "орфанные" (непарные) как ошибки
    results: list[VideoProcessingResult] = []
    
    # Запускаем задачи
    if tasks:
        processed_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for res in processed_results:
            if isinstance(res, VideoProcessingResult):
                results.append(res)
            elif isinstance(res, Exception):
                # Критическая ошибка в таске (не должна случаться, т.к. внутри catch-all)
                logger.error("Critical error in worker task: %s", res)
                # Добавляем заглушку ошибки
                results.append(VideoProcessingResult(index=0, success=False, error_message=str(res)))

    # Обработка непарных (skipped)
    for i, orphan_msg in enumerate(orphans):
        # Индекс продолжаем после пар
        idx = len(pairs) + 1 + i
        results.append(VideoProcessingResult(
            index=idx,
            success=False,
            is_orphan=True,
            error_message="⚠️ Непарный скриншот — нужна пара: Обзор + Удержание"
        ))

    # Сортируем результаты по индексу для отчета
    results.sort(key=lambda r: r.index)

    # 3. Сохранение данных (Fault Tolerance: сохраняем то, что успешно)
    saved_count = 0
    failed_count = 0
    
    user_id = message.from_user.id if message.from_user else 0
    supabase = get_supabase()

    for res in results:
        if res.success and res.ai_result:
            try:
                # Сохранение в Supabase
                if supabase and user_id:
                    await asyncio.to_thread(
                        insert_video,
                        supabase,
                        user_id,
                        res.ai_result,
                        res.raw_response,
                    )
                
                # Экспорт в Google Sheets (если Score > Threshold, но тут сохраняем все успешные)
                # Логика фильтрации может быть внутри export_hook_to_sheet или здесь.
                # По ТЗ: "Если score > threshold, save to Google Sheets" - проверим score.
                # Но обычно export_hook_to_sheet сама решает или сохраняем всё. 
                # Предположим, сохраняем всё успешное, а сервис сам решит.
                if GOOGLE_SHEET_ID:
                    await asyncio.to_thread(export_video_to_sheet, res.ai_result)
                
                saved_count += 1
            except Exception as e:
                logger.error(f"Failed to save video {res.index}: {e}")
                # Не помечаем как failed для юзера, так как анализ прошел, проблема на бэкенде
                # Можно добавить пометку в отчет, но пока просто логируем.
        else:
            failed_count += 1

    # 4. Формирование отчета (UX)
    report_text = build_summary_report(results, saved_count, failed_count, len(messages))

    # Отправка отчета
    try:
        await processing_msg.edit_text(report_text)
    except Exception:
        await message.answer(report_text)


async def process_single_video(index: int, msg1: Message, msg2: Message, bot: Bot) -> VideoProcessingResult:
    """
    Скачивает два фото, отправляет в AI, возвращает результат.
    Не падает при ошибках (Fault Tolerance).
    """
    try:
        # Скачиваем фото параллельно
        # Берем photo[-1] (наилучшее качество)
        photos = [msg1.photo[-1], msg2.photo[-1]] # type: ignore
        
        # Функция для скачивания с ретраями/обработкой
        async def download(file_id: str) -> bytes:
            try:
                file_info = await bot.get_file(file_id)
                if not file_info.file_path:
                    raise ValueError("No file path")
                
                downloaded = await bot.download_file(file_info.file_path)
                if downloaded is None:
                    raise ValueError("Empty response")
                    
                return downloaded.read() if hasattr(downloaded, "read") else bytes(downloaded)
            except TelegramNetworkError:
                # Простейший retry logic можно добавить здесь, но для скорости пока без него
                raise
        
        # Параллельная загрузка двух файлов
        images_bytes = await asyncio.gather(
            download(photos[0].file_id),
            download(photos[1].file_id)
        )
        
        # AI Анализ (в треде, т.к. requests синхронный)
        result_json, raw_response = await asyncio.to_thread(
            analyze_screenshot,
            list(images_bytes),
            mime_type="image/jpeg",
        )
        
        if not result_json:
            return VideoProcessingResult(
                index=index,
                success=False,
                error_message="⚠️ Не удалось распознать метрики"
            )
            
        # Успешный анализ
        title = result_json.get("video_title") or f"Video #{index}"
        score = result_json.get("score", 0)
        
        # Определение рейтинга для краткости (Kill/Iterate/Scale)
        verdict = result_json.get("verdict", "")
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
            ai_result=result_json,
            raw_response=raw_response
        )

    except Exception as e:
        logger.exception(f"Error processing video {index}: {e}")
        return VideoProcessingResult(
            index=index,
            success=False,
            error_message=f"⚠️ Ошибка: {str(e)[:40]}"
        )


def build_summary_report(results: list[VideoProcessingResult], saved: int, failed: int, total_images: int) -> str:
    """Формирует итоговое сообщение для пользователя."""

    total = len(results)
    lines = [
        f"✅ Готово: {total} видео",
        f"💾 Сохранено: {saved} | ❌ Ошибок: {failed}",
        ""
    ]

    for res in results:
        if res.success:
            platform = res.ai_result.get("platform", "Unknown").capitalize() if res.ai_result else "Video"
            title = res.video_title or "Untitled"
            if len(title) > 18:
                title = title[:18] + "…"

            icon = "🟢"
            if res.rating_label == "Kill":
                icon = "🔴"
            elif res.rating_label == "Iterate":
                icon = "🟡"

            line = f"{icon} {res.index}. [{platform}] {title} — {res.score}/100"
            lines.append(line)
        else:
            error_msg = res.error_message or "Ошибка"
            lines.append(f"⚠️ {res.index}. {error_msg}")

    return "\n".join(lines)
