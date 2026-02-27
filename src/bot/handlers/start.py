"""
Команды /start, /help и /stats.
"""
from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from src import config
from src.db.repositories.users import (
    get_or_create_user,
    is_user_authorized,
    authorize_user,
    get_screenshots_mode,
    set_screenshots_mode,
)
from src.db.repositories.videos import get_user_stats_summary
from src.db.supabase_client import get_supabase

router = Router(name="start")

# Режим скриншотов: 2 или 3 на одно видео (используется в start и upload)
def screenshots_mode_keyboard(current: str) -> InlineKeyboardMarkup:
    """Клавиатура переключения режима: 2 или 3 скриншота."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📸 2 скриншота" + (" ✓" if current == "2" else ""),
                callback_data="mode_2",
            ),
            InlineKeyboardButton(
                text="📸 3 скриншота" + (" ✓" if current == "3" else ""),
                callback_data="mode_3",
            ),
        ],
    ])

WELCOME_UNAUTHORIZED_TEXT = (
    "👋 <b>Привет!</b>\n\n"
    "🤖 Я — <b>Creator Copilot</b>. С помощью искусственного интеллекта я анализирую скриншоты со статистикой твоих видео — <b>TikTok</b> и <b>Instagram Reels</b>.\n\n"
    "🔐 Для доступа введи команду:\n"
    "<code>/start КОДОВОЕ_СЛОВО</code>\n\n"
    "Кодовое слово узнай у администратора."
)

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
                "✅ <b>Доступ разрешён!</b>\n\n" + START_TEXT,
            )
        else:
            await message.answer(
                "⚠️ Ошибка авторизации. Попробуй позже или обратись к администратору."
            )
        return

    # Не авторизован и нет/неверный код
    if config.AUTH_SECRET:
        await message.answer(WELCOME_UNAUTHORIZED_TEXT)
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


@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    """Показать текущий режим скриншотов и кнопки переключения."""
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        await message.answer("Не удалось определить пользователя.")
        return
    supabase = get_supabase()
    if not supabase:
        await message.answer("БД недоступна.")
        return
    current = get_screenshots_mode(supabase, user_id)
    label = "2 скриншота (Обзор + Удержание)" if current == "2" else "3 скриншота"
    await message.answer(
        f"📸 <b>Режим скриншотов</b>\n\nСейчас: <b>{label}</b>\n\nВыбери режим:",
        reply_markup=screenshots_mode_keyboard(current),
    )


@router.callback_query(lambda c: c.data in ("mode_2", "mode_3"))
async def cb_screenshots_mode(callback: CallbackQuery) -> None:
    """Переключение режима 2/3 скриншота и подтверждение."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not user_id:
        await callback.answer("Ошибка: пользователь не определён.")
        return
    target = "3" if callback.data == "mode_3" else "2"
    supabase = get_supabase()
    if not supabase:
        await callback.answer("БД недоступна.")
        return
    ok = set_screenshots_mode(supabase, user_id, target)
    if not ok:
        await callback.answer("Не удалось сохранить режим.")
        return
    label = "2 скриншота (Обзор + Удержание)" if target == "2" else "3 скриншота"
    await callback.answer(f"Режим переключён на {label}")
    # Обновляем только клавиатуру (сообщение может быть /start или /mode)
    try:
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=screenshots_mode_keyboard(target))
    except Exception:
        pass


# Оценка токенов и стоимости AI на один анализ ролика (2 вызова: extraction + scoring)
# Данные из OpenRouter (Gemini 3 Flash): ~9.2k токенов, ~$0.017 на ролик
ESTIMATED_TOKENS_PER_VIDEO = 9200
ESTIMATED_COST_USD_PER_VIDEO = 0.017


def _normalize_platform_for_stats(raw_platform: str) -> str:
    """Приводит platform из БД к читаемому названию: TikTok / Instagram."""
    p = (raw_platform or "").lower()
    if "tiktok" in p:
        return "TikTok"
    if "reels" in p or "instagram" in p:
        return "Instagram"
    return raw_platform or "Другое"


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
    platforms_raw = stats.get("platforms", {})

    # Считаем по платформам: TikTok и Instagram (reels)
    platform_counts: dict[str, int] = {}
    for p, count in platforms_raw.items():
        name = _normalize_platform_for_stats(p)
        platform_counts[name] = platform_counts.get(name, 0) + count

    # Оценка токенов и стоимости на основе реальных данных OpenRouter
    avg_tokens = ESTIMATED_TOKENS_PER_VIDEO
    cost_per_video = ESTIMATED_COST_USD_PER_VIDEO
    total_cost_est = total * cost_per_video

    lines = [
        "📊 <b>Твоя статистика</b>",
        "",
        f"📋 Всего видео: <b>{total}</b>",
        "",
        "📱 <b>По платформам:</b>",
    ]

    for plat in ("TikTok", "Instagram", "Другое"):
        cnt = platform_counts.get(plat, 0)
        if cnt > 0:
            lines.append(f"   • {plat}: <b>{cnt}</b>")

    lines.extend([
        "",
        f"📈 Средний балл: <b>{avg}/10</b>",
        "",
        "🤖 <b>AI (оценка по OpenRouter):</b>",
        f"   • ~{avg_tokens:,} токенов на ролик".replace(",", " "),
        f"   • ~${cost_per_video:.3f} за ролик",
        f"   • Итого за {total} видео: ~${total_cost_est:.2f}",
    ])

    await message.answer("\n".join(lines))
