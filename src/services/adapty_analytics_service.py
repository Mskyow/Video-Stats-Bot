"""Adapty install-cohort metrics -> Google Sheets."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests

from src import config
from src.services.sheets_service import _get_client, _open_spreadsheet

ADAPTY_ANALYTICS_URL = "https://api-admin.adapty.io/api/v1/client-api/metrics/analytics/"
COHORT_SHEET = "Install Cohorts"
COHORT_HEADERS = [
    "Install Date", "Adapty First Launches", "Trials from This Install Cohort", "Install to Trial (to date)",
    "New Paid Subscriptions", "Install to Paid (to date)",
]


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


class AdaptyAnalyticsClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def daily_install_cohort_metric(self, chart_id: str, start: date, end: date) -> dict[str, int]:
        if not config.ADAPTY_SECRET_KEY:
            raise RuntimeError("ADAPTY_SECRET_KEY is not configured.")
        response = self.session.post(
            ADAPTY_ANALYTICS_URL,
            headers={
                "Authorization": f"Api-Key {config.ADAPTY_SECRET_KEY}",
                "Content-Type": "application/json",
                "Adapty-Tz": config.APPSTORE_ANALYTICS_TIMEZONE,
            },
            json={
                "chart_id": chart_id,
                "filters": {"date": [start.isoformat(), end.isoformat()], "store": ["app_store"]},
                "period_unit": "day",
                "date_type": "profile_install_date",
                "format": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        common = ((response.json().get("data") or {}).get("common") or {})
        series = (common.get("data") or [])
        if not series:
            return {}
        return {
            str(item.get("x") or "")[:10]: int(float(item.get("y") or 0))
            for item in (series[0].get("values") or [])
            if str(item.get("x") or "")
        }


def _cohort_rows(days: list[date], metrics: dict[str, dict[str, int]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row_number, day in enumerate(days, start=3):
        values = metrics.get(day.isoformat(), {})
        rows.append([
            day.isoformat(), values.get("installs", 0), values.get("trials_new", 0),
            f'=IF(B{row_number}=0;"";C{row_number}/B{row_number})',
            values.get("subscriptions_new", 0),
            f'=IF(B{row_number}=0;"";E{row_number}/B{row_number})',
        ])
    end_row = len(rows) + 2
    return [[
        "TOTAL", f'=SUM(B3:B{end_row})', f'=SUM(C3:C{end_row})',
        '=IF(B2=0;"";C2/B2)',
        f'=SUM(E3:E{end_row})', '=IF(B2=0;"";E2/B2)',
    ], *rows]


def sync_adapty_install_cohorts(*, start_date: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    if not config.ADAPTY_SECRET_KEY:
        return {"status": "skipped_not_configured"}
    start = date.fromisoformat(start_date or config.APPSTORE_ANALYTICS_START_DATE)
    end = date.fromisoformat(target_date) if target_date else datetime.now().date() - timedelta(days=1)
    if end < start:
        return {"status": "no_dates", "start": start.isoformat(), "end": end.isoformat()}

    client = AdaptyAnalyticsClient()
    metrics = {
        day.isoformat(): {
            "installs": 0,
            "trials_new": 0,
            "subscriptions_new": 0,
        }
        for day in _days(start, end)
    }
    for chart_id in ("installs", "trials_new", "subscriptions_new"):
        for source_date, value in client.daily_install_cohort_metric(chart_id, start, end).items():
            if source_date in metrics:
                metrics[source_date][chart_id] = value

    book = _open_spreadsheet(_get_client())
    try:
        worksheet = book.worksheet(COHORT_SHEET)
    except Exception:
        worksheet = book.add_worksheet(title=COHORT_SHEET, rows=1000, cols=len(COHORT_HEADERS))
    if worksheet.col_count < len(COHORT_HEADERS):
        worksheet.resize(cols=len(COHORT_HEADERS))
    worksheet.update(range_name=f"A1:{_column(len(COHORT_HEADERS))}1", values=[COHORT_HEADERS], value_input_option="USER_ENTERED")
    worksheet.batch_clear([f"A2:{_column(len(COHORT_HEADERS))}{worksheet.row_count}"])
    rows = _cohort_rows(_days(start, end), metrics)
    worksheet.update(range_name=f"A2:{_column(len(COHORT_HEADERS))}{len(rows)+1}", values=rows, value_input_option="USER_ENTERED")
    worksheet.freeze(rows=1)
    worksheet.format(f"A1:{_column(len(COHORT_HEADERS))}1", {
        "backgroundColor": {"red": 0.15, "green": 0.25, "blue": 0.40},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER",
    })
    for column in ("D", "F"):
        worksheet.format(f"{column}2:{column}{len(rows)+1}", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
    return {"status": "success", "start": start.isoformat(), "end": end.isoformat(), "rows": len(rows)}
