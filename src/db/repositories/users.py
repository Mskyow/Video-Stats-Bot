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
        resp = client.table("users").select("*").eq("id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0]

        # Создаем нового пользователя (неавторизованного, status='pending')
        resp = client.table("users").insert({
            "id": user_id,
            "username": username,
            "status": "pending",
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
    Пользователь авторизован если status = 'approved'.
    """
    if client is None:
        # Если БД недоступна, разрешаем доступ (для разработки)
        logger.warning("Supabase client not initialized; allowing access")
        return True

    try:
        resp = client.table("users").select("status").eq("id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("status") == "approved"
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
    Меняет status на 'approved'.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip authorize_user")
        return False

    try:
        resp = client.table("users").update({
            "status": "approved"
        }).eq("id", user_id).execute()

        if resp.data and len(resp.data) > 0:
            logger.info("User authorized: %s", user_id)
            return True
        return False

    except Exception as e:
        logger.exception("authorize_user failed: %s", e)
        return False


# Alias functions для совместимости с middlewares.py

def get_user_status(
    client: Client | None,
    user_id: int,
) -> str | None:
    """
    Возвращает статус пользователя ('pending', 'approved', 'rejected') или None если пользователь не найден.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip get_user_status")
        return "approved"  # Для разработки разрешаем доступ

    try:
        resp = client.table("users").select("status").eq("id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("status")
        return None

    except Exception as e:
        logger.exception("get_user_status failed: %s", e)
        return None


def register_user(
    client: Client | None,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any] | None:
    """
    Регистрирует или обновляет пользователя в БД.
    Возвращает данные пользователя.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip register_user")
        return None

    try:
        # Проверяем существует ли пользователь
        resp = client.table("users").select("*").eq("id", telegram_id).execute()
        if resp.data and len(resp.data) > 0:
            # Обновляем username если изменился
            existing = resp.data[0]
            if existing.get("username") != username or existing.get("first_name") != first_name or existing.get("last_name") != last_name:
                client.table("users").update({
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                }).eq("id", telegram_id).execute()
            return existing

        # Создаем нового пользователя (pending)
        resp = client.table("users").insert({
            "id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "status": "pending",
            "role": "user",
        }).execute()

        if resp.data and len(resp.data) > 0:
            logger.info("Registered new user: %s", telegram_id)
            return resp.data[0]
        return None

    except Exception as e:
        logger.exception("register_user failed: %s", e)
        return None


def promote_user_to_approved(
    client: Client | None,
    user_id: int,
) -> bool:
    """
    Повышает пользователя до статуса 'approved'.
    """
    return authorize_user(client, user_id)
