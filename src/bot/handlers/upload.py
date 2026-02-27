"""
Режим загрузки статистики (/upload, /done).
Позволяет пользователю перейти в режим, где бот готов принимать пачки скриншотов.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.handlers.start import screenshots_mode_keyboard
from src.bot.states import UploadMode
from src.db.repositories.users import get_screenshots_mode, is_user_authorized
from src.db.supabase_client import get_supabase

router = Router(name="upload")


UPLOAD_MODE_TEXT = (
    "📤 <b>Режим загрузки активирован!</b>\n\n"
    "Теперь я готов принимать скриншоты статистики.\n\n"
    "<b>Как загружать:</b>\n"
    "• Отправляй скриншоты парами: Обзор + Удержание\n"
    "• Можно загружать несколько видео сразу\n"
    "• Я автоматически распознаю метрики и сохраню анализ\n\n"
    "<b>Когда закончишь:</b> /done — выйти из режима загрузки"
)

DONE_TEXT = (
    "✅ <b>Режим загрузки завершён!</b>\n\n"
    "Все скриншоты обработаны.\n"
    "Для новой загрузки используй /upload"
)

ALREADY_ACTIVE_TEXT = (
    "⚡️ Ты уже в режиме загрузки!\n\n"
    "Просто отправляй скриншоты статистики.\n"
    "Для выхода используй /done"
)

NOT_ACTIVE_TEXT = (
    "ℹ️ Сейчас ты не в режиме загрузки.\n\n"
    "Чтобы начать загружать скриншоты, используй /upload"
)

NOT_AUTHORIZED_TEXT = (
    "🔒 <b>Доступ ограничен.</b>\n\n"
    "Для анализа видео сначала авторизуйся:\n"
    "<code>/start КОДОВОЕ_СЛОВО</code>"
)


@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext) -> None:
    """
    Активирует режим загрузки статистики.
    Пользователь может отправлять пачки скриншотов.
    """
    user = message.from_user
    if not user:
        await message.answer("Не удалось определить пользователя.")
        return

    # Проверяем авторизацию
    supabase = get_supabase()
    if not is_user_authorized(supabase, user.id):
        await message.answer(NOT_AUTHORIZED_TEXT)
        return

    # Проверяем, не активен ли уже режим
    current_state = await state.get_state()
    current_mode = get_screenshots_mode(supabase, user.id) if supabase else "2"
    keyboard = screenshots_mode_keyboard(current_mode)
    if current_state == UploadMode.active:
        await message.answer(ALREADY_ACTIVE_TEXT, reply_markup=keyboard)
        return

    # Активируем режим загрузки
    await state.set_state(UploadMode.active)
    await message.answer(UPLOAD_MODE_TEXT, reply_markup=keyboard)


@router.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext) -> None:
    """
    Завершает режим загрузки статистики.
    """
    # Проверяем, активен ли режим загрузки
    current_state = await state.get_state()
    if current_state != UploadMode.active:
        await message.answer(NOT_ACTIVE_TEXT)
        return

    # Очищаем состояние
    await state.clear()
    await message.answer(DONE_TEXT)


@router.message(UploadMode.active, ~F.photo)
async def handle_non_photo_in_upload_mode(message: Message) -> None:
    """
    Обрабатывает сообщения без фото в режиме загрузки.
    Напоминает пользователю, что ожидаются скриншоты.
    """
    await message.answer(
        "📸 В режиме загрузки я жду только скриншоты статистики.\n\n"
        "Отправь скриншоты видео, или используй /done для выхода из режима."
    )
