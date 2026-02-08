"""
Репозиторий пользователей: авторизация по кодовому слову.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


def get_or_create_user(
    client: Client | None,
    user_id: int,
    username: str | None = None,
) -> dict[str, Any] | None:
    """
    Получает или создает пользователя в БД.
    Возвращает данные пользователя.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip get_or_create_user")
        return None

    try:
        # Сначала проверяем существует ли пользователь
        resp = client.table("users").select("*").eq("user_id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0]

        # Создаем нового пользователя (неавторизованного)
        resp = client.table("users").insert({
            "user_id": user_id,
            "username": username,
            "is_authorized": False,
        }).execute()

        if resp.data and len(resp.data) > 0:
            logger.info("Created new user: %s", user_id)
            return resp.data[0]
        return None

    except Exception as e:
        logger.exception("get_or_create_user failed: %s", e)
        return None


def is_user_authorized(
    client: Client | None,
    user_id: int,
) -> bool:
    """
    Проверяет, авторизован ли пользователь.
    """
    if client is None:
        # Если БД недоступна, разрешаем доступ (для разработки)
        logger.warning("Supabase client not initialized; allowing access")
        return True

    try:
        resp = client.table("users").select("is_authorized").eq("user_id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("is_authorized", False)
        return False

    except Exception as e:
        logger.exception("is_user_authorized failed: %s", e)
        return False


def authorize_user(
    client: Client | None,
    user_id: int,
) -> bool:
    """
    Авторизует пользователя (после ввода правильного кода).
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip authorize_user")
        return False

    try:
        resp = client.table("users").update({
            "is_authorized": True
        }).eq("user_id", user_id).execute()

        if resp.data and len(resp.data) > 0:
            logger.info("User authorized: %s", user_id)
            return True
        return False

    except Exception as e:
        logger.exception("authorize_user failed: %s", e)
        return False
