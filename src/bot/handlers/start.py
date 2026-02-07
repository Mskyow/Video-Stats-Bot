"""
Команды /start, /help и /stats.
"""
from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.db.repositories.videos import get_user_stats_summary
from src.db.supabase_client import get_supabase

router = Router(name="start")

START_TEXT = (
    "👋 <b>Привет! Я — Creator Copilot.</b>\n\n"
    "Анализирую метрики видео из TikTok, Instagram Reels и YouTube Shorts. "
    "Скажу, что работает, что нет — и что делать дальше.\n\n"
    "<b>Команды:</b>\n"
    "📸 Отправь скриншоты аналитики — получишь разбор\n"
    "/help — как пользоваться ботом\n"
    "/stats — твоя статистика\n"
    "/day_stats — отчёт за 24 часа\n"
    "/all_stats — общая статистика"
)

HELP_TEXT = (
    "<b>📖 Как пользоваться</b>\n\n"
    "<b>Что отправлять:</b>\n"
    "Скриншоты аналитики из TikTok, YouTube Studio или Instagram Insights. "
    "Лучше всего работают 2 скриншота: Обзор (метрики) + График удержания.\n\n"
    "<b>Что анализирую:</b>\n"
    "• Hook (3 сек) — цепляет ли начало\n"
    "• Retention — как долго смотрят\n"
    "• Engagement — лайки, комменты, репосты\n\n"
    "<b>Бенчмарки (норма для шортс):</b>\n"
    "Hook (3с): 🔴 &lt;55% (Плохо) | 🟡 55-70% (Норм) | 🟢 70%+ (Хорошо)\n"
    "Досмотр: 🔴 &lt;40% (Плохо) | 🟡 40-70% (Норм) | 🟢 70%+ (Хорошо)\n"
    "ER (вовлечение): 🔴 &lt;5% (Плохо) | 🟡 5-10% (Норм) | 🟢 10%+ (Хорошо)\n"
    "Репосты: 🔴 &lt;0.5% | 🟡 0.5-1.5% | 🟢 1.5%+\n"
    "Сохранения: 🔴 &lt;1% | 🟡 1-3% | 🟢 3%+\n\n"
    "<b>Вердикты:</b>\n"
    "🔴 KILL — не трать время, идея не зашла\n"
    "🟡 ITERATE — есть потенциал, доработай\n"
    "🟢 SCALE — отлично, делай больше таких"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Показывает сводную статистику анализов пользователя."""
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        await message.answer("Не удалось определить пользователя.")
        return

    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return

    stats = await asyncio.to_thread(get_user_stats_summary, supabase, user_id)

    if not stats or stats.get("total", 0) == 0:
        await message.answer(
            "📊 Пока нет анализов.\n\n"
            "Отправь скриншоты метрик видео — начну считать статистику."
        )
        return

    total = stats["total"]
    avg = stats.get("avg_score", 0)
    verdicts = stats.get("verdicts", {})
    platforms = stats.get("platforms", {})
    hook_stats = stats.get("hook_stats", {})

    lines = [
        f"📊 <b>Твоя статистика</b>",
        "",
        f"📋 Анализов: <b>{total}</b>",
        f"📈 Средний балл: <b>{avg}/100</b>",
    ]

    if verdicts:
        lines.append("")
        lines.append("<b>Вердикты:</b>")
        for v, count in sorted(verdicts.items(), key=lambda x: -x[1]):
            lines.append(f"  {v}: {count}")

    if platforms:
        lines.append("")
        lines.append("<b>Платформы:</b>")
        for p, count in sorted(platforms.items(), key=lambda x: -x[1]):
            lines.append(f"  {p.upper()}: {count}")

    await message.answer("\n".join(lines))
