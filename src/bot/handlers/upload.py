"""
Upload mode commands for video screenshots and funnel screenshots.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.handlers.start import screenshots_mode_keyboard
from src.bot.states import FunnelUploadMode, UploadMode
from src.db.repositories.users import get_screenshots_mode, is_user_authorized
from src.db.supabase_client import get_supabase

router = Router(name="upload")


VIDEO_UPLOAD_MODE_TEXT = (
    "📸 <b>Режим скринов включён</b>\n\n"
    "Сейчас бот ждёт скрины для листа <b>Video Analysis</b>.\n\n"
    "<b>Что делать:</b>\n"
    "• отправляй скрины статистики роликов\n"
    "• по умолчанию: пара <b>Overview + Retention</b>\n"
    "• можно отправить <b>альбомом сразу</b> или <b>по одному сообщению подряд</b>\n"
    "• бот начнёт анализ только когда соберётся полная пара/тройка\n\n"
    "Когда закончишь — <code>/done</code>"
)

FUNNEL_UPLOAD_MODE_TEXT = (
    "📊 <b>Режим воронки по скринам включён</b>\n\n"
    "Сейчас бот ждёт <b>6 скринов за один день</b> для листа <b>Marketing Funnels</b>.\n\n"
    "<b>Порядок:</b>\n"
    "1. App Store — Search Impressions\n"
    "2. App Store — Product Page Views\n"
    "3. App Store — Installs\n"
    "4. Google Play — Product Page Views\n"
    "5. Google Play — Installs\n"
    "6. Adapty — Purchases (all stores)\n\n"
    "Можно отправлять по одному или альбомом. Когда соберётся 6 скринов, бот сам начнёт распознавание.\n"
    "Сбросить режим — <code>/done</code>"
)

DONE_TEXT = (
    "✅ <b>Режим загрузки выключен</b>\n\n"
    "Для роликов: <code>/upload</code>\n"
    "Для воронки: <code>/upload_funnel</code>"
)

NOT_ACTIVE_TEXT = (
    "ℹ️ Сейчас ни один режим загрузки не активен.\n\n"
    "Для роликов: <code>/upload</code>\n"
    "Для воронки: <code>/upload_funnel</code>"
)

NOT_AUTHORIZED_TEXT = (
    "🔒 <b>Доступ ограничен.</b>\n\n"
    "Сначала авторизуйся:\n"
    "<code>/start КОДОВОЕ_СЛОВО</code>"
)


def _is_any_upload_state(current_state: str | None) -> bool:
    return current_state in {UploadMode.active.state, FunnelUploadMode.active.state}


@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if not user:
        await message.answer("Не удалось определить пользователя.")
        return

    supabase = get_supabase()
    if not is_user_authorized(supabase, user.id):
        await message.answer(NOT_AUTHORIZED_TEXT)
        return

    current_mode = get_screenshots_mode(supabase, user.id) if supabase else "2"
    keyboard = screenshots_mode_keyboard(current_mode)
    current_state = await state.get_state()
    if current_state == UploadMode.active.state:
        await message.answer("📸 Режим скринов уже включён.", reply_markup=keyboard)
        return

    await state.clear()
    await state.set_state(UploadMode.active)
    await state.update_data(pending_video_photo_ids=[])
    await message.answer(VIDEO_UPLOAD_MODE_TEXT, reply_markup=keyboard)


@router.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if not _is_any_upload_state(current_state):
        await message.answer(NOT_ACTIVE_TEXT)
        return

    await state.clear()
    await message.answer(DONE_TEXT)


@router.message(UploadMode.active, ~F.photo, ~F.document)
async def handle_non_photo_in_upload_mode(message: Message) -> None:
    await message.answer(
        "📸 Сейчас я жду только скрины статистики роликов.\n\n"
        "Когда закончишь — <code>/done</code>."
    )


@router.message(FunnelUploadMode.active, ~F.photo, ~F.document)
async def handle_non_photo_in_funnel_mode(message: Message) -> None:
    await message.answer(
        "📊 Сейчас я жду только скрины воронки за один день.\n\n"
        "Нужны 6 скринов: App Store, Google Play и Adapty.\n"
        "Сбросить режим — <code>/done</code>."
    )
