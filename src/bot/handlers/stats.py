"""
Handlers for statistics and reporting commands (/day_stats, /all_stats).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src.config import GOOGLE_SHEET_ID
from src.db.repositories.videos import get_videos_by_date_range, get_global_stats

logger = logging.getLogger(__name__)

router = Router(name="stats")


async def build_day_stats_report(
    supabase_client,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """
    Строит полный отчёт day_stats (как для /day_stats).
    Возвращает (текст_отчёта, markup_или_None).
    """
    now = datetime.utcnow()
    minsk_offset = timedelta(hours=3)
    now_minsk = now + minsk_offset

    yesterday = now - timedelta(hours=24)
    start_date = yesterday.isoformat()
    end_date = now.isoformat()

    videos = get_videos_by_date_range(supabase_client, start_date, end_date)

    if not videos:
        return (
            "📊 За последние 24 часа нет анализов.\n\n"
            "Отправь скриншоты метрик видео.",
            None,
        )

    # 1. Считаем количество видео по платформам
    platform_counts: dict[str, int] = {"TikTok": 0, "Instagram": 0, "YouTube Shorts": 0}

    for v in videos:
        plat_raw = (v.get("platform") or "Other").lower()
        if "tiktok" in plat_raw:
            platform_counts["TikTok"] += 1
        elif "reels" in plat_raw or "instagram" in plat_raw:
            platform_counts["Instagram"] += 1
        elif "youtube" in plat_raw or "shorts" in plat_raw:
            platform_counts["YouTube Shorts"] += 1
        else:
            platform_counts.setdefault("Other", 0)
            platform_counts["Other"] += 1

    platforms_summary = []
    for plat, count in platform_counts.items():
        if count > 0:
            platforms_summary.append(f"{plat}: {count}")

    platforms_text = ". ".join(platforms_summary) if platforms_summary else "Нет данных"

    # 2. Средний балл за сегодня
    scores: list[float] = []
    for v in videos:
        s = v.get("score")
        if s is not None:
            try:
                scores.append(float(s))
            except (ValueError, TypeError):
                pass

    avg_score = sum(scores) / len(scores) if scores else 0

    # 3. Высший балл
    max_score = max(scores) if scores else 0

    # 4. Высшие просмотры
    max_views = 0
    for v in videos:
        metrics = v.get("metrics") or {}
        raw_views = metrics.get("views") or metrics.get("view_count")

        if raw_views is not None:
            try:
                current_views = 0
                if isinstance(raw_views, (int, float)):
                    current_views = int(raw_views)
                elif isinstance(raw_views, str):
                    clean_views = raw_views.upper().replace(",", "").replace(" ", "")
                    if "K" in clean_views:
                        current_views = int(float(clean_views.replace("K", "")) * 1000)
                    elif "M" in clean_views:
                        current_views = int(float(clean_views.replace("M", "")) * 1000000)
                    elif clean_views.replace(".", "", 1).isdigit():
                        current_views = int(float(clean_views))

                if current_views > max_views:
                    max_views = current_views
            except (ValueError, TypeError):
                pass

    formatted_views = f"{max_views:,}".replace(",", " ")

    report_lines = [
        f"<b>Отчет по видео за последние сутки ({now_minsk.strftime('%d.%m')}):</b>\n",
        "1. Количество видео по платформам:",
        f"{platforms_text}",
        f"2. Средний балл за сегодня: {avg_score:.1f}/10",
        f"3. Высший балл за видео: {max_score:.1f}/10",
        f"4. Высшие просмотры на видео за сегодня: {formatted_views}",
    ]

    report_text = "\n".join(report_lines)

    # Генерация AI summary
    try:
        from src.ai.day_summary import generate_day_summary

        ai_summary_min_videos = 3
        ai_summary = await generate_day_summary(videos, min_videos=ai_summary_min_videos)
        if ai_summary:
            ai_block = "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
            ai_block += ai_summary
            if len(report_text) + len(ai_block) > 3800:
                logger.warning("AI summary is too long for Telegram message; shrinking AI block.")
                ai_block = (
                    "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
                    "🤖 <b>AI-сводка дня</b>\n\n"
                    "Сводка сокращена из-за лимита Telegram.\n"
                    "Подробности — в Google таблице."
                )
            report_text += ai_block
        elif len(videos) < ai_summary_min_videos:
            missing_videos = ai_summary_min_videos - len(videos)
            missing_videos_text = (
                f"нужно еще {missing_videos} видео"
                if missing_videos > 0
                else "нужно больше данных"
            )
            report_text += (
                "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 <b>AI-сводка дня</b>\n\n"
                "Пока недостаточно данных для AI-сводки.\n"
                f"Сейчас: {len(videos)} видео, минимум: {ai_summary_min_videos} ({missing_videos_text})."
            )
    except Exception as e:
        logger.warning("Failed to generate AI summary: %s", e)

    # Добавляем ссылку на Google Sheets в конец
    report_text += "\n\nДля просмотра детальной статистики перейдите в Google таблицу👇"

    # Клавиатура с кнопкой
    markup = None
    if GOOGLE_SHEET_ID:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Перейти в Google Таблицу", url=sheet_url)]
            ]
        )

    return report_text, markup


@router.message(Command("day_stats"))
async def cmd_day_stats(message: Message, **kwargs) -> None:
    """
    Fetch videos for the last 24h and display the day stats report.
    """
    user_id = message.from_user.id
    logger.info("User %s requested /day_stats", user_id)

    loading_msg = await message.answer("⏳ Генерирую отчет...")

    supabase_client = kwargs.get("supabase_client")
    if not supabase_client:
        logger.error("supabase_client not found in handler kwargs for /day_stats")
        await loading_msg.edit_text(
            "⚠️ Ошибка подключения к базе данных.\n"
            "Попробуй позже."
        )
        return

    try:
        report_text, markup = await build_day_stats_report(supabase_client)
        await loading_msg.edit_text(report_text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error fetching day stats: %s", e)
        await loading_msg.edit_text(
            "⚠️ Ошибка при получении статистики.\n"
            "Попробуй позже."
        )


@router.message(Command("all_stats"))
async def cmd_all_stats(message: Message, **kwargs) -> None:
    """
    Fetch and display global stats.
    """
    user_id = message.from_user.id
    logger.info("User %s requested /all_stats", user_id)

    # Получаем supabase_client из kwargs (передан middleware)
    supabase_client = kwargs.get("supabase_client")
    if not supabase_client:
        logger.error("supabase_client not found in handler kwargs for /all_stats")
        await message.answer(
            "⚠️ Ошибка подключения к базе данных.\n"
            "Попробуй позже."
        )
        return

    try:
        stats = get_global_stats(supabase_client)
        if not stats:
            await message.answer(
                "📈 Не удалось получить статистику.\n\n"
                "Возможно, в базе ещё нет записей. "
                "Отправь скриншоты метрик."
            )
            return

        total = stats.get("total_count", 0)
        platforms = stats.get("platforms") or {}
        tiktok_stats = platforms.get("TikTok") or {}
        instagram_stats = platforms.get("Instagram") or {}

        if total == 0:
            await message.answer(
                "📈 В базе ещё нет видео.\n\n"
                "Отправь скриншоты метрик для анализа."
            )
            return

        def format_int(value: Any) -> str:
            try:
                return f"{int(value):,}".replace(",", " ")
            except (ValueError, TypeError):
                return "0"

        tiktok_total = tiktok_stats.get("total_videos", 0)
        instagram_total = instagram_stats.get("total_videos", 0)
        tiktok_avg = float(tiktok_stats.get("avg_score", 0) or 0)
        instagram_avg = float(instagram_stats.get("avg_score", 0) or 0)
        tiktok_max_views = format_int(tiktok_stats.get("max_views", 0))
        instagram_max_views = format_int(instagram_stats.get("max_views", 0))

        text = (
            "📈 <b>Общая статистика по всем загруженным видео</b>\n"
            "<i>Эта сводка помогает быстро сравнить TikTok и Instagram: "
            "где больше контента, выше средний балл и сильнее охват.</i>\n\n"
            "1️⃣ <b>Всего видео по платформам</b>\n"
            f"• TikTok: <b>{format_int(tiktok_total)}</b>\n"
            f"• Instagram: <b>{format_int(instagram_total)}</b>\n\n"
            "2️⃣ <b>Средний балл на видео по платформам</b>\n"
            f"• TikTok: <b>{tiktok_avg:.1f}/10</b>\n"
            f"• Instagram: <b>{instagram_avg:.1f}/10</b>\n\n"
            "3️⃣ <b>Самые высокие просмотры на одном видео по платформам</b>\n"
            f"• TikTok: <b>{tiktok_max_views}</b>\n"
            f"• Instagram: <b>{instagram_max_views}</b>\n\n"
            f"Всего видео в базе: <b>{format_int(total)}</b>"
        )
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.exception("Error fetching global stats: %s", e)
        await message.answer(
            "⚠️ Ошибка при получении статистики.\n"
            "Попробуй позже."
        )
