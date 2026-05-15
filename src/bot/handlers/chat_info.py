"""
Utilities for reporting current chat and topic identifiers.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.users import is_user_authorized
from src.db.supabase_client import get_supabase

router = Router(name="chat_info")


def _authorized(user_id: int) -> bool:
    return is_user_authorized(get_supabase(), user_id)


@router.message(Command("chat_info"))
async def cmd_chat_info(message: Message) -> None:
    user = message.from_user
    if not user or not _authorized(user.id):
        await message.answer(
            "🔒 <b>Доступ ограничен.</b>\n\n"
            "Сначала авторизуйся через <code>/start КОДОВОЕ_СЛОВО</code>."
        )
        return

    chat = message.chat
    thread_id = getattr(message, "message_thread_id", None)
    is_topic = thread_id is not None
    lines = [
        "🧾 <b>Chat Info</b>",
        "",
        f"Chat title: <code>{chat.title or chat.full_name or 'DM'}</code>",
        f"Chat ID: <code>{chat.id}</code>",
        f"Chat type: <code>{chat.type}</code>",
    ]
    if is_topic:
        lines.append(f"Topic ID: <code>{thread_id}</code>")
        lines.append("")
        lines.append("Для Railway:")
        lines.append(f"<code>REPORT_CHAT_ID={chat.id}</code>")
        lines.append(f"<code>REPORT_TOPIC_ID={thread_id}</code>")
    else:
        lines.append("")
        lines.append("Для Railway:")
        lines.append(f"<code>REPORT_CHAT_ID={chat.id}</code>")

    await message.answer("\n".join(lines))
