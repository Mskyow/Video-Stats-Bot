"""
Репозиторий пользователей: авторизация по кодовому слову.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

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
        # Fail-closed: если БД недоступна, доступ не разрешаем.
        logger.warning("Supabase client not initialized; denying access")
        return False

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
        logger.warning("Supabase client not initialized; cannot read user status")
        return None

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


# Режим скриншотов: 2 (пара) или 3 скриншота на одно видео

def get_screenshots_mode(
    client: Client | None,
    user_id: int,
) -> Literal["2", "3"]:
    """
    Возвращает режим скриншотов пользователя: '2' (пара) или '3' (три скриншота).
    По умолчанию '2', если пользователь не найден или БД недоступна.
    """
    if client is None:
        logger.warning("Supabase client not initialized; defaulting screenshots_mode to 2")
        return "2"

    try:
        resp = client.table("users").select("screenshots_mode").eq("id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            mode = resp.data[0].get("screenshots_mode")
            if mode in ("2", "3"):
                return mode
        return "2"
    except Exception as e:
        logger.exception("get_screenshots_mode failed: %s", e)
        return "2"


def set_screenshots_mode(
    client: Client | None,
    user_id: int,
    mode: Literal["2", "3"],
) -> bool:
    """
    Устанавливает режим скриншотов пользователя.
    Возвращает True при успехе.
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip set_screenshots_mode")
        return False

    try:
        resp = client.table("users").update({"screenshots_mode": mode}).eq("id", user_id).execute()
        if resp.data and len(resp.data) > 0:
            logger.info("User %s screenshots_mode set to %s", user_id, mode)
            return True
        return False
    except Exception as e:
        logger.exception("set_screenshots_mode failed: %s", e)
        return False
