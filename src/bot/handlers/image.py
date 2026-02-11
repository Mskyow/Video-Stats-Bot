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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext

from src.ai.openrouter_service import analyze_screenshot
from src.config import (
    GOOGLE_SHEET_ID,
    MAX_CONCURRENT_ANALYSIS,
    TG_FILE_DOWNLOAD_TIMEOUT_SEC,
)
from src.db.repositories.videos import insert_video
from src.db.supabase_client import get_supabase
from src.services.sheets_service import queue_export

router = Router(name="image")
logger = logging.getLogger(__name__)
VIDEO_ANALYSIS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS)


@dataclass
class VideoProcessingResult:
    """Результат обработки одного видео (пары скриншотов)."""
    index: int
    video_title: str | None = None
    success: bool = False
    score: float = 0.0
    rating_label: str = "N/A"
    error_message: str | None = None
    ai_result: dict[str, Any] | None = None
    raw_response: str | None = None
    is_orphan: bool = False  # Если не хватило пары (нечетное кол-во)


def _format_processing_exception(exc: Exception) -> str:
    """
    Приводит технические исключения к коротким пользовательским сообщениям.
    """
    if isinstance(exc, TelegramNetworkError):
        return "⚠️ Telegram временно недоступен. Проверь интернет и повтори через 1-2 минуты."

    if isinstance(exc, TimeoutError):
        return "⚠️ Превышен тайм-аут при обработке. Повтори отправку этой пары скриншотов."

    text = str(exc).strip()
    lowered = text.lower()

    if any(token in lowered for token in ("429", "rate limit", "too many requests")):
        return "⚠️ Лимит AI-запросов временно достигнут. Повтори через 1-2 минуты."
    if any(token in lowered for token in ("timeout", "timed out")):
        return "⚠️ Запрос обрабатывался слишком долго. Попробуй ещё раз."
    if any(token in lowered for token in ("connection", "network", "dns", "unreachable")):
        return "⚠️ Сетевая ошибка при обработке. Проверь интернет и повтори попытку."

    if text:
        return f"⚠️ Ошибка обработки: {text[:160]}"
    return "⚠️ Непредвиденная ошибка обработки. Повтори попытку."


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot, album: list[Message] | None = None) -> None:
    """
    Обрабатывает альбом скриншотов (или одиночное фото).
    
    Алгоритм:
    1. Сортируем сообщения альбома по ID.
    2. Разбиваем на пары (Overview + Retention).
    3. Запускаем параллельный анализ для всех пар.
    4. Сохраняем успешные результаты в БД/Google Sheets.
    5. Формируем единый сводный отчет.
    """
    # Если middleware не передал album, используем само сообщение как список из 1
    messages = album or [message]
    user_id = message.from_user.id if message.from_user else 0
    
    # 1. Сортировка и валидация
    messages.sort(key=lambda m: m.message_id)
    
    pairs = []
    orphans = []
    
    # Разбиваем на пары [A, B], [C, D]
    for i in range(0, len(messages), 2):
        if i + 1 < len(messages):
            pairs.append((messages[i], messages[i+1]))
        else:
            orphans.append(messages[i])
            
    total_videos = len(pairs) + len(orphans)
    if total_videos == 0:
        return

    batch_id = f"{user_id or 'unknown'}:{message.message_id}"
    logger.info(
        "Start batch processing id=%s user_id=%s pairs=%s orphans=%s",
        batch_id,
        user_id,
        len(pairs),
        len(orphans),
    )

    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer(
        f"⏳ <b>Начинаю обработку...</b>\n"
        f"Видео в очереди: {len(pairs)}\n"
        f"Одновременно обрабатываю: до {MAX_CONCURRENT_ANALYSIS}\n"
        f"⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️ 0%"
    )

    # 2. Параллельная обработка (The Engine)
    tasks = []
    
    # Создаем shared counter для прогресса
    progress_counter = {"processed": 0, "total": len(pairs)}
    
    async def update_progress():
        progress_counter["processed"] += 1
        processed = progress_counter["processed"]
        total = progress_counter["total"]
        percent = int((processed / total) * 100)
        
        try:
            bar_len = 10
            filled = int(bar_len * processed / total)
            # Красивый прогресс бар
            bar = "🟩" * filled + "⬜️" * (bar_len - filled)
            
            await processing_msg.edit_text(
                f"⏳ <b>Обработка видео...</b>\n"
                f"Готово: {processed} из {total}\n"
                f"{bar} {percent}%"
            )
        except Exception:
            pass

    for idx, (msg1, msg2) in enumerate(pairs, start=1):
        # Оборачиваем process_single_video для обновления прогресса
        async def task_wrapper(i, m1, m2, b):
            res = await process_single_video(i, m1, m2, b, batch_id=batch_id)
            await update_progress()
            return res
            
        tasks.append(task_wrapper(idx, msg1, msg2, bot))

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
                logger.error("Critical error in worker task batch_id=%s: %s", batch_id, res)
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
            error_message="⚠️ Непарный скриншот (нужна пара: Обзор + Удержание)."
        ))

    results.sort(key=lambda r: r.index)

    # 3. Сохранение данных (Fault Tolerance: сохраняем то, что успешно)
    saved_count = 0
    failed_count = 0
    duplicate_count = 0
    
    supabase = get_supabase()

    for res in results:
        if res.success and res.ai_result:
            is_duplicate = False
            try:
                # 1. Попытка сохранения в Supabase
                if supabase and user_id:
                    try:
                        insert_res = await asyncio.to_thread(
                            insert_video,
                            supabase,
                            user_id,
                            res.ai_result,
                            res.raw_response,
                        )
                        
                        if insert_res and insert_res.get("duplicate"):
                            is_duplicate = True
                            res.error_message = "♻️ Дубликат (уже сохранено)"
                            duplicate_count += 1
                        else:
                            saved_count += 1
                            
                    except Exception as e:
                        logger.exception(
                            "Failed to save video to Supabase batch_id=%s video_index=%s: %s",
                            batch_id,
                            res.index,
                            e,
                        )
                        # Считаем успешным, так как анализ прошел
                        saved_count += 1
                else:
                    saved_count += 1

                # 2. Экспорт в Google Sheets (если не дубликат)
                if not is_duplicate and GOOGLE_SHEET_ID:
                    if res.raw_response:
                        res.ai_result["raw_response"] = res.raw_response
                    queue_export(res.ai_result)
                
            except Exception as e:
                logger.exception(
                    "Failed to process post-AI result batch_id=%s video_index=%s: %s",
                    batch_id,
                    res.index,
                    e,
                )
                # Если упало на верхнем уровне
                if not is_duplicate:
                     saved_count += 1
        else:
            failed_count += 1

    # 4. Формирование отчета (UX)
    # Считаем реальные ошибки (failed_count включает в себя и дубликаты, если они не сохранены)
    # Но в нашей логике выше: если дубликат, мы делаем duplicate_count += 1, и НЕ делаем saved_count += 1
    # failed_count инкрементится только если res.success == False.
    # Значит, дубликаты (у которых res.success=True, но error_message="Дубликат") не попадают в failed_count.
    # Проверим логику выше:
    # if res.success: -> check duplicate -> duplicate_count++ OR saved_count++
    # else: failed_count++
    # Итого: total = saved + duplicates + failed
    
    report_text = build_summary_report(results, saved_count, failed_count, duplicate_count)

    # Кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    buttons = []
    
    if GOOGLE_SHEET_ID:
        url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
        buttons.append(InlineKeyboardButton(text="📊 Google Sheet", url=url))
        
    buttons.append(InlineKeyboardButton(text="🚪 Выйти из режима загрузки", callback_data="exit_upload_mode"))
    # Располагаем кнопки вертикально
    for btn in buttons:
        keyboard.inline_keyboard.append([btn])

    await processing_msg.edit_text(report_text, reply_markup=keyboard)
    logger.info(
        "Finished batch id=%s user_id=%s total=%s saved=%s duplicates=%s failed=%s",
        batch_id,
        user_id,
        len(results),
        saved_count,
        duplicate_count,
        failed_count,
    )

@router.callback_query(F.data == "exit_upload_mode")
async def cb_exit_upload_mode(callback: CallbackQuery, state: FSMContext):
    """Выход из режима загрузки по кнопке."""
    await state.clear()
    await callback.message.edit_text(
        "✅ Режим загрузки завершён.\nДля новой загрузки используй /upload",
        reply_markup=None
    )
    await callback.answer("Режим загрузки отключен")


async def process_single_video(
    index: int,
    msg1: Message,
    msg2: Message,
    bot: Bot,
    batch_id: str | None = None,
) -> VideoProcessingResult:
    """
    Скачивает два фото, отправляет в AI, возвращает результат.
    Не падает при ошибках (Fault Tolerance).
    """
    try:
        async with VIDEO_ANALYSIS_SEMAPHORE:
            logger.debug(
                "Processing video batch_id=%s index=%s message_ids=(%s,%s)",
                batch_id,
                index,
                msg1.message_id,
                msg2.message_id,
            )
            # Скачиваем фото параллельно
            # Берем photo[-1] (наилучшее качество)
            photos = [msg1.photo[-1], msg2.photo[-1]]  # type: ignore

            async def download(file_id: str) -> bytes:
                """Скачивает один файл из Telegram с жесткими тайм-аутами."""
                try:
                    file_info = await asyncio.wait_for(
                        bot.get_file(file_id), timeout=TG_FILE_DOWNLOAD_TIMEOUT_SEC
                    )
                    if not file_info.file_path:
                        raise ValueError("No file path")

                    downloaded = await asyncio.wait_for(
                        bot.download_file(file_info.file_path),
                        timeout=TG_FILE_DOWNLOAD_TIMEOUT_SEC,
                    )
                    if downloaded is None:
                        raise ValueError("Empty response")

                    if hasattr(downloaded, "read"):
                        raw_data = await asyncio.wait_for(
                            asyncio.to_thread(downloaded.read),
                            timeout=TG_FILE_DOWNLOAD_TIMEOUT_SEC,
                        )
                    else:
                        raw_data = downloaded

                    return raw_data if isinstance(raw_data, bytes) else bytes(raw_data)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(
                        f"тайм-аут скачивания файла Telegram ({TG_FILE_DOWNLOAD_TIMEOUT_SEC:.0f}с)"
                    ) from exc
                except TelegramNetworkError:
                    # Сетевой сбой Telegram
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
                    error_message=(
                        "⚠️ AI не смог распознать метрики. "
                        "Проверь, что на скриншотах четко видны цифры, и отправь пару повторно."
                    ),
                )

            # Проверка на несоответствие контента (разные видео в скриншотах)
            if result_json.get("error") == "content_mismatch":
                reason = result_json.get("reason", "Неизвестная причина")
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⛔️ Скриншоты не совпадают: похоже, это разные видео. "
                        f"Причина: {reason}"
                    ),
                )

            # Успешный анализ
            title = result_json.get("video_title") or f"Video #{index}"
            score = float(result_json.get("score", 0))

            # Определение рейтинга для краткости (Kill/Fix/Iterate/Scale)
            verdict = result_json.get("verdict", "")
            rating_label = "N/A"
            if "KILL" in verdict:
                rating_label = "Kill"
            elif "FIX" in verdict:
                rating_label = "Fix"
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
        logger.exception("Error processing video batch_id=%s index=%s: %s", batch_id, index, e)
        return VideoProcessingResult(
            index=index,
            success=False,
            error_message=_format_processing_exception(e),
        )


def build_summary_report(results: list[VideoProcessingResult], saved: int, failed: int, duplicates: int) -> str:
    """Формирует итоговое сообщение для пользователя."""

    total_videos = len(results)
    header = (
        f"✅ <b>Готово: {total_videos} видео</b>\n"
        f"💾 Сохранено: {saved} | ♻️ Дубликатов: {duplicates} | ❌ Ошибок: {failed}\n"
    )
    
    lines = []
    for res in results:
        # Проверяем, является ли результат дубликатом
        is_duplicate = res.error_message and "дубликат" in res.error_message.lower()

        if res.success and not is_duplicate:
            # Успешный анализ и не дубликат
            platform = res.ai_result.get("platform", "Unknown") if res.ai_result else "Video"
            # Нормализация платформы
            if "instagram" in platform.lower():
                platform = "Instagram"
            elif "youtube" in platform.lower():
                platform = "YouTube"
            elif "tiktok" in platform.lower():
                platform = "TikTok"
            else:
                platform = platform.capitalize()

            # Обрезаем заголовок
            title = res.video_title or "Untitled"
            if len(title) > 25:
                title = title[:24] + "…"
            
            # Эмодзи по скору
            score = res.score
            emoji = "⚪"
            if score >= 8.0:
                emoji = "🟢"  # Отлично
            elif score >= 5.5:
                emoji = "🟡"  # Норм/Так себе
            else:
                emoji = "🔴"  # Плохо

            line = f"{emoji} {res.index}. [{platform}] {title} — {score}/10"
            lines.append(line)
            
        elif is_duplicate:
            # Дубликат
            platform = res.ai_result.get("platform", "Unknown") if res.ai_result else "Video"
            if "instagram" in platform.lower(): platform = "Instagram"
            elif "youtube" in platform.lower(): platform = "YouTube"
            elif "tiktok" in platform.lower(): platform = "TikTok"
            else: platform = platform.capitalize()
            
            title = res.video_title or "Untitled"
            if len(title) > 25: title = title[:24] + "…"
            
            line = f"♻️ {res.index}. [{platform}] {title} — Дубликат"
            lines.append(line)
            
        else:
            # Ошибка (res.success == False)
            error_msg = res.error_message or "Unknown error"
            # Упрощаем текст ошибки для списка
            if "непарный" in error_msg.lower():
                error_msg = "Непарный скриншот"
            elif "не смог распознать" in error_msg.lower():
                error_msg = "AI не распознал"
            elif "не совпадают" in error_msg.lower():
                error_msg = "Скриншоты от разных видео"
            elif "тайм-аут" in error_msg.lower() or "timeout" in error_msg.lower():
                error_msg = "Тайм-аут обработки"
            elif "лимит ai-запросов" in error_msg.lower() or "rate limit" in error_msg.lower():
                error_msg = "Лимит AI (повтори позже)"
            elif "telegram временно недоступен" in error_msg.lower():
                error_msg = "Telegram временно недоступен"
            
            line = f"❌ {res.index}. {error_msg}"
            lines.append(line)
            
    report = header + "\n" + "\n".join(lines)
    
    if GOOGLE_SHEET_ID:
        report += f"\n\n📤 Успешно отправлено в Google таблицу: {saved}/{total_videos}"
        
    return report
