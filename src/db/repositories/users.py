"""
Проверка user_id по whitelist (таблица users в Supabase).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


def is_user_allowed(client: Client | None, user_id: int) -> bool:
    """
    Проверяет, есть ли user_id в таблице users (whitelist).
    Если клиент не передан или запрос падает — по умолчанию False (доступ запрещён).
    """
    if client is None:
        logger.warning("Supabase client not initialized; denying access")
        return False
    try:
        resp = client.table("users").select("id").eq("id", user_id).execute()
        return bool(resp.data and len(resp.data) > 0)
    except Exception as e:
        logger.exception("is_user_allowed failed for user_id=%s: %s", user_id, e)
        return False
