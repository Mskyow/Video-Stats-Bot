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
    "Я анализирую метрики твоих видео (TikTok, Reels, Shorts) "
    "на уровне Senior Growth Analyst.\n\n"
    "<b>Что я делаю:</b>\n"
    "📸 Принимаю скриншоты аналитики видео\n"
    "📊 Оцениваю Hook, Retention, Completion, Engagement\n"
    "🎯 Даю вердикт: KILL / ITERATE / SCALE HARD\n"
    "💡 Предлагаю конкретные действия для роста\n\n"
    "<b>Как использовать:</b>\n"
    "Просто отправь скриншот с метриками видео — я сделаю полный разбор.\n\n"
    "/help — справка\n"
    "/stats — твоя статистика анализов"
)

HELP_TEXT = (
    "<b>📖 Справка</b>\n\n"
    "Отправь скриншот аналитики видео (из TikTok, YouTube Studio, Instagram Insights).\n\n"
    "<b>Что должно быть на скриншоте:</b>\n"
    "• Основные метрики (просмотры, лайки, комментарии, репосты, сохранения)\n"
    "• Графики retention / engagement (если есть)\n"
    "• Overview панель аналитики\n\n"
    "<b>Мои бенчмарки:</b>\n"
    "• Hook (3s): 60%+ = Good, 70%+ = Viral Potential\n"
    "• Completion: зависит от длительности (60-90%+ для коротких)\n"
    "• Share Rate: 1.5%+ = Scale Signal\n\n"
    "<b>Вердикты:</b>\n"
    "🔴 KILL — контент не работает, не итерируй\n"
    "✂️ FIX BODY — хук ок, но тело видео проседает\n"
    "🟡 ITERATE — есть потенциал, доработай\n"
    "🚀 SCALE HARD — формат работает, масштабируй!"
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
            "📊 У тебя ещё нет анализов.\n"
            "Отправь скриншот с метриками видео, чтобы начать!"
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
        f"📋 Всего анализов: <b>{total}</b>",
        f"📈 Средний Score: <b>{avg}/100</b>",
        "",
    ]

    if hook_stats:
        lines.append("<b>📌 Hook (по записям):</b>")
        for h, count in sorted(hook_stats.items(), key=lambda x: -x[1]):
            if h != "unknown":
                lines.append(f"  {h}: {count}")
        if hook_stats.get("unknown"):
            lines.append(f"  без оценки: {hook_stats['unknown']}")
        lines.append("")

    if verdicts:
        lines.append("<b>Вердикты:</b>")
        for v, count in sorted(verdicts.items(), key=lambda x: -x[1]):
            lines.append(f"  {v}: {count}")
        lines.append("")

    if platforms:
        lines.append("<b>Платформы:</b>")
        for p, count in sorted(platforms.items(), key=lambda x: -x[1]):
            lines.append(f"  {p.upper()}: {count}")

    await message.answer("\n".join(lines))
