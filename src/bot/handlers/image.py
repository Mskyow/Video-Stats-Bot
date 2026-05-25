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
from src.bot.states import UploadMode, YouTubeUploadMode
from src.config import (
    GOOGLE_SHEET_ID,
    MAX_CONCURRENT_ANALYSIS,
    TG_FILE_DOWNLOAD_TIMEOUT_SEC,
    VIDEO_ANALYSIS_AI_TIMEOUT_SEC,
    VIDEO_ANALYSIS_DB_TIMEOUT_SEC,
)
from src.db.repositories.videos import insert_video
from src.db.supabase_client import get_supabase
from src.services.sheets_service import queue_export

router = Router(name="image")
logger = logging.getLogger(__name__)
VIDEO_ANALYSIS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS)
PENDING_PHOTO_IDS_KEY = "pending_video_photo_ids"


@dataclass
class VideoProcessingResult:
    """Результат обработки одного видео (2 или 3 скриншота в зависимости от режима)."""
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


@router.message(UploadMode.active, F.photo)
@router.message(YouTubeUploadMode.active, F.photo)
async def handle_photo(
    message: Message,
    bot: Bot,
    state: FSMContext,
    album: list[Message] | None = None,
) -> None:
    """
    Обрабатывает альбом скриншотов (или одиночное фото).
    
    Алгоритм:
    1. Сортируем сообщения альбома по ID.
    2. Разбиваем на группы по 2 или 3 (режим пользователя): Overview + Retention [ + опционально 3-й ].
    3. Запускаем параллельный анализ для всех групп.
    4. Сохраняем успешные результаты в БД/Google Sheets.
    5. Формируем единый сводный отчет.
    """
    # Обрабатываем скриншоты только в личке и только после явного /upload.
    if message.chat.type != "private":
        return

    # Если middleware не передал album, используем само сообщение как список из 1
    messages = album or [message]
    user_id = message.from_user.id if message.from_user else 0
    logger.info(
        "Photo batch received: user_id=%s message_id=%s media_group_id=%s count=%s",
        user_id,
        message.message_id,
        message.media_group_id,
        len(messages),
    )

    # Режим: строго 2 или 3 скриншота на одно видео (берём из БД до любой обработки)
    current_data = await state.get_data()
    chunk_size = int(current_data.get("upload_chunk_size") or "2")

    n = len(messages)
    if n == 0:
        logger.warning("Empty photo batch: user_id=%s message_id=%s", user_id, message.message_id)
        return

    # Single screenshots sent as separate messages are buffered until the full pair/triple is ready.
    if message.media_group_id is None and n == 1:
        current_data = await state.get_data()
        pending_photo_ids = list(current_data.get(PENDING_PHOTO_IDS_KEY) or [])
        pending_photo_ids.append(messages[0].photo[-1].file_id)
        await state.update_data(**{PENDING_PHOTO_IDS_KEY: pending_photo_ids})

        if len(pending_photo_ids) < chunk_size:
            await message.answer(
                f"📥 Скриншоты сохранены: <b>{len(pending_photo_ids)}/{chunk_size}</b>\n"
                f"Пришли ещё {chunk_size - len(pending_photo_ids)} скриншот(а), и я начну анализ."
            )
            return

        if len(pending_photo_ids) > chunk_size:
            await state.update_data(**{PENDING_PHOTO_IDS_KEY: []})
            await message.answer(
                "⚠️ В очереди оказалось больше скриншотов, чем нужно на одно видео.\n\n"
                "Я сбросил локальную очередь. Начни заново: отправь ровно "
                f"{chunk_size} скриншота на одно видео."
            )
            return

        file_id_groups = [pending_photo_ids]
        await state.update_data(**{PENDING_PHOTO_IDS_KEY: []})
        await _run_batch_processing(
            message=message,
            bot=bot,
            file_id_groups=file_id_groups,
            user_id=user_id,
            effective_chunk_size=chunk_size,
        )
        return

    # Albums must contain a whole number of videos.
    if n % chunk_size != 0:
        logger.info(
            "Rejected batch by count: user_id=%s message_id=%s count=%s chunk_size=%s",
            user_id,
            message.message_id,
            n,
            chunk_size,
        )
        if chunk_size == 2:
            await message.answer(
                "⚠️ <b>Режим «2 скриншота»</b>\n\n"
                "Отправь чётное количество фото: по 2 скриншота на каждое видео (Обзор + Удержание).\n\n"
                f"Сейчас отправлено: <b>{n}</b>. Добавь или убери одно фото, либо переключи режим: /mode"
            )
        else:
            await message.answer(
                "⚠️ <b>Режим «3 скриншота»</b>\n\n"
                "Отправь количество фото, кратное 3: по 3 скриншота на каждое видео.\n\n"
                f"Сейчас отправлено: <b>{n}</b>. Добавь или убери фото, либо переключи режим: /mode"
            )
        return

    messages.sort(key=lambda m: m.message_id)
    file_id_groups: list[list[str]] = [
        [msg.photo[-1].file_id for msg in messages[i : i + chunk_size]]
        for i in range(0, len(messages), chunk_size)
    ]
    await _run_batch_processing(
        message=message,
        bot=bot,
        file_id_groups=file_id_groups,
        user_id=user_id,
        effective_chunk_size=chunk_size,
    )

@router.callback_query(F.data == "exit_upload_mode")
async def cb_exit_upload_mode(callback: CallbackQuery, state: FSMContext):
    """Выход из режима загрузки по кнопке."""
    await state.clear()
    await callback.message.edit_text(
        "✅ Режим скринов выключен.\nДля новой загрузки используй /upload",
        reply_markup=None
    )
    await callback.answer("Режим скринов выключен")


async def process_single_video(
    index: int,
    messages: list[Message],
    bot: Bot,
    batch_id: str | None = None,
) -> VideoProcessingResult:
    """
    Обрабатывает одно видео: принимает динамический список сообщений (2 или 3 фото),
    скачивает их параллельно, передаёт в AI-анализатор. Не падает при ошибках (Fault Tolerance).
    """
    try:
        async with VIDEO_ANALYSIS_SEMAPHORE:
            started_at = asyncio.get_running_loop().time()
            logger.debug(
                "Processing video batch_id=%s index=%s message_ids=%s",
                batch_id,
                index,
                [m.message_id for m in messages],
            )
            # Берём наилучшее качество (photo[-1]); скачиваем все файлы группы параллельно
            photos = [m.photo[-1] for m in messages]  # type: ignore[index]

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

            # Конкурентная загрузка всех фото группы (2 или 3)
            logger.info(
                "Downloading screenshots batch_id=%s index=%s count=%s",
                batch_id,
                index,
                len(photos),
            )
            images_bytes = await asyncio.gather(*[download(p.file_id) for p in photos])
            logger.info(
                "Downloaded screenshots batch_id=%s index=%s bytes=%s",
                batch_id,
                index,
                [len(img) for img in images_bytes],
            )

            # AI Анализ (в треде, т.к. requests синхронный)
            logger.info("Starting AI analysis batch_id=%s index=%s", batch_id, index)
            result_json, raw_response = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_screenshot,
                    list(images_bytes),
                    mime_type="image/jpeg",
                ),
                timeout=VIDEO_ANALYSIS_AI_TIMEOUT_SEC,
            )
            logger.info(
                "AI analysis finished batch_id=%s index=%s success=%s elapsed_sec=%.2f",
                batch_id,
                index,
                bool(result_json),
                asyncio.get_running_loop().time() - started_at,
            )

            if not result_json:
                logger.warning("AI returned empty result batch_id=%s index=%s", batch_id, index)
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⚠️ AI не смог распознать метрики. "
                        "Проверь, что на скриншотах четко видны цифры, и отправь скриншоты повторно."
                    ),
                )

            # Ошибка авторизации OpenRouter (401) — неверный/просроченный API ключ
            if result_json.get("error") == "api_auth_failed":
                logger.error("AI auth failed batch_id=%s index=%s", batch_id, index)
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⚠️ Ошибка доступа к AI (OpenRouter). "
                        "Проверьте API ключ OPENROUTER_API_KEY в настройках бота."
                    ),
                )

            # Проверка на несоответствие контента (разные видео в скриншотах)
            if result_json.get("error") == "content_mismatch":
                logger.info("AI content mismatch batch_id=%s index=%s reason=%s", batch_id, index, result_json.get("reason"))
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
            result_json["source_image_count"] = len(messages)
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

            logger.info(
                "Video analysis success batch_id=%s index=%s title=%s score=%s verdict=%s total_elapsed_sec=%.2f",
                batch_id,
                index,
                title,
                score,
                verdict,
                asyncio.get_running_loop().time() - started_at,
            )
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


async def process_single_video_by_file_ids(
    index: int,
    file_ids: list[str],
    bot: Bot,
    batch_id: str | None = None,
) -> VideoProcessingResult:
    try:
        async with VIDEO_ANALYSIS_SEMAPHORE:
            started_at = asyncio.get_running_loop().time()

            async def download(file_id: str) -> bytes:
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
                    raise

            logger.info(
                "Downloading screenshots batch_id=%s index=%s count=%s",
                batch_id,
                index,
                len(file_ids),
            )
            images_bytes = await asyncio.gather(*[download(file_id) for file_id in file_ids])
            logger.info(
                "Downloaded screenshots batch_id=%s index=%s bytes=%s",
                batch_id,
                index,
                [len(img) for img in images_bytes],
            )

            logger.info("Starting AI analysis batch_id=%s index=%s", batch_id, index)
            result_json, raw_response = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_screenshot,
                    list(images_bytes),
                    mime_type="image/jpeg",
                ),
                timeout=VIDEO_ANALYSIS_AI_TIMEOUT_SEC,
            )
            logger.info(
                "AI analysis finished batch_id=%s index=%s success=%s elapsed_sec=%.2f",
                batch_id,
                index,
                bool(result_json),
                asyncio.get_running_loop().time() - started_at,
            )

            if not result_json:
                logger.warning("AI returned empty result batch_id=%s index=%s", batch_id, index)
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⚠️ AI не смог распознать метрики. "
                        "Проверь, что на скриншотах четко видны цифры, и отправь скриншоты повторно."
                    ),
                )

            if result_json.get("error") == "api_auth_failed":
                logger.error("AI auth failed batch_id=%s index=%s", batch_id, index)
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⚠️ Ошибка доступа к AI (OpenRouter). "
                        "Проверьте API ключ OPENROUTER_API_KEY в настройках бота."
                    ),
                )

            if result_json.get("error") == "content_mismatch":
                logger.info("AI content mismatch batch_id=%s index=%s reason=%s", batch_id, index, result_json.get("reason"))
                reason = result_json.get("reason", "Неизвестная причина")
                return VideoProcessingResult(
                    index=index,
                    success=False,
                    error_message=(
                        "⛔️ Скриншоты не совпадают: похоже, это разные видео. "
                        f"Причина: {reason}"
                    ),
                )

            result_json["source_image_count"] = len(file_ids)
            title = result_json.get("video_title") or f"Video #{index}"
            score = float(result_json.get("score", 0))
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

            logger.info(
                "Video analysis success batch_id=%s index=%s title=%s score=%s verdict=%s total_elapsed_sec=%.2f",
                batch_id,
                index,
                title,
                score,
                verdict,
                asyncio.get_running_loop().time() - started_at,
            )
            return VideoProcessingResult(
                index=index,
                success=True,
                video_title=title,
                score=score,
                rating_label=rating_label,
                ai_result=result_json,
                raw_response=raw_response,
            )
    except Exception as e:
        logger.exception("Error processing video batch_id=%s index=%s: %s", batch_id, index, e)
        return VideoProcessingResult(
            index=index,
            success=False,
            error_message=_format_processing_exception(e),
        )


async def _run_batch_processing(
    *,
    message: Message,
    bot: Bot,
    file_id_groups: list[list[str]],
    user_id: int,
    effective_chunk_size: int,
) -> None:
    batch_id = f"{user_id or 'unknown'}:{message.message_id}"
    logger.info(
        "Start batch processing id=%s user_id=%s chunk=%s groups=%s",
        batch_id,
        user_id,
        effective_chunk_size,
        len(file_id_groups),
    )

    processing_msg = await message.answer(
        f"⏳ <b>Начинаю обработку...</b>\n"
        f"Видео в очереди: {len(file_id_groups)} (режим: {effective_chunk_size} скриншота)\n"
        f"Одновременно обрабатываю: до {MAX_CONCURRENT_ANALYSIS}\n"
        f"⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️⬜️ 0%"
    )

    tasks = []
    progress_counter = {"processed": 0, "total": len(file_id_groups)}

    async def update_progress():
        progress_counter["processed"] += 1
        processed = progress_counter["processed"]
        total = progress_counter["total"]
        percent = int((processed / total) * 100)
        try:
            bar_len = 10
            filled = int(bar_len * processed / total)
            bar = "🟩" * filled + "⬜️" * (bar_len - filled)
            await processing_msg.edit_text(
                f"⏳ <b>Обработка видео...</b>\n"
                f"Готово: {processed} из {total}\n"
                f"{bar} {percent}%"
            )
        except Exception:
            pass

    for idx, file_ids in enumerate(file_id_groups, start=1):
        async def task_wrapper(i: int, ids: list[str], b: Bot):
            res = await process_single_video_by_file_ids(i, ids, b, batch_id=batch_id)
            await update_progress()
            return res
        tasks.append(task_wrapper(idx, file_ids, bot))

    results: list[VideoProcessingResult] = []
    processed_results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in processed_results:
        if isinstance(res, VideoProcessingResult):
            results.append(res)
        elif isinstance(res, Exception):
            logger.error("Critical error in worker task batch_id=%s: %s", batch_id, res)
            results.append(VideoProcessingResult(index=0, success=False, error_message=str(res)))

    results.sort(key=lambda r: r.index)

    saved_count = 0
    failed_count = 0
    duplicate_count = 0
    supabase = get_supabase()

    for res in results:
        if res.success and res.ai_result:
            is_duplicate = False
            try:
                db_saved = False
                if supabase and user_id:
                    try:
                        logger.info(
                            "Saving video to DB batch_id=%s video_index=%s title=%s",
                            batch_id,
                            res.index,
                            res.video_title,
                        )
                        insert_res = await asyncio.wait_for(
                            asyncio.to_thread(
                                insert_video,
                                supabase,
                                user_id,
                                res.ai_result,
                                res.raw_response,
                            ),
                            timeout=VIDEO_ANALYSIS_DB_TIMEOUT_SEC,
                        )
                        logger.info(
                            "DB save finished batch_id=%s video_index=%s result=%s",
                            batch_id,
                            res.index,
                            "duplicate" if insert_res and insert_res.get("duplicate") else bool(insert_res),
                        )
                        if insert_res and insert_res.get("duplicate"):
                            is_duplicate = True
                            res.error_message = "♻️ Дубликат (уже сохранено)"
                            duplicate_count += 1
                        elif insert_res:
                            db_saved = True
                        else:
                            res.success = False
                            res.error_message = "⚠️ Ошибка сохранения в базу данных"
                            failed_count += 1
                            logger.error(
                                "DB insert returned empty result batch_id=%s video_index=%s",
                                batch_id,
                                res.index,
                            )
                    except Exception as e:
                        logger.exception(
                            "Failed to save video to Supabase batch_id=%s video_index=%s: %s",
                            batch_id,
                            res.index,
                            e,
                        )
                        res.success = False
                        res.error_message = "⚠️ Ошибка сохранения в базу данных"
                        failed_count += 1
                else:
                    db_saved = True

                if db_saved and not is_duplicate:
                    saved_count += 1
                    if GOOGLE_SHEET_ID:
                        try:
                            if res.raw_response:
                                res.ai_result["raw_response"] = res.raw_response
                            logger.info(
                                "Queueing Google Sheets export batch_id=%s video_index=%s",
                                batch_id,
                                res.index,
                            )
                            queue_export(res.ai_result)
                        except Exception as e:
                            logger.exception(
                                "Failed to queue Google Sheets export batch_id=%s video_index=%s: %s",
                                batch_id,
                                res.index,
                                e,
                            )
            except Exception as e:
                logger.exception(
                    "Failed to process post-AI result batch_id=%s video_index=%s: %s",
                    batch_id,
                    res.index,
                    e,
                )
                if not is_duplicate:
                    res.success = False
                    if not res.error_message:
                        res.error_message = "⚠️ Ошибка пост-обработки результата"
                    failed_count += 1
        else:
            failed_count += 1

    report_text = build_summary_report(results, saved_count, failed_count, duplicate_count)
    logger.info(
        "Built batch report id=%s user_id=%s saved=%s duplicates=%s failed=%s",
        batch_id,
        user_id,
        saved_count,
        duplicate_count,
        failed_count,
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    buttons = []
    if GOOGLE_SHEET_ID:
        url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
        buttons.append(InlineKeyboardButton(text="📊 Открыть таблицу", url=url))
    buttons.append(InlineKeyboardButton(text="✅ Закрыть режим скринов", callback_data="exit_upload_mode"))
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
            elif "openrouter" in error_msg.lower() or "api ключ" in error_msg.lower():
                error_msg = "Ошибка API ключа OpenRouter"
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
        report += f"\n\n📤 Поставлено в очередь экспорта в Google таблицу: {saved}/{total_videos}"
        
    return report
