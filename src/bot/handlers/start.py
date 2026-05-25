"""
Commands /start, /help, /mode and /stats.
"""
from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src import config
from src.bot.states import FunnelUploadMode, UploadMode, YouTubeUploadMode
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
                InlineKeyboardButton(text="📸 TikTok / Instagram", callback_data="start_video_upload"),
                InlineKeyboardButton(text="▶️ YouTube", callback_data="start_youtube_upload"),
            ],
            [
                InlineKeyboardButton(text="📊 Воронка по скринам", callback_data="start_funnel_upload"),
                InlineKeyboardButton(text="📄 Импорт CSV", callback_data="help_csv"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Режим 2/3 скрина", callback_data="help_mode"),
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
                    text="📸 2 скрина" + (" ✓" if current == "2" else ""),
                    callback_data="mode_2",
                ),
                InlineKeyboardButton(
                    text="📸 3 скрина" + (" ✓" if current == "3" else ""),
                    callback_data="mode_3",
                ),
            ],
        ]
    )


WELCOME_UNAUTHORIZED_TEXT = (
    "👋 <b>Привет!</b>\n\n"
    "Бот помогает:\n"
    "• загружать скрины видео в <b>Video Analysis</b>\n"
    "• обновлять <b>Marketing Funnels</b> через скрины, CSV и API-источники\n\n"
    "Для доступа введи:\n"
    "<code>/start КОДОВОЕ_СЛОВО</code>"
)

START_TEXT = (
    "👋 <b>Creator Copilot</b>\n\n"
    "<b>Что умеет бот:</b>\n"
    "• TikTok / Instagram скрины → <b>Video Analysis</b>\n"
    "• YouTube скрины → <b>Video Analysis</b>\n"
    "• CSV воронки → <b>Marketing Funnels</b>\n"
    "• Скрины воронки → <b>Marketing Funnels</b>\n"
    "• API / Sources → проверка готовности App Store и Google Play\n\n"
    "<b>Быстрый старт:</b>\n"
    "1. TikTok / Instagram: <code>/upload</code>\n"
    "2. YouTube: <code>/upload_youtube</code>\n"
    "3. Скрины воронки: <code>/upload_funnel</code>\n"
    "4. CSV: <code>/import_csv</code>\n"
    "5. Завершить режим загрузки: <code>/done</code>\n\n"
    "<b>Остальные команды:</b>\n"
    "/stats, /day_stats, /all_stats, /help, /sync_funnels"
)

HELP_TEXT = (
    "<b>Как пользоваться ботом</b>\n\n"
    "<b>TikTok / Instagram</b>\n"
    "1. Нажми <code>/upload</code>\n"
    "2. Отправь скрины в правильном порядке\n"
    "3. Обычно это 2 скрина на одно видео: Overview + Retention\n"
    "4. Можно отправить сразу много фото одним альбомом\n"
    "5. Бот режет их подряд на пары\n\n"
    "<b>YouTube</b>\n"
    "1. Нажми <code>/upload_youtube</code>\n"
    "2. Отправь один или несколько YouTube-скринов\n"
    "3. Каждый скрин = отдельное видео\n\n"
    "<b>Скрины воронки</b>\n"
    "1. Нажми <code>/upload_funnel</code>\n"
    "2. Отправь 6 скринов за один день\n"
    "3. Бот распознает значения и обновит <b>Marketing Funnels</b>\n\n"
    "<b>CSV для воронки</b>\n"
    "1. Нажми <code>/import_csv</code>\n"
    "2. Отправь CSV в личку боту\n"
    "3. Бот сделает upsert по ключу <code>Date + Channel + Store</code>"
)

SCREENSHOTS_HELP_TEXT = (
    "📸 <b>TikTok / Instagram</b>\n\n"
    "Идеальный сценарий:\n"
    "1. Нажми <code>/upload</code>\n"
    "2. Открой галерею\n"
    "3. Выбери скрины в правильном порядке\n"
    "4. Отправь их одним альбомом\n\n"
    "Важно:\n"
    "• 2 скрина = 1 видео\n"
    "• порядок внутри альбома критичен\n"
    "• бот разобьёт пачку как 1-2, 3-4, 5-6 и так далее\n\n"
    "Лист: <b>Video Analysis</b>"
)

YOUTUBE_HELP_TEXT = (
    "▶️ <b>YouTube</b>\n\n"
    "Идеальный сценарий:\n"
    "1. Нажми <code>/upload_youtube</code>\n"
    "2. Открой галерею\n"
    "3. Выбери один или несколько YouTube-скринов\n"
    "4. Отправь их одним альбомом или по одному\n\n"
    "Важно:\n"
    "• 1 скрин = 1 видео\n"
    "• можно грузить сразу пачку\n"
    "• бот обработает каждый скрин отдельно\n\n"
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
    "Бот распознаёт значения со скринов и обновляет лист <b>Marketing Funnels</b>.\n\n"
    "<b>Порядок батча на 1 день:</b>\n"
    "1. App Store — Search Impressions\n"
    "2. App Store — Product Page Views\n"
    "3. App Store — Installs\n"
    "4. Google Play — Product Page Views\n"
    "5. Google Play — Installs\n"
    "6. Adapty — Purchases (all stores)\n\n"
    "Команда входа: <code>/upload_funnel</code>"
)

MODE_HELP_TEXT = (
    "⚙️ <b>Режим 2/3 скрина</b>\n\n"
    "<b>2 скрина</b> — стандартный сценарий для TikTok / Instagram: Overview + Retention\n"
    "<b>3 скрина</b> — если нужен дополнительный retention-after-core\n\n"
    "Команда: <code>/mode</code>\n"
    "Этот режим не влияет на YouTube. Для YouTube всегда используется 1 скрин на видео."
)

SOURCES_HELP_TEXT = (
    "🔌 <b>API / Sources</b>\n\n"
    "Команда <code>/sources</code> показывает, готовы ли App Store Connect и Google Play.\n"
    "Команда <code>/sync_funnels</code> показывает текущий статус автосбора воронки.\n\n"
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
                InlineKeyboardButton(text="📸 TikTok / Instagram", callback_data="help_screens"),
                InlineKeyboardButton(text="▶️ YouTube", callback_data="help_youtube"),
            ],
            [
                InlineKeyboardButton(text="📊 Воронка", callback_data="help_funnel_screens"),
                InlineKeyboardButton(text="📄 CSV", callback_data="help_csv"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Режим 2/3", callback_data="help_mode"),
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


@router.callback_query(
    lambda c: c.data in (
        "help_screens",
        "help_youtube",
        "help_csv",
        "help_funnel_screens",
        "help_mode",
        "help_sources",
    )
)
async def cb_quick_help(callback: CallbackQuery) -> None:
    if callback.data == "help_screens":
        text = SCREENSHOTS_HELP_TEXT
    elif callback.data == "help_youtube":
        text = YOUTUBE_HELP_TEXT
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


@router.callback_query(lambda c: c.data == "start_video_upload")
async def cb_start_video_upload(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    supabase = get_supabase()
    current = get_screenshots_mode(supabase, user_id) if supabase and user_id else "2"
    await state.clear()
    await state.set_state(UploadMode.active)
    await state.update_data(
        pending_video_photo_ids=[],
        upload_chunk_size=current,
        upload_flow="tiktok_instagram",
    )
    if callback.message:
        await callback.message.answer(
            SCREENSHOTS_HELP_TEXT,
            reply_markup=screenshots_mode_keyboard(current),
        )
    await callback.answer("Режим TikTok / Instagram включён")


@router.callback_query(lambda c: c.data == "start_youtube_upload")
async def cb_start_youtube_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(YouTubeUploadMode.active)
    await state.update_data(
        pending_video_photo_ids=[],
        upload_chunk_size="1",
        upload_flow="youtube",
    )
    if callback.message:
        await callback.message.answer(YOUTUBE_HELP_TEXT, reply_markup=help_back_keyboard())
    await callback.answer("Режим YouTube включён")


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
    label = "2 скрина (Overview + Retention)" if current == "2" else "3 скрина"
    await message.answer(
        f"📸 <b>Режим TikTok / Instagram</b>\n\nСейчас: <b>{label}</b>\n\nВыбери режим:",
        reply_markup=screenshots_mode_keyboard(current),
    )


@router.callback_query(lambda c: c.data in ("mode_2", "mode_3"))
async def cb_screenshots_mode(callback: CallbackQuery, state: FSMContext) -> None:
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
    current_state = await state.get_state()
    if current_state == UploadMode.active.state:
        await state.update_data(upload_chunk_size=target)
    label = "2 скрина (Overview + Retention)" if target == "2" else "3 скрина"
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
    if "youtube" in p or "shorts" in p:
        return "YouTube"
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

    for platform_name in ("TikTok", "Instagram", "YouTube", "Другое"):
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
