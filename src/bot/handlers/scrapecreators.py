from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.marketing import (
    list_latest_previous_video_snapshots,
    upsert_channel_daily_metric,
    upsert_social_video_snapshot,
)
from src.db.supabase_client import get_supabase
from src.services.scrapecreators_service import (
    ScrapeCreatorsClient,
    ScrapeCreatorsError,
    SocialVideoMetric,
    calculate_view_deltas,
)
from src.services.sheets_service import queue_marketing_daily_export
from src.services.social_scrape_collector import (
    SocialScrapeResult,
    collect_configured_social_accounts,
)

router = Router(name="scrapecreators")
logger = logging.getLogger(__name__)


HELP_TEXT = (
    "Формат:\n"
    "<code>/sc_check instagram sarah.mitchell13</code>\n"
    "<code>/sc_check tiktok eli_robinsonn</code>\n\n"
    "Для записи snapshot:\n"
    "<code>/sc_collect instagram sarah.mitchell13</code>\n"
    "<code>/sc_collect tiktok eli_robinsonn</code>\n\n"
    "Все настроенные аккаунты:\n"
    "<code>/sc_collect_all</code>\n\n"
    "Первый collect сохраняет baseline. Daily views считаются со второго snapshot."
)


def format_configured_collection_results(results: list[SocialScrapeResult]) -> str:
    lines = ["📡 <b>Social scrape</b>", ""]
    for result in results:
        if result.status == "success":
            lines.extend(
                [
                    f"✅ <b>{result.display_name}</b> — {result.platform}",
                    f"Аккаунт: <b>@{result.handle}</b>",
                    f"API-страниц: <b>{result.pages_requested}</b>",
                    f"Роликов в границе: <b>{result.videos_saved}</b>",
                    f"Lifetime views: <b>{result.total_lifetime_views:,}</b>".replace(",", " "),
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"❌ <b>{result.display_name}</b> — {result.platform}",
                    f"Ошибка: <code>{result.error or 'unknown'}</code>",
                    "",
                ]
            )
    return "\n".join(lines).rstrip()


def _parse_args(message: Message) -> tuple[str, str] | None:
    parts = (message.text or "").split()
    if len(parts) < 3:
        return None
    return parts[1], parts[2].strip()


def _summary_text(platform: str, handle: str, videos: list[SocialVideoMetric]) -> str:
    total_views = sum(item.views or 0 for item in videos)
    lines = [
        "✅ <b>ScrapeCreators check</b>",
        "",
        f"Платформа: <b>{platform}</b>",
        f"Аккаунт: <b>@{handle.strip().removeprefix('@')}</b>",
        f"Получено роликов: <b>{len(videos)}</b>",
        f"Сумма lifetime views в ответе: <b>{total_views:,}</b>".replace(",", " "),
        "",
        "<b>Первые ролики:</b>",
    ]
    for item in videos[:5]:
        title = (item.title or "-").replace("\n", " ")
        if len(title) > 60:
            title = title[:57] + "..."
        lines.append(
            "• "
            f"views=<b>{item.views if item.views is not None else '-'}</b>, "
            f"likes={item.likes if item.likes is not None else '-'}, "
            f"comments={item.comments if item.comments is not None else '-'} — "
            f"{title}"
        )
    return "\n".join(lines)


def _fetch_videos(platform: str, handle: str) -> list[SocialVideoMetric]:
    return ScrapeCreatorsClient().fetch_account_videos(platform, handle)


@router.message(Command("sc_check"))
async def cmd_sc_check(message: Message) -> None:
    args = _parse_args(message)
    if not args:
        await message.answer(HELP_TEXT)
        return
    platform, handle = args
    try:
        videos = await asyncio.to_thread(_fetch_videos, platform, handle)
    except ScrapeCreatorsError as exc:
        await message.answer(f"❌ ScrapeCreators error:\n<code>{str(exc)}</code>")
        return
    except Exception:
        logger.exception("Unexpected ScrapeCreators check error")
        await message.answer("❌ Неожиданная ошибка ScrapeCreators. Посмотрю в логах.")
        return
    await message.answer(_summary_text(platform, handle, videos))


@router.message(Command("sc_collect_all"))
async def cmd_sc_collect_all(message: Message) -> None:
    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return
    await message.answer("⏳ Собираю все настроенные social-аккаунты…")
    results = await asyncio.to_thread(collect_configured_social_accounts, supabase)
    for result in results:
        if result.daily_metric:
            queue_marketing_daily_export(result.daily_metric)
    await message.answer(format_configured_collection_results(results))


@router.message(Command("sc_collect"))
async def cmd_sc_collect(message: Message) -> None:
    args = _parse_args(message)
    if not args:
        await message.answer(HELP_TEXT)
        return

    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return

    platform_arg, handle = args
    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    try:
        videos = await asyncio.to_thread(_fetch_videos, platform_arg, handle)
    except ScrapeCreatorsError as exc:
        await message.answer(f"❌ ScrapeCreators error:\n<code>{str(exc)}</code>")
        return
    except Exception:
        logger.exception("Unexpected ScrapeCreators collect error")
        await message.answer("❌ Неожиданная ошибка ScrapeCreators. Посмотрю в логах.")
        return

    if not videos:
        await message.answer("ScrapeCreators ответил, но роликов не нашёл.")
        return

    platform = videos[0].platform
    account_name = videos[0].account_name
    previous_by_video = await asyncio.to_thread(
        list_latest_previous_video_snapshots,
        supabase,
        platform=platform,
        account_name=account_name,
        before_date=snapshot_date,
    )

    saved_count = 0
    try:
        for item in videos:
            await asyncio.to_thread(
                upsert_social_video_snapshot,
                supabase,
                snapshot_date=snapshot_date,
                platform=item.platform,
                account_name=item.account_name,
                video_id=item.video_id,
                video_url=item.video_url,
                published_at=item.published_at,
                title=item.title,
                views=item.views,
                likes=item.likes,
                comments=item.comments,
                saves=item.saves,
                shares=item.shares,
                raw_json=item.raw_json,
            )
            saved_count += 1
    except Exception:
        logger.exception("Failed to save ScrapeCreators snapshots")
        await message.answer("❌ Получил данные, но не смог сохранить snapshots в Supabase.")
        return

    daily_views, matched_count = calculate_view_deltas(videos, previous_by_video)
    if matched_count <= 0:
        await message.answer(
            "✅ <b>Baseline сохранён</b>\n\n"
            f"Платформа: <b>{platform}</b>\n"
            f"Аккаунт: <b>{account_name}</b>\n"
            f"Snapshot date: <b>{snapshot_date}</b>\n"
            f"Сохранено роликов: <b>{saved_count}</b>\n\n"
            "Предыдущего snapshot ещё нет, поэтому дневную дельту пока считать нельзя. "
            "Следующий <code>/sc_collect</code> уже сможет посчитать прирост views."
        )
        return

    likes = sum(item.likes or 0 for item in videos)
    comments = sum(item.comments or 0 for item in videos)
    saves = sum(item.saves or 0 for item in videos)
    shares = sum(item.shares or 0 for item in videos)
    try:
        saved_metric = await asyncio.to_thread(
            upsert_channel_daily_metric,
            supabase,
            metric_date=snapshot_date,
            platform=platform,
            account_name=account_name,
            views=daily_views,
            likes=likes,
            comments=comments,
            saves=saves,
            shares=shares,
            source="scrapecreators_delta",
            raw_text=f"matched_videos={matched_count}; saved_snapshots={saved_count}",
            created_by_telegram_id=message.from_user.id if message.from_user else None,
        )
        queue_marketing_daily_export(saved_metric)
    except Exception:
        logger.exception("Failed to save ScrapeCreators daily metric")
        await message.answer("❌ Snapshots сохранил, но дневную метрику сохранить не смог.")
        return

    await message.answer(
        (
            "✅ <b>ScrapeCreators collect сохранён</b>\n\n"
            f"Платформа: <b>{platform}</b>\n"
            f"Аккаунт: <b>{account_name}</b>\n"
            f"Дата: <b>{snapshot_date}</b>\n"
            f"Сохранено snapshot роликов: <b>{saved_count}</b>\n"
            f"Роликов совпало с предыдущим snapshot: <b>{matched_count}</b>\n"
            f"Daily views delta: <b>{daily_views:,}</b>\n\n"
            "Записал в Supabase и поставил экспорт в <b>Marketing Daily</b>."
        ).replace(",", " ")
    )
