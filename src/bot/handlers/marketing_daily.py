"""
MVP flow for top-of-funnel daily marketing metrics.

Input example:
    24.06 TikTok 12000 Instagram 4300 YouTube 800

The goal is deliberately simple: record daily total views per acquisition channel,
without tying marketing funnel data to individual videos.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import dateparser
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.states import MarketingDailyMode
from src.config import REPORT_TIMEZONE
from src.db.repositories.marketing import list_channel_daily_metrics, upsert_channel_daily_metric
from src.db.supabase_client import get_supabase
from src.services.sheets_service import queue_marketing_daily_export

router = Router(name="marketing_daily")
logger = logging.getLogger(__name__)

PLATFORM_ALIASES: dict[str, str] = {
    "tiktok": "TikTok",
    "tik tok": "TikTok",
    "тикток": "TikTok",
    "тик ток": "TikTok",
    "instagram": "Instagram",
    "insta": "Instagram",
    "ig": "Instagram",
    "инстаграм": "Instagram",
    "youtube": "YouTube",
    "yt": "YouTube",
    "ютуб": "YouTube",
    "ютьюб": "YouTube",
}

PLATFORM_PATTERN = re.compile(
    r"\b(tik\s?tok|tiktok|тикток|тик\s?ток|instagram|insta|ig|инстаграм|youtube|yt|ютуб|ютьюб)\b",
    flags=re.IGNORECASE,
)

DATE_PATTERN = re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\b")
NUMBER_PATTERN = re.compile(r"(?<![\w@])(\d[\d\s.,]*)(?:\s*(k|к|тыс|m|млн))?", flags=re.IGNORECASE)


HELP_TEXT = (
    "📊 <b>Маркетинг за день</b>\n\n"
    "Отправь дневные просмотры по каналам одним сообщением.\n\n"
    "<b>Пример:</b>\n"
    "<code>24.06 TikTok 12000 Instagram 4300 YouTube 800</code>\n\n"
    "Можно по строкам:\n"
    "<code>TikTok 12 000\nInstagram 4 300\nYouTube 800</code>\n\n"
    "Пока MVP пишет только дневные totals по каналам: дата, платформа, account=total, views. "
    "Это пойдёт в Supabase и лист <b>Marketing Daily</b>."
)


def _normalize_platform(raw: str) -> str:
    key = re.sub(r"\s+", " ", raw.strip().lower())
    return PLATFORM_ALIASES.get(key, raw.strip().title())


def _parse_number(raw_number: str, suffix: str | None = None) -> int:
    cleaned = str(raw_number or "").strip().replace(" ", "").replace(",", ".")
    value = float(cleaned)
    normalized_suffix = str(suffix or "").strip().lower()
    if normalized_suffix in {"k", "к", "тыс"}:
        value *= 1_000
    elif normalized_suffix in {"m", "млн"}:
        value *= 1_000_000
    return int(round(value))


def _extract_metric_date(text: str) -> str:
    match = DATE_PATTERN.search(text)
    now = datetime.now()
    if not match:
        return now.strftime("%Y-%m-%d")

    raw = match.group(1)
    parsed = dateparser.parse(
        raw,
        settings={
            "DATE_ORDER": "DMY",
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": now,
            "TIMEZONE": REPORT_TIMEZONE,
        },
    )
    if not parsed:
        return now.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d")


def _extract_account(segment: str) -> str:
    match = re.search(r"@([A-Za-z0-9_.]+)", segment)
    if match:
        return "@" + match.group(1)
    return "total"


def parse_marketing_daily_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    metric_date = _extract_metric_date(text)
    matches = list(PLATFORM_PATTERN.finditer(text))
    rows: list[dict[str, Any]] = []

    for index, match in enumerate(matches):
        platform = _normalize_platform(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[start:end]

        number_match = NUMBER_PATTERN.search(segment)
        if not number_match:
            continue

        rows.append(
            {
                "metric_date": metric_date,
                "platform": platform,
                "account_name": _extract_account(segment),
                "views": _parse_number(number_match.group(1), number_match.group(2)),
                "source": "telegram_text",
                "raw_text": text.strip(),
            }
        )

    return metric_date, rows


def _build_saved_text(metric_date: str, saved_rows: list[dict[str, Any]]) -> str:
    total_views = sum(int(row.get("views") or 0) for row in saved_rows)
    lines = [
        "✅ <b>Маркетинг за день сохранён</b>",
        "",
        f"Дата: <b>{metric_date}</b>",
        f"Всего views: <b>{total_views:,}</b>".replace(",", " "),
        "",
    ]
    for row in saved_rows:
        platform = row.get("platform") or "-"
        account = row.get("account_name") or "total"
        views = int(row.get("views") or 0)
        lines.append(f"• {platform} / {account}: <b>{views:,}</b>".replace(",", " "))
    lines.extend(["", "Записал в Supabase и поставил экспорт в Google Sheets: <b>Marketing Daily</b>."])
    return "\n".join(lines)


@router.message(Command("marketing"))
async def cmd_marketing(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MarketingDailyMode.active)
    await message.answer(HELP_TEXT)


@router.message(Command("marketing_today"))
async def cmd_marketing_today(message: Message) -> None:
    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return
    metric_date = datetime.now().strftime("%Y-%m-%d")
    rows = await asyncio.to_thread(list_channel_daily_metrics, supabase, metric_date=metric_date)
    if not rows:
        await message.answer(f"За <b>{metric_date}</b> маркетинговых totals пока нет.")
        return
    await message.answer(_build_saved_text(metric_date, rows))


@router.message(MarketingDailyMode.active, F.text)
async def handle_marketing_daily_text(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return

    metric_date, rows = parse_marketing_daily_text(text)
    if not rows:
        await message.answer(
            "Не нашёл платформы и просмотры.\n\n"
            "Отправь в таком формате:\n"
            "<code>24.06 TikTok 12000 Instagram 4300 YouTube 800</code>"
        )
        return

    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return

    user_id = message.from_user.id if message.from_user else None
    saved_rows: list[dict[str, Any]] = []
    try:
        for row in rows:
            saved = await asyncio.to_thread(
                upsert_channel_daily_metric,
                supabase,
                metric_date=row["metric_date"],
                platform=row["platform"],
                account_name=row["account_name"],
                views=row["views"],
                source=row["source"],
                raw_text=row["raw_text"],
                created_by_telegram_id=user_id,
            )
            saved_rows.append(saved)
            queue_marketing_daily_export(saved)
    except Exception:
        logger.exception("Failed to save marketing daily metrics")
        await message.answer("❌ Не удалось сохранить маркетинговые totals. Посмотрю ошибку в логах.")
        return

    await state.clear()
    await message.answer(_build_saved_text(metric_date, saved_rows))
