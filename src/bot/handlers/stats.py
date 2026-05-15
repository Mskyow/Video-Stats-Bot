"""
Handlers for statistics and reporting commands (/day_stats, /all_stats).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src.config import (
    ADMIN_USER_ID,
    GOOGLE_SHEET_ID,
    REPORT_CHAT_ID,
    REPORT_TOPIC_ID,
)
from src.db.repositories.videos import get_videos_by_date_range, get_global_stats
from src.services.sheets_service import get_marketing_funnel_daily_summary

logger = logging.getLogger(__name__)

router = Router(name="stats")


def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (ValueError, TypeError):
        return "0"


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
        f"📊 <b>Ежедневный отчёт</b>",
        f"<i>Видео: последние 24 часа. Дата отчёта: {now_minsk.strftime('%b %d')}</i>",
        "",
        "<b>Видео</b>",
        f"• Загружено: <b>{len(videos)}</b>",
        f"• Платформы: {platforms_text}",
        f"• Средний score: <b>{avg_score:.1f}/10</b>",
        f"• Лучший score: <b>{max_score:.1f}/10</b>",
        f"• Макс. views: <b>{formatted_views}</b>",
    ]

    funnel_report_date = (now_minsk - timedelta(days=1)).strftime("%Y-%m-%d")
    funnel_summary = await asyncio.to_thread(get_marketing_funnel_daily_summary, funnel_report_date)

    report_lines.extend(["", f"<b>Marketing Funnels ({(now_minsk - timedelta(days=1)).strftime('%b %d')})</b>"])
    if not funnel_summary.get("available"):
        report_lines.append("• Воронка недоступна: не настроен доступ к таблице")
    elif not funnel_summary.get("has_rows"):
        report_lines.append("• За эту дату в листе пока нет строк")
    else:
        social_views = funnel_summary.get("social_views", {})
        stores = funnel_summary.get("stores", {})
        report_lines.append(
            "• Viral views: "
            f"TikTok <b>{_format_int(social_views.get('TikTok Viral', 0))}</b>, "
            f"YouTube <b>{_format_int(social_views.get('YouTube Viral', 0))}</b>, "
            f"Instagram <b>{_format_int(social_views.get('Instagram Viral', 0))}</b>"
        )

        app_store = stores.get("App Store", {})
        google_play = stores.get("Google Play", {})
        report_lines.append(
            "• App Store: "
            f"search <b>{_format_int(app_store.get('search_impressions', 0))}</b>, "
            f"page views <b>{_format_int(app_store.get('product_page_views', 0))}</b>, "
            f"installs <b>{_format_int(app_store.get('installs', 0))}</b>"
        )
        report_lines.append(
            "• Google Play: "
            f"search <b>{_format_int(google_play.get('search_impressions', 0))}</b>, "
            f"page views <b>{_format_int(google_play.get('product_page_views', 0))}</b>, "
            f"installs <b>{_format_int(google_play.get('installs', 0))}</b>"
        )

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
    report_text += "\n\nДля деталей — Google таблица 👇"

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


@router.message(Command("send_report"))
async def cmd_send_report(message: Message, **kwargs) -> None:
    """
    Ручная отправка отчёта за последние 24ч в рабочий чат (REPORT_CHAT_ID).
    Доступна только администратору (ADMIN_USER_ID).
    """
    user = message.from_user
    if not user:
        return

    if not ADMIN_USER_ID:
        await message.answer("⚠️ Команда отключена (не задан ADMIN_USER_ID).")
        return

    if user.id != ADMIN_USER_ID:
        await message.answer("⚠️ Команда доступна только администратору.")
        return

    if not REPORT_CHAT_ID:
        await message.answer(
            "⚠️ Рабочий чат не настроен. Задайте REPORT_CHAT_ID в переменных окружения."
        )
        return

    loading_msg = await message.answer("⏳ Отправляю отчёт в рабочий чат...")

    supabase_client = kwargs.get("supabase_client")
    if not supabase_client:
        logger.error("supabase_client not found in handler kwargs for /send_report")
        await loading_msg.edit_text(
            "⚠️ Ошибка подключения к базе данных. Попробуй позже."
        )
        return

    try:
        report_text, markup = await build_day_stats_report(supabase_client)
        send_kwargs: dict = {
            "chat_id": REPORT_CHAT_ID,
            "text": report_text,
            "parse_mode": "HTML",
        }
        if markup is not None:
            send_kwargs["reply_markup"] = markup
        if REPORT_TOPIC_ID is not None:
            send_kwargs["message_thread_id"] = REPORT_TOPIC_ID

        bot = message.bot
        await bot.send_message(**send_kwargs)
        await loading_msg.edit_text("✅ Отчёт отправлен в рабочий чат.")
        logger.info("User %s triggered manual report send to chat %s", user.id, REPORT_CHAT_ID)
    except Exception as e:
        logger.exception("Failed to send report to work chat: %s", e)
        await loading_msg.edit_text(
            "⚠️ Не удалось отправить отчёт в рабочий чат. Попробуй позже."
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
