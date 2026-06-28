"""
Instagram Graph API collector for account-level top-of-funnel metrics.

Requires:
- INSTAGRAM_ACCESS_TOKEN
- INSTAGRAM_USER_ID

This is the closest stable automated path for Instagram views. Public scraping often
returns likes/comments but not views; the Graph API can access account/media insights
for authorized Business/Creator accounts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from src import config


class InstagramGraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstagramAccountDailyMetrics:
    metric_date: str
    views: int | None
    reach: int | None
    impressions: int | None
    profile_views: int | None
    raw: dict[str, Any]


def _base_url() -> str:
    version = getattr(config, "INSTAGRAM_GRAPH_API_VERSION", "v23.0") or "v23.0"
    return f"https://graph.facebook.com/{version}"


def _require_credentials() -> tuple[str, str]:
    token = getattr(config, "INSTAGRAM_ACCESS_TOKEN", None)
    user_id = getattr(config, "INSTAGRAM_USER_ID", None)
    if not token or not user_id:
        raise InstagramGraphError(
            "Не заданы INSTAGRAM_ACCESS_TOKEN и/или INSTAGRAM_USER_ID в .env"
        )
    return token, user_id


def _request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token, _ = _require_credentials()
    response = requests.get(
        f"{_base_url()}/{path.lstrip('/')}",
        params={**params, "access_token": token},
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:1000]}
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else str(payload)
        raise InstagramGraphError(message)
    return payload


def check_instagram_graph_connection() -> dict[str, Any]:
    _, user_id = _require_credentials()
    return _request(
        user_id,
        {
            "fields": "id,username,name,profile_picture_url,followers_count,media_count",
        },
    )


def _extract_metric_value(payload: dict[str, Any], metric_name: str) -> int | None:
    for item in payload.get("data") or []:
        if item.get("name") != metric_name:
            continue
        # Newer Graph API metrics can return total_value.
        total_value = item.get("total_value")
        if isinstance(total_value, dict) and isinstance(total_value.get("value"), (int, float)):
            return int(total_value["value"])
        # Older daily metrics return values list.
        values = item.get("values") or []
        if values and isinstance(values[-1], dict) and isinstance(values[-1].get("value"), (int, float)):
            return int(values[-1]["value"])
    return None


def collect_instagram_account_daily_metrics(metric_date: date | None = None) -> InstagramAccountDailyMetrics:
    _, user_id = _require_credentials()
    target_date = metric_date or date.today()
    since = target_date.isoformat()
    until = target_date.isoformat()

    attempts: list[dict[str, Any]] = []
    errors: list[str] = []

    # Meta changes metric availability over time. Try the modern "views" first,
    # then fallback to older/common account metrics.
    for params in (
        {
            "metric": "views",
            "period": "day",
            "metric_type": "total_value",
            "since": since,
            "until": until,
        },
        {
            "metric": "impressions,reach,profile_views",
            "period": "day",
            "since": since,
            "until": until,
        },
    ):
        try:
            attempts.append(_request(f"{user_id}/insights", params))
        except InstagramGraphError as exc:
            errors.append(str(exc))

    if not attempts:
        raise InstagramGraphError("; ".join(errors) or "Instagram Insights API returned no data")

    merged = {"attempts": attempts, "errors": errors}
    return InstagramAccountDailyMetrics(
        metric_date=since,
        views=next((_extract_metric_value(item, "views") for item in attempts if _extract_metric_value(item, "views") is not None), None),
        reach=next((_extract_metric_value(item, "reach") for item in attempts if _extract_metric_value(item, "reach") is not None), None),
        impressions=next((_extract_metric_value(item, "impressions") for item in attempts if _extract_metric_value(item, "impressions") is not None), None),
        profile_views=next((_extract_metric_value(item, "profile_views") for item in attempts if _extract_metric_value(item, "profile_views") is not None), None),
        raw=merged,
    )
