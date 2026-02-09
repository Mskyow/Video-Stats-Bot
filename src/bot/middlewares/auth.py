"""
Middleware: авторизация пользователей с auto-register и approval flow.

Логика:
1. При любом сообщении от нового пользователя — автоматически регистрируем (status='pending').
2. Если статус 'approved' — пропускаем в handler.
3. Если статус 'pending' — сообщаем, что ожидает одобрения.
4. Если статус 'rejected' — сообщаем об отказе.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject

from src.db.repositories.users import get_user_status, register_user, promote_user_to_approved
from src.config import AUTH_SECRET

logger = logging.getLogger(__name__)

PENDING_MESSAGE = (
    "🔒 Bot is private.\n\n"
    "Please send the access code."
)

ACCESS_GRANTED_MESSAGE = "Доступ разрешён✅"

REJECTED_MESSAGE = (
    "❌ Доступ отклонён.\n\n"
    "По вопросам обратись к администратору."
)

FIRST_CONTACT_MESSAGE = (
    "👋 Привет!\n\n"
    "Я анализирую метрики видео из TikTok, Reels, Shorts.\n"
    "Отправь код доступа, чтобы начать пользоваться."
)


class AuthMiddleware(BaseMiddleware):
    """
    Auto-register + approval check.
    При первом контакте регистрирует пользователя в Supabase.
    Пропускает в handler только approved пользователей.
    """

    def __init__(self, supabase_client: Any, bot: Bot) -> None:
        self.supabase = supabase_client
        self.bot = bot

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Пропускаем не-Message события и события без пользователя
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        user_id = event.from_user.id
        # Проверяем секретный код в сообщении
        text = getattr(event, "text", "") or ""
        is_secret_code = text.strip() == AUTH_SECRET

        # Проверяем текущий статус
        status = await asyncio.to_thread(
            get_user_status, self.supabase, user_id
        )

        if status == "approved":
            # Обновляем профильные данные (username мог смениться)
            await self._ensure_registered(event)
            # Добавляем supabase_client в data для использования в хендлерах
            data["supabase_client"] = self.supabase
            return await handler(event, data)

        # Если код верный - регистрируем/аппрувим
        if is_secret_code:
            await self._ensure_registered(event)
            success = await asyncio.to_thread(
                promote_user_to_approved, self.supabase, user_id
            )
            if success:
                logger.info("User %s approved via secret code", user_id)
                await event.answer(ACCESS_GRANTED_MESSAGE)
                return None
            else:
                logger.error("Failed to approve user %s", user_id)
                await event.answer("⚠️ Ошибка при активации доступа. Попробуйте позже.")
                return None

        if status == "rejected":
            logger.info("Rejected user attempted access: id=%s", user_id)
            await event.answer(REJECTED_MESSAGE)
            return None

        # status == "pending" или None (новый пользователь)
        if status is None:
            # Новый пользователь — регистрируем как pending
            await self._ensure_registered(event)
            logger.info(
                "New user registered: id=%s, username=%s",
                user_id,
                event.from_user.username,
            )
            await self._notify_admin_new_user(event)
            await event.answer(FIRST_CONTACT_MESSAGE)
            return None

        # Уже зарегистрирован, но pending
        logger.info("Pending user attempted access: id=%s", user_id)
        await event.answer(PENDING_MESSAGE)
        return None

    async def _ensure_registered(self, event: Message) -> None:
        """Регистрирует или обновляет профиль пользователя в БД."""
        user = event.from_user
        if user is None:
            return
        await asyncio.to_thread(
            register_user,
            self.supabase,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )

    async def _notify_admin_new_user(self, event: Message) -> None:
        """Отправляет уведомление админу о новом пользователе."""
        from src import config

        admin_id = getattr(config, "ADMIN_USER_ID", None)
        if not admin_id:
            return

        user = event.from_user
        if user is None:
            return

        username = f"@{user.username}" if user.username else "нет"
        name = user.full_name or "—"

        text = (
            "🆕 Новый пользователь\n\n"
            f"👤 {name}\n"
            f"🔗 {username}\n"
            f"🆔 <code>{user.id}</code>\n\n"
            "Для одобрения: <code>status = approved</code> в Supabase"
        )

        try:
            await self.bot.send_message(admin_id, text)
        except Exception as e:
            logger.warning("Failed to notify admin about new user: %s", e)
