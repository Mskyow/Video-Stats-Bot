"""
Commands /start, /help, /mode and /stats.
"""
from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.states import FunnelUploadMode
from src import config
from src.db.repositories.users import (
    authorize_user,
    get_or_create_user,
    get_screenshots_mode,
    is_user_authorized,
    set_screenshots_mode,
)
from src.db.repositories.videos import get_user_stats_summary
from src.db.supabase_client import get_supabase

router = Router(name="start")


def main_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📸 Как загрузить скрины", callback_data="help_screens"),
                InlineKeyboardButton(text="📄 Как импортировать CSV", callback_data="help_csv"),
            ],
            [
                InlineKeyboardButton(text="📊 Воронка по скринам", callback_data="start_funnel_upload"),
                InlineKeyboardButton(text="⚙️ Режим 2/3 скрина", callback_data="help_mode"),
            ],
            [
                InlineKeyboardButton(text="🔌 API / Sources", callback_data="help_sources"),
            ],
        ]
    )


def help_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main_menu")]
        ]
    )


def screenshots_mode_keyboard(current: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
        ]
    )


WELCOME_UNAUTHORIZED_TEXT = (
    "👋 <b>Привет!</b>\n\n"
    "Я помогаю вести две вещи:\n"
    "• разбор роликов по скринам\n"
    "• обновление маркетинговой воронки по CSV и API-источникам\n\n"
    "🔐 Для доступа введи:\n"
    "<code>/start КОДОВОЕ_СЛОВО</code>\n\n"
    "Кодовое слово узнай у администратора."
)

START_TEXT = (
    "👋 <b>Creator Copilot</b>\n\n"
    "<b>Что делает бот:</b>\n"
    "• <b>Скрины роликов</b> -> лист <b>Video Analysis</b>\n"
    "• <b>CSV воронки</b> -> лист <b>Marketing Funnels</b>\n"
    "• <b>Скрины воронки</b> -> полу-ручное обновление <b>Marketing Funnels</b>\n"
    "• <b>API / Sources</b> -> проверка готовности App Store и Google Play\n\n"
    "<b>Быстрый старт:</b>\n"
    "1. Для скринов: <code>/upload</code>\n"
    "2. Для воронки по скринам: <code>/upload_funnel</code>\n"
    "3. Для CSV: <code>/import_csv</code>\n"
    "4. Для API-источников: <code>/sources</code>\n"
    "5. Выйти из режима скринов: <code>/done</code>\n\n"
    "<b>Остальные команды:</b>\n"
    "/stats, /day_stats, /all_stats, /help, /sync_funnels"
)

HELP_TEXT = (
    "<b>Как пользоваться ботом</b>\n\n"
    "<b>Скрины роликов</b>\n"
    "1. <code>/upload</code>\n"
    "2. Отправь скрины статистики\n"
    "3. <code>/done</code>\n"
    "Результат: запись в <b>Video Analysis</b> и обновление viral views в <b>Marketing Funnels</b>.\n\n"
    "<b>Скрины воронки</b>\n"
    "1. <code>/upload_funnel</code>\n"
    "2. Отправь дневные скрины App Store / Google Play / Adapty\n"
    "Результат: бот с помощью AI распознаёт значения и обновляет <b>Marketing Funnels</b>.\n\n"
    "<b>CSV для воронки</b>\n"
    "1. <code>/import_csv</code>\n"
    "2. Отправь CSV в личку боту\n"
    "Результат: upsert строк в <b>Marketing Funnels</b> по ключу Date + Channel + Store.\n\n"
    "<b>API / Sources</b>\n"
    "1. <code>/sources</code> — проверить готовность App Store / Google Play\n"
    "2. <code>/sync_funnels</code> — посмотреть текущий статус автосбора\n\n"
    "👇 <b>Полезные материалы:</b>"
)

SCREENSHOTS_HELP_TEXT = (
    "📸 <b>Скрины роликов</b>\n\n"
    "1. Нажми <code>/upload</code>\n"
    "2. Отправляй скрины статистики роликов\n"
    "3. Когда закончил — <code>/done</code>\n\n"
    "Лист: <b>Video Analysis</b>"
)

CSV_QUICK_HELP_TEXT = (
    "📄 <b>CSV для воронки</b>\n\n"
    "1. Нажми <code>/import_csv</code>\n"
    "2. Отправь CSV-файл в личку боту\n\n"
    "Лист: <b>Marketing Funnels</b>\n"
    "Ключ обновления: <code>Date + Channel + Store</code>"
)

FUNNEL_SCREENSHOTS_HELP_TEXT = (
    "📊 <b>Воронка по скринам</b>\n\n"
    "Бот будет <b>с помощью AI</b> распознавать числа со скринов и обновлять лист <b>Marketing Funnels</b>.\n\n"
    "<b>План батча на 1 день:</b>\n"
    "1. App Store — Search Impressions\n"
    "2. App Store — Product Page Views\n"
    "3. App Store — Installs\n"
    "4. Google Play — Product Page Views\n"
    "5. Google Play — Installs\n"
    "6. Adapty — Purchases (все сторы)\n\n"
    "Команда входа: <code>/upload_funnel</code>\n"
    "Бот ждёт ровно 6 скринов за один день и после шестого сам запускает AI-распознавание."
)

MODE_HELP_TEXT = (
    "⚙️ <b>Режим скринов</b>\n\n"
    "<b>2 скрина</b> — Overview + Retention\n"
    "<b>3 скрина</b> — если нужен ещё retention-after-core\n\n"
    "Сменить режим: <code>/mode</code>"
)

SOURCES_HELP_TEXT = (
    "🔌 <b>API / Sources</b>\n\n"
    "Команда <code>/sources</code> показывает, готовы ли App Store Connect и Google Play.\n"
    "Команда <code>/sync_funnels</code> показывает текущий статус автосбора.\n\n"
    "Если источник ещё не подключён, CSV остаётся fallback-вариантом."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        await message.answer("Не удалось определить пользователя.")
        return

    supabase = get_supabase()
    get_or_create_user(supabase, user.id, user.username)

    args = message.text.split(maxsplit=1) if message.text else []
    provided_code = args[1].strip() if len(args) > 1 else None

    if is_user_authorized(supabase, user.id):
        await message.answer(START_TEXT, reply_markup=main_actions_keyboard())
        return

    if config.AUTH_SECRET and provided_code == config.AUTH_SECRET:
        if authorize_user(supabase, user.id):
            await message.answer(
                "✅ <b>Доступ разрешён!</b>\n\n" + START_TEXT,
                reply_markup=main_actions_keyboard(),
            )
        else:
            await message.answer("⚠️ Ошибка авторизации. Попробуй позже или обратись к администратору.")
        return

    if config.AUTH_SECRET:
        await message.answer(WELCOME_UNAUTHORIZED_TEXT)
    else:
        authorize_user(supabase, user.id)
        await message.answer(START_TEXT, reply_markup=main_actions_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📸 Скрины", callback_data="help_screens"),
                InlineKeyboardButton(text="📄 CSV", callback_data="help_csv"),
            ],
            [
                InlineKeyboardButton(text="📊 Воронка-скрины", callback_data="help_funnel_screens"),
                InlineKeyboardButton(text="⚙️ Режим 2/3", callback_data="help_mode"),
            ],
            [
                InlineKeyboardButton(text="🔌 API / Sources", callback_data="help_sources"),
            ],
            [
                InlineKeyboardButton(
                    text="📚 Бенчмарки и оценка",
                    url="https://www.notion.so/3031199f0c2480c98ef3fbb036702cc4?source=copy_link",
                )
            ],
        ]
    )
    await message.answer(HELP_TEXT, reply_markup=keyboard)


@router.callback_query(lambda c: c.data in ("help_screens", "help_csv", "help_funnel_screens", "help_mode", "help_sources"))
async def cb_quick_help(callback: CallbackQuery) -> None:
    if callback.data == "help_screens":
        text = SCREENSHOTS_HELP_TEXT
    elif callback.data == "help_csv":
        text = CSV_QUICK_HELP_TEXT
    elif callback.data == "help_funnel_screens":
        text = FUNNEL_SCREENSHOTS_HELP_TEXT
    elif callback.data == "help_sources":
        text = SOURCES_HELP_TEXT
    else:
        text = MODE_HELP_TEXT
    if callback.message:
        await callback.message.answer(text, reply_markup=help_back_keyboard())
    await callback.answer("Открыто")


@router.callback_query(lambda c: c.data == "start_funnel_upload")
async def cb_start_funnel_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(FunnelUploadMode.active)
    await state.update_data(funnel_photos=[])
    if callback.message:
        await callback.message.answer(FUNNEL_SCREENSHOTS_HELP_TEXT, reply_markup=help_back_keyboard())
    await callback.answer("Режим воронки включён")


@router.callback_query(lambda c: c.data == "back_to_main_menu")
async def cb_back_to_main_menu(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(START_TEXT, reply_markup=main_actions_keyboard())
    await callback.answer("Готово")


@router.message(Command("upload_funnel"))
async def cmd_upload_funnel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(FunnelUploadMode.active)
    await state.update_data(funnel_photos=[])
    await message.answer(FUNNEL_SCREENSHOTS_HELP_TEXT)


@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
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
    try:
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=screenshots_mode_keyboard(target))
    except Exception:
        pass


ESTIMATED_TOKENS_PER_VIDEO = 9200
ESTIMATED_COST_USD_PER_VIDEO = 0.017


def _normalize_platform_for_stats(raw_platform: str) -> str:
    p = (raw_platform or "").lower()
    if "tiktok" in p:
        return "TikTok"
    if "reels" in p or "instagram" in p:
        return "Instagram"
    return raw_platform or "Другое"


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
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

    platform_counts: dict[str, int] = {}
    for platform_name, count in platforms_raw.items():
        name = _normalize_platform_for_stats(platform_name)
        platform_counts[name] = platform_counts.get(name, 0) + count

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

    for platform_name in ("TikTok", "Instagram", "Другое"):
        count = platform_counts.get(platform_name, 0)
        if count > 0:
            lines.append(f"   • {platform_name}: <b>{count}</b>")

    lines.extend(
        [
            "",
            f"📈 Средний балл: <b>{avg}/10</b>",
            "",
            "🤖 <b>AI (оценка по OpenRouter):</b>",
            f"   • ~{avg_tokens:,} токенов на ролик".replace(",", " "),
            f"   • ~${cost_per_video:.3f} за ролик",
            f"   • Итого за {total} видео: ~${total_cost_est:.2f}",
        ]
    )

    await message.answer("\n".join(lines))
