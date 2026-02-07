"""
Middleware для сбора медиа-групп (альбомов).
"""
import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Union

from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """
    Собирает сообщения с одинаковым media_group_id в список.
    """

    def __init__(self, latency: Union[int, float] = 0.6):
        self.latency = latency
        self.album_data: Dict[str, List[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        try:
            self.album_data[event.media_group_id].append(event)
            return  # Пропускаем остальные сообщения группы
        except KeyError:
            self.album_data[event.media_group_id] = [event]
            await asyncio.sleep(self.latency)

            message = event
            data["album"] = self.album_data[event.media_group_id]

            try:
                # Сортируем по message_id
                data["album"].sort(key=lambda x: x.message_id)
                return await handler(message, data)
            finally:
                del self.album_data[event.media_group_id]
