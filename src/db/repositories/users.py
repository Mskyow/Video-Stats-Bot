"""
Репозиторий пользователей: auto-register, проверка статуса, обновление.

Логика авторизации:
- При первом обращении пользователь автоматически регистрируется со статусом 'pending'.
- Админ из Supabase Dashboard меняет статус на 'approved' или 'rejected'.
- Бот проверяет статус при каждом сообщении.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_user(client: Client | None, telegram_id: int) -> dict[str, Any] | None:
    """Возвращает запись пользователя или None, если не найден."""
    if client is None:
        return None
    try:
        resp = client.table("users").select("*").eq("id", telegram_id).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0]
        return None
    except Exception as e:
        logger.exception("get_user failed for telegram_id=%s: %s", telegram_id, e)
        return None


def get_user_status(client: Client | None, telegram_id: int) -> str | None:
    """Возвращает статус пользователя: 'pending' | 'approved' | 'rejected' | None."""
    if client is None:
        return None
    try:
        resp = (
            client.table("users")
            .select("status")
            .eq("id", telegram_id)
            .execute()
        )
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("status")
        return None
    except Exception as e:
        logger.exception("get_user_status failed for telegram_id=%s: %s", telegram_id, e)
        return None


def is_user_approved(client: Client | None, telegram_id: int) -> bool:
    """Проверяет, одобрен ли пользователь (status = 'approved')."""
    return get_user_status(client, telegram_id) == "approved"


def is_user_allowed(client: Client | None, user_id: int) -> bool:
    """
    Backward-compatible обёртка.
    Возвращает True, если пользователь approved.
    """
    return is_user_approved(client, user_id)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def register_user(
    client: Client | None,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any] | None:
    """
    Регистрирует нового пользователя со статусом 'pending'.
    Если пользователь уже существует — обновляет username/first_name/last_name
    и возвращает существующую запись (статус НЕ перезаписывается).
    """
    if client is None:
        logger.warning("Supabase client not initialized; skip register_user")
        return None

    try:
        existing = get_user(client, telegram_id)
        if existing:
            # Обновляем профильные данные (username мог измениться)
            updates: dict[str, Any] = {}
            if username and username != existing.get("username"):
                updates["username"] = username
            if first_name and first_name != existing.get("first_name"):
                updates["first_name"] = first_name
            if last_name and last_name != existing.get("last_name"):
                updates["last_name"] = last_name

            if updates:
                client.table("users").update(updates).eq("id", telegram_id).execute()
                logger.info("Updated profile for user %s: %s", telegram_id, updates)

            return existing

        # Новый пользователь
        payload: dict[str, Any] = {
            "id": telegram_id,
            "status": "pending",
            "role": "user",
        }
        if username:
            payload["username"] = username
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name

        resp = client.table("users").insert(payload).execute()
        if resp.data and len(resp.data) > 0:
            logger.info(
                "Registered new user: telegram_id=%s, username=%s, status=pending",
                telegram_id,
                username,
            )
            return resp.data[0]
        return None

    except Exception as e:
        logger.exception("register_user failed for telegram_id=%s: %s", telegram_id, e)
        return None


def promote_user_to_approved(client: Client | None, telegram_id: int) -> bool:
    """Promotes user to 'approved' status."""
    return update_user_status(client, telegram_id, "approved")


def update_user_status(
    client: Client | None,
    telegram_id: int,
    status: str,
) -> bool:
    """
    Обновляет статус пользователя. Используется админом.
    status: 'approved' | 'rejected' | 'pending'
    """
    if client is None:
        return False
    if status not in ("pending", "approved", "rejected"):
        logger.error("Invalid status: %s", status)
        return False
    try:
        resp = (
            client.table("users")
            .update({"status": status})
            .eq("id", telegram_id)
            .execute()
        )
        return bool(resp.data and len(resp.data) > 0)
    except Exception as e:
        logger.exception("update_user_status failed: %s", e)
        return False


def get_users_by_status(
    client: Client | None,
    status: str,
) -> list[dict[str, Any]]:
    """Возвращает список пользователей с данным статусом."""
    if client is None:
        return []
    try:
        resp = (
            client.table("users")
            .select("*")
            .eq("status", status)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.exception("get_users_by_status failed: %s", e)
        return []
