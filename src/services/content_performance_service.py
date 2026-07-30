"""Read the current per-video content-performance presentation from Supabase."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client


CONTENT_PERFORMANCE_VIEW = "content_performance"


def list_content_performance_rows(
    supabase: Client,
    *,
    lookback_days: int = 7,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return one latest metric row per video published in the rolling window."""
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=lookback_days)
    response = (
        supabase.table(CONTENT_PERFORMANCE_VIEW)
        .select("*")
        .gte("published_at", cutoff.isoformat())
        .order("published_at", desc=True)
        .execute()
    )
    return list(response.data or [])
