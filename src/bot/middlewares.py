"""
Middleware: проверка пользователя по whitelist (таблица users).
Неавторизованным отправляется сообщение, обработчик не вызывается.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.db.repositories.users import is_user_allowed

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """
    Проверяет user_id по whitelist в Supabase (таблица users).
    Если пользователя нет в списке — отвечаем и не вызываем handler.
    """

    def __init__(self, supabase_client: Any) -> None:
        self.supabase = supabase_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        user_id = event.from_user.id
        allowed = await asyncio.to_thread(is_user_allowed, self.supabase, user_id)
        if not allowed:
            logger.info("Access denied for user_id=%s", user_id)
            await event.answer(
                "У вас нет доступа к этому боту. Обратитесь к администратору."
            )
            return None
        return await handler(event, data)
