"""
Telegram command for trying free public scraping of TikTok/Instagram video URLs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.states import PublicScrapeMode
from src.db.repositories.marketing import insert_public_video_scrape, upsert_channel_daily_metric
from src.db.supabase_client import get_supabase
from src.services.public_video_scraper import PublicVideoMetrics, extract_urls, scrape_public_video
from src.services.sheets_service import queue_marketing_daily_export

router = Router(name="public_scrape")
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🔎 <b>Бесплатный public scrape</b>\n\n"
    "Отправь ссылку на TikTok или Instagram Reel/Post.\n\n"
    "<b>Пример:</b>\n"
    "<code>https://www.tiktok.com/@account/video/...</code>\n\n"
    "Я попробую достать публичные счётчики через yt-dlp: views, likes, comments, shares. "
    "Если платформа закроет доступ или попросит login/cookies — верну ошибку."
)


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}".replace(",", " ")


def _metric_date_from_upload_date(upload_date: str | None) -> str:
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return datetime.now().strftime("%Y-%m-%d")


def _build_result_text(metrics: PublicVideoMetrics, saved_daily: bool, saved_raw: bool) -> str:
    lines = [
        "✅ <b>Public scrape результат</b>" if saved_daily else "⚠️ <b>Метрики получены, но views нет</b>",
        "",
        f"Платформа: <b>{metrics.platform}</b>",
        f"Автор: <b>{metrics.uploader or '-'}</b>",
        f"Дата публикации: <b>{metrics.upload_date or '-'}</b>",
        f"Views: <b>{_format_optional_int(metrics.views)}</b>",
        f"Likes: <b>{_format_optional_int(metrics.likes)}</b>",
        f"Comments: <b>{_format_optional_int(metrics.comments)}</b>",
        f"Shares: <b>{_format_optional_int(metrics.shares)}</b>",
    ]
    if metrics.title:
        lines.extend(["", f"Название: <code>{metrics.title[:180]}</code>"])
    if saved_daily:
        lines.extend(["", "Записал в Supabase и поставил экспорт в <b>Marketing Daily</b>."])
    elif saved_raw:
        lines.extend(
            [
                "",
                "Сохранил в Supabase как raw scrape. В <b>Marketing Daily</b> не записываю, потому что нет views.",
            ]
        )
    return "\n".join(lines)


@router.message(Command("scrape"))
async def cmd_scrape(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    urls = extract_urls(text)
    if urls:
        await _scrape_url_message(message, urls[0])
        return

    await state.clear()
    await state.set_state(PublicScrapeMode.active)
    await message.answer(HELP_TEXT)


@router.message(PublicScrapeMode.active, F.text)
async def handle_scrape_text(message: Message, state: FSMContext) -> None:
    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer("Не вижу ссылку. Пришли URL TikTok или Instagram.")
        return
    await _scrape_url_message(message, urls[0])
    await state.clear()


async def _scrape_url_message(message: Message, url: str) -> None:
    progress = await message.answer("⏳ Пробую бесплатно достать публичные метрики...")
    try:
        metrics = await asyncio.to_thread(scrape_public_video, url)
    except Exception as exc:
        logger.exception("Public scrape failed")
        supabase = get_supabase()
        if supabase:
            user_id = message.from_user.id if message.from_user else None
            await asyncio.to_thread(
                insert_public_video_scrape,
                supabase,
                platform="Other",
                url=url,
                created_by_telegram_id=user_id,
                error=str(exc)[:1000],
            )
        await progress.edit_text(
            "❌ Не получилось достать метрики бесплатно.\n\n"
            f"Причина: <code>{str(exc)[:500]}</code>\n\n"
            "Ошибку сохранил в Supabase raw scrape log.\n\n"
            "Это нормально для Instagram/TikTok: иногда нужен login/cookies или платформа режет public access."
        )
        return

    saved_raw = False
    saved_daily = False
    supabase = get_supabase()
    user_id = message.from_user.id if message.from_user else None
    if supabase:
        await asyncio.to_thread(
            insert_public_video_scrape,
            supabase,
            platform=metrics.platform,
            url=metrics.url,
            raw_id=metrics.raw_id,
            title=metrics.title,
            uploader=metrics.uploader,
            upload_date=metrics.upload_date,
            views=metrics.views,
            likes=metrics.likes,
            comments=metrics.comments,
            shares=metrics.shares,
            created_by_telegram_id=user_id,
        )
        saved_raw = True

    if metrics.views is not None and metrics.platform in {"TikTok", "Instagram"}:
        if supabase:
            metric_date = _metric_date_from_upload_date(metrics.upload_date)
            account_name = metrics.uploader or "unknown"
            saved_row = await asyncio.to_thread(
                upsert_channel_daily_metric,
                supabase,
                metric_date=metric_date,
                platform=metrics.platform,
                account_name=account_name,
                views=metrics.views,
                likes=metrics.likes,
                comments=metrics.comments,
                shares=metrics.shares,
                source="public_scrape",
                raw_text=metrics.url,
                created_by_telegram_id=user_id,
            )
            queue_marketing_daily_export(saved_row)
            saved_daily = True

    await progress.edit_text(_build_result_text(metrics, saved_daily, saved_raw))
