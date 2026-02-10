"""
Команды /start, /help и /stats.
"""
from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src import config
from src.db.repositories.users import get_or_create_user, is_user_authorized, authorize_user
from src.db.repositories.videos import get_user_stats_summary
from src.db.supabase_client import get_supabase

router = Router(name="start")

START_TEXT = (
    "👋 <b>Привет! Я — Creator Copilot.</b>\n\n"
    "С помощью искусственного интеллекта я анализирую скриншоты со статистикой твоих видео (TikTok, Instagram Reels).\n\n"
    "Я автоматически извлекаю все важные метрики, добавляю их в базу данных и Google Sheets, чтобы ты мог видеть полную картину своего контента.\n\n"
    "<b>Команды:</b>\n"
    "/upload — включить режим загрузки скриншотов\n"
    "/done — завершить загрузку скриншотов\n"
    "/stats — посмотреть сводную статистику\n"
    "/day_stats — отчет за последние 24 часа\n"
    "/all_stats — общая статистика по всем видео\n"
    "/help — инструкция по использованию"
)

HELP_TEXT = (
    "<b>🚀 Как пользоваться ботом?</b>\n\n"
    "Всё очень просто:\n\n"
    "1️⃣ <b>Нажми /upload</b>\n"
    "Бот перейдет в режим ожидания скриншотов.\n\n"
    "2️⃣ <b>Отправь скриншоты статистики</b>\n"
    "Просто скинь скриншоты метрик из TikTok или Instagram. Я сам распознаю, где какая платформа и какие цифры важны.\n\n"
    "3️⃣ <b>Готово!</b>\n"
    "Я обработаю данные, оценю видео по 10-балльной шкале, дам рекомендации и сохраню всё в таблицу.\n\n"
    "Когда закончишь загружать — нажми /done, чтобы выйти из режима загрузки.\n\n"
    "👇 <b>Полезные материалы:</b>"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Обработка /start с опциональным кодовым словом."""
    user = message.from_user
    if not user:
        await message.answer("Не удалось определить пользователя.")
        return

    supabase = get_supabase()
    user_data = get_or_create_user(supabase, user.id, user.username)

    # Получаем текст после команды (кодовое слово)
    args = message.text.split(maxsplit=1) if message.text else []
    provided_code = args[1].strip() if len(args) > 1 else None

    # Проверяем, авторизован ли уже пользователь
    if is_user_authorized(supabase, user.id):
        await message.answer(START_TEXT)
        return

    # Проверяем кодовое слово
    if config.AUTH_SECRET and provided_code == config.AUTH_SECRET:
        if authorize_user(supabase, user.id):
            await message.answer(
                "✅ <b>Доступ разрешён!</b>\n\n" + START_TEXT
            )
        else:
            await message.answer(
                "⚠️ Ошибка авторизации. Попробуй позже или обратись к администратору."
            )
        return

    # Не авторизован и нет/неверный код
    if config.AUTH_SECRET:
        await message.answer(
            "🔒 <b>Бот защищён кодовым словом.</b>\n\n"
            "Для доступа отправь:\n"
            "<code>/start КОДОВОЕ_СЛОВО</code>\n\n"
            "Или обратись к администратору."
        )
    else:
        # Если код не настроен — разрешаем доступ
        authorize_user(supabase, user.id)
        await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Бенчмарки и расчёт оценки", url="https://www.notion.so/3031199f0c2480c98ef3fbb036702cc4?source=copy_link")]
    ])
    await message.answer(HELP_TEXT, reply_markup=keyboard)


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
