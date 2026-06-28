"""
Telegram commands for Instagram Graph API automation.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.marketing import upsert_channel_daily_metric
from src.db.supabase_client import get_supabase
from src.services.instagram_graph_api import (
    InstagramGraphError,
    check_instagram_graph_connection,
    collect_instagram_account_daily_metrics,
)
from src.services.sheets_service import queue_marketing_daily_export

router = Router(name="instagram_api")
logger = logging.getLogger(__name__)


def _fmt(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}".replace(",", " ")


@router.message(Command("ig_check"))
async def cmd_ig_check(message: Message) -> None:
    progress = await message.answer("⏳ Проверяю Instagram Graph API credentials...")
    try:
        profile = await asyncio.to_thread(check_instagram_graph_connection)
    except InstagramGraphError as exc:
        await progress.edit_text(
            "❌ Instagram Graph API пока не готов.\n\n"
            f"<code>{str(exc)[:800]}</code>\n\n"
            "Нужно задать в .env: <code>INSTAGRAM_ACCESS_TOKEN</code> и <code>INSTAGRAM_USER_ID</code>."
        )
        return
    except Exception:
        logger.exception("Unexpected Instagram Graph API check error")
        await progress.edit_text("❌ Ошибка проверки Instagram Graph API. Детали в логах.")
        return

    await progress.edit_text(
        "✅ <b>Instagram Graph API подключён</b>\n\n"
        f"ID: <code>{profile.get('id', '-')}</code>\n"
        f"Username: <b>{profile.get('username', '-')}</b>\n"
        f"Followers: <b>{_fmt(profile.get('followers_count'))}</b>\n"
        f"Media: <b>{_fmt(profile.get('media_count'))}</b>"
    )


@router.message(Command("ig_collect"))
async def cmd_ig_collect(message: Message) -> None:
    progress = await message.answer("⏳ Забираю Instagram views через Graph API...")
    try:
        metrics = await asyncio.to_thread(collect_instagram_account_daily_metrics)
    except InstagramGraphError as exc:
        await progress.edit_text(
            "❌ Instagram Graph API не отдал views.\n\n"
            f"<code>{str(exc)[:900]}</code>"
        )
        return
    except Exception:
        logger.exception("Unexpected Instagram Graph API collect error")
        await progress.edit_text("❌ Ошибка сбора Instagram Graph API. Детали в логах.")
        return

    usable_views = metrics.views if metrics.views is not None else metrics.impressions
    if usable_views is None:
        await progress.edit_text(
            "⚠️ Instagram API ответил, но views/impressions не нашёл.\n\n"
            f"Дата: <b>{metrics.metric_date}</b>\n"
            f"Views: <b>{_fmt(metrics.views)}</b>\n"
            f"Reach: <b>{_fmt(metrics.reach)}</b>\n"
            f"Impressions: <b>{_fmt(metrics.impressions)}</b>\n"
            f"Profile views: <b>{_fmt(metrics.profile_views)}</b>"
        )
        return

    supabase = get_supabase()
    if not supabase:
        await progress.edit_text("БД недоступна.")
        return

    user_id = message.from_user.id if message.from_user else None
    saved_row = await asyncio.to_thread(
        upsert_channel_daily_metric,
        supabase,
        metric_date=metrics.metric_date,
        platform="Instagram",
        account_name="total",
        views=usable_views,
        source="instagram_graph_api",
        raw_text=str(metrics.raw)[:5000],
        created_by_telegram_id=user_id,
    )
    queue_marketing_daily_export(saved_row)

    await progress.edit_text(
        "✅ <b>Instagram API metrics сохранены</b>\n\n"
        f"Дата: <b>{metrics.metric_date}</b>\n"
        f"Views: <b>{_fmt(metrics.views)}</b>\n"
        f"Reach: <b>{_fmt(metrics.reach)}</b>\n"
        f"Impressions fallback: <b>{_fmt(metrics.impressions)}</b>\n"
        f"Profile views: <b>{_fmt(metrics.profile_views)}</b>\n\n"
        "Записал в Supabase и поставил экспорт в <b>Marketing Daily</b>."
    )
