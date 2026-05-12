"""
Commands for source setup and future automated funnel sync.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.users import is_user_authorized
from src.db.supabase_client import get_supabase
from src.services.funnel_sources_service import build_funnel_sources_status_text

router = Router(name="funnel_sync")


NOT_AUTHORIZED_TEXT = (
    "🔒 <b>Доступ ограничен.</b>\n\n"
    "Сначала авторизуйся через <code>/start КОДОВОЕ_СЛОВО</code>."
)


SYNC_HELP_TEXT = (
    "🔌 <b>Автосбор Marketing Funnels</b>\n\n"
    "Сейчас доступны два режима:\n"
    "• <b>/import_csv</b> — импорт подготовленного CSV\n"
    "• <b>/sources</b> — проверка готовности App Store / Google Play\n\n"
    "Когда API-источники будут настроены, бот сможет обновлять воронку без ручного CSV."
)


def _is_authorized(user_id: int) -> bool:
    return is_user_authorized(get_supabase(), user_id)


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    user = message.from_user
    if not user or not _is_authorized(user.id):
        await message.answer(NOT_AUTHORIZED_TEXT)
        return
    await message.answer(build_funnel_sources_status_text())


@router.message(Command("sync_funnels"))
async def cmd_sync_funnels(message: Message) -> None:
    user = message.from_user
    if not user or not _is_authorized(user.id):
        await message.answer(NOT_AUTHORIZED_TEXT)
        return
    await message.answer(SYNC_HELP_TEXT + "\n\n" + build_funnel_sources_status_text())
