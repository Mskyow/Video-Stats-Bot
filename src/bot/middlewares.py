"""
Middleware: авторизация пользователей с auto-register и approval flow.

Логика:
1. При любом сообщении от нового пользователя — автоматически регистрируем (status='pending').
2. Если статус 'approved' — пропускаем в handler.
3. Если статус 'pending' — сообщаем, что ожидает одобрения.
4. Если статус 'rejected' — сообщаем об отказе.
5. Если ALLOW_ALL_USERS=1 — пропускаем всех (для тестирования).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject

from src import config
from src.db.repositories.users import get_user_status, register_user

logger = logging.getLogger(__name__)

PENDING_MESSAGE = (
    "👋 Ваша заявка на доступ к боту принята!\n\n"
    "⏳ Ожидайте одобрения администратором.\n"
    "Вам придёт уведомление, как только доступ будет открыт."
)

REJECTED_MESSAGE = (
    "❌ К сожалению, ваша заявка на доступ была отклонена.\n\n"
    "Если вы считаете, что это ошибка — свяжитесь с администратором."
)

FIRST_CONTACT_MESSAGE = (
    "👋 Добро пожаловать!\n\n"
    "Я бот для анализа метрик видео (TikTok, Reels, Shorts).\n"
    "Ваша заявка автоматически отправлена администратору на одобрение.\n\n"
    "⏳ Пожалуйста, подождите — как только админ одобрит доступ, "
    "вы сможете отправлять скриншоты для анализа."
)


class AuthMiddleware(BaseMiddleware):
    """
    Auto-register + approval check.
    При первом контакте регистрирует пользователя в Supabase.
    Пропускает в handler только approved пользователей.
    """

    def __init__(self, supabase_client: Any) -> None:
        self.supabase = supabase_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Пропускаем не-Message события и события без пользователя
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        # Bypass для тестирования
        if getattr(config, "ALLOW_ALL_USERS", False):
            # Всё равно регистрируем для учёта, но не блокируем
            await self._ensure_registered(event)
            return await handler(event, data)

        user_id = event.from_user.id

        # Проверяем текущий статус
        status = await asyncio.to_thread(
            get_user_status, self.supabase, user_id
        )

        if status is None:
            # Новый пользователь — регистрируем
            await self._ensure_registered(event)
            await event.answer(FIRST_CONTACT_MESSAGE)
            logger.info(
                "New user registered: id=%s, username=%s",
                user_id,
                event.from_user.username,
            )
            # Уведомляем админа о новом пользователе
            await self._notify_admin_new_user(event, data)
            return None

        if status == "approved":
            # Обновляем профильные данные (username мог смениться)
            await self._ensure_registered(event)
            return await handler(event, data)

        if status == "rejected":
            logger.info("Rejected user attempted access: id=%s", user_id)
            await event.answer(REJECTED_MESSAGE)
            return None

        # status == "pending"
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

    async def _notify_admin_new_user(self, event: Message, data: dict[str, Any]) -> None:
        """Отправляет уведомление админу о новом пользователе."""
        admin_id = getattr(config, "ADMIN_USER_ID", None)
        if not admin_id:
            return

        user = event.from_user
        if user is None:
            return

        bot: Bot | None = data.get("bot")
        if bot is None:
            return

        username = f"@{user.username}" if user.username else "нет"
        name = user.full_name or "—"

        text = (
            "🆕 <b>Новый пользователь!</b>\n\n"
            f"👤 <b>Имя:</b> {name}\n"
            f"🔗 <b>Username:</b> {username}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n\n"
            "Для одобрения: измените <code>status</code> на <code>approved</code> "
            "в таблице <code>users</code> в Supabase Dashboard."
        )

        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logger.warning("Failed to notify admin about new user: %s", e)
