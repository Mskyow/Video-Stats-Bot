"""
Клиент Supabase (один экземпляр на приложение).
Инициализируется в main при старте бота.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from supabase import create_client, Client

if TYPE_CHECKING:
    pass

_client: Client | None = None


def get_client(url: str, key: str) -> Client:
    """Возвращает синглтон-клиент Supabase."""
    global _client
    if _client is None:
        _client = create_client(url, key)
    return _client


def get_supabase() -> Client | None:
    """Возвращает текущий клиент или None, если ещё не инициализирован."""
    return _client
