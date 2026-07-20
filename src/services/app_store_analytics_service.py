"""App Store Connect Analytics Reports -> Google Sheets.

Apple publishes analytics asynchronously and can correct prior dates. This
collector replaces each source date from the newest processing batch and never
turns an unavailable report into zero metrics.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from src import config
from src.services.funnel_sources_service import APPSTORE_API_BASE, _build_appstore_token
from src.services.sheets_service import _get_client, _open_spreadsheet

logger = logging.getLogger(__name__)

FACTS_SHEET = "Store Daily Facts"
TOTAL_SHEET = "Store Total"
FUNNEL_SHEET = "App Store Funnel"
ASA_SHEET = "ASA"
VIRAL_SHEET = "Viral + Organic"
OVERVIEW_SHEET = "Acquisition Overview"
QUALITY_SHEET = "Data Quality"

FACT_HEADERS = [
    "Date", "App ID", "Bundle ID", "Store", "Aggregation Scope", "Territory",
    "Platform", "Source Type", "Source Detail", "Campaign ID",
    "Unique Impressions", "Impressions", "Unique Product Page Views",
    "Product Page Views", "First-Time Downloads", "Total Downloads", "Redownloads",
    "Purchases", "Data Status", "Attribution Status", "Apple Report Name",
    "Apple Report Instance ID", "Retrieved At", "Updated At", "Record Key", "Notes",
]
TOTAL_HEADERS = [
    "Date", "Status", "Unique Impressions", "Impressions", "Unique Product Page Views",
    "Product Page Views", "First-Time Downloads", "Total Downloads", "Redownloads",
    "Purchases", "Impression Frequency", "PPV Frequency", "Page Visit Rate",
    "Page Conversion", "Acquisition Conversion", "Retrieved At",
]
FUNNEL_HEADERS = [
    "Date", "Impressions (Total)", "Impression to Product Page View",
    "Product Page Views (Total)", "Product Page View to First-Time Download",
    "First-Time Downloads", "First-Time Download to Trial (calendar)", "Free Trial Starts",
    "Trial to Paid (calendar)", "Paid from Trial", "Direct Paid Purchases",
]
ASA_HEADERS = [
    "Date", "Status", "Attribution Source", "Campaign ID", "Campaign Name", "Country",
    "Unique Impressions", "Impressions", "Unique Product Page Views", "Product Page Views",
    "First-Time Downloads", "Total Downloads", "Redownloads", "Purchases",
    "Page Visit Rate", "Page Conversion", "Acquisition Conversion", "Attribution Status",
    "Retrieved At",
]
VIRAL_HEADERS = [
    "Date", "Social Status", "Viral Views Total", "Instagram Views", "TikTok Views",
    "Store Status", "Organic Scope", "Organic Unique Impressions", "Organic Impressions",
    "Organic Unique PPV", "Organic PPV", "Organic First-Time Downloads",
    "Store Page Visit Rate", "Store Page Conversion", "Relationship Type", "Notes",
]
OVERVIEW_HEADERS = [
    "Date", "Channel", "Relationship Type", "Data Status", "Marketing Views",
    "Unique Store Impressions", "Unique Product Page Views", "First-Time Downloads",
    "Purchases", "View-to-Store Rate", "Store Page Visit Rate", "Store Page Conversion",
    "Acquisition Conversion", "Source Scope", "Notes",
]
QUALITY_HEADERS = [
    "Checked At", "Date", "Scope", "Check Code", "Severity", "Status", "Expected",
    "Actual", "Record Key", "Message", "First Seen At", "Last Seen At", "Resolved At",
]

RELEASED_STATES = {"READY_FOR_SALE", "PRE_ORDER_READY_FOR_SALE"}


@dataclass
class ReportFile:
    name: str
    instance_id: str
    processing_date: str
    rows: list[dict[str, str]]


class AppStoreAnalyticsClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_build_appstore_token()}"}

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _all(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        while url:
            payload = self._get(url, params)
            result.extend(payload.get("data") or [])
            url = str((payload.get("links") or {}).get("next") or "")
            params = None
        return result

    def app(self) -> dict[str, Any]:
        apps = self._get(
            f"{APPSTORE_API_BASE}/apps",
            {"filter[bundleId]": config.APPSTORE_BUNDLE_ID, "limit": 10},
        ).get("data") or []
        if not apps:
            raise RuntimeError(f"App not found: {config.APPSTORE_BUNDLE_ID}")
        return apps[0]

    def first_release_pending(self, app_id: str) -> bool:
        versions = self._all(
            f"{APPSTORE_API_BASE}/apps/{app_id}/appStoreVersions", {"limit": 50}
        )
        states = {
            str((version.get("attributes") or {}).get("appStoreState") or "")
            for version in versions
        }
        return not bool(states & RELEASED_STATES)

    def report_files(self, app_id: str) -> list[ReportFile]:
        requests_data = self._all(
            f"{APPSTORE_API_BASE}/apps/{app_id}/analyticsReportRequests", {"limit": 200}
        )
        files: list[ReportFile] = []
        seen_instances: set[str] = set()
        for request_data in requests_data:
            reports = self._all(
                f"{APPSTORE_API_BASE}/analyticsReportRequests/{request_data['id']}/reports",
                {"limit": 200},
            )
            for report in reports:
                name = str((report.get("attributes") or {}).get("name") or "")
                if not _is_supported_report(name):
                    continue
                instances = self._all(
                    f"{APPSTORE_API_BASE}/analyticsReports/{report['id']}/instances",
                    {"filter[granularity]": "DAILY", "limit": 200},
                )
                for instance in instances:
                    instance_id = str(instance["id"])
                    if instance_id in seen_instances:
                        continue
                    seen_instances.add(instance_id)
                    rows: list[dict[str, str]] = []
                    segments = self._all(
                        f"{APPSTORE_API_BASE}/analyticsReportInstances/{instance_id}/segments",
                        {"fields[analyticsReportSegments]": "url,checksum,sizeInBytes", "limit": 200},
                    )
                    for segment in segments:
                        url = str((segment.get("attributes") or {}).get("url") or "")
                        if not url:
                            continue
                        download = self.session.get(url, timeout=60)
                        download.raise_for_status()
                        text = gzip.decompress(download.content).decode("utf-8-sig")
                        rows.extend(csv.DictReader(io.StringIO(text), delimiter="\t"))
                    attrs = instance.get("attributes") or {}
                    files.append(ReportFile(name, instance_id, str(attrs.get("processingDate") or ""), rows))
        return files


def _is_supported_report(name: str) -> bool:
    return "Detailed" not in name and (
        "App Store Discovery and Engagement" in name
        or "App Downloads" in name
        or name == "App Store Purchases Standard"
        or name == "App Store Subscription Event Report Standard"
    )


def _number(row: dict[str, str], key: str) -> int | None:
    value = str(row.get(key) or "").strip().replace(",", "")
    return None if value == "" else int(float(value))


def _record_key(row: dict[str, Any]) -> str:
    fields = ("Date", "App ID", "Aggregation Scope", "Territory", "Platform", "Source Type", "Source Detail", "Campaign ID")
    return "|".join(str(row.get(field) or "").strip() for field in fields)


def _extract_facts(app: dict[str, Any], files: list[ReportFile], start: date, end: date) -> list[dict[str, Any]]:
    newest: dict[tuple[str, str], ReportFile] = {}
    for file in files:
        for item in file.rows:
            source_date = str(item.get("Date") or "")
            if start.isoformat() <= source_date <= end.isoformat():
                key = (file.name, source_date)
                if key not in newest or file.processing_date >= newest[key].processing_date:
                    newest[key] = file

    selected = {file.instance_id: file for file in newest.values()}.values()
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    now = datetime.now(timezone.utc).isoformat()
    app_id = str(app["id"])
    bundle = str((app.get("attributes") or {}).get("bundleId") or "")
    for file in selected:
        for item in file.rows:
            source_date = str(item.get("Date") or "")
            if newest.get((file.name, source_date)) is not file:
                continue
            source = str(item.get("Source Type") or "")
            detail = str(item.get("Source Info") or "")
            campaign = str(item.get("Campaign") or "")
            territory = str(item.get("Territory") or "")
            platform = str(item.get("Device") or "")
            scope = "campaign" if campaign else "source"
            key = (source_date, scope, territory, platform, source, detail, campaign)
            fact = grouped.setdefault(key, {
                "Date": source_date, "App ID": app_id, "Bundle ID": bundle, "Store": "App Store",
                "Aggregation Scope": scope, "Territory": territory, "Platform": platform,
                "Source Type": source, "Source Detail": detail, "Campaign ID": campaign,
                "Unique Impressions": None, "Impressions": None,
                "Unique Product Page Views": None, "Product Page Views": None,
                "First-Time Downloads": None, "Total Downloads": None, "Redownloads": None,
                "Purchases": None, "Data Status": "complete",
                "Attribution Status": "attributed" if campaign else "store-reported",
                "Apple Report Name": file.name, "Apple Report Instance ID": file.instance_id,
                "Retrieved At": now, "Updated At": now,
                "Notes": "Unique metrics are valid at this row grain; never sum across dimensions.",
            })
            count = _number(item, "Counts") or 0
            unique = _number(item, "Unique Counts")
            if "Discovery and Engagement" in file.name:
                event = str(item.get("Event") or "").lower()
                page_type = str(item.get("Page Type") or "").lower()
                if event == "impression":
                    fact["Impressions"] = (fact["Impressions"] or 0) + count
                    if unique is not None:
                        fact["Unique Impressions"] = (fact["Unique Impressions"] or 0) + unique
                elif event == "page view" and page_type == "product page":
                    fact["Product Page Views"] = (fact["Product Page Views"] or 0) + count
                    if unique is not None:
                        fact["Unique Product Page Views"] = (fact["Unique Product Page Views"] or 0) + unique
            elif "Downloads" in file.name:
                download_type = str(item.get("Download Type") or "").lower()
                if download_type == "first-time download":
                    fact["First-Time Downloads"] = (fact["First-Time Downloads"] or 0) + count
                elif download_type == "redownload":
                    fact["Redownloads"] = (fact["Redownloads"] or 0) + count

    facts = list(grouped.values())
    for fact in facts:
        if fact["First-Time Downloads"] is not None or fact["Redownloads"] is not None:
            fact["Total Downloads"] = (fact["First-Time Downloads"] or 0) + (fact["Redownloads"] or 0)
        fact["Record Key"] = _record_key(fact)
    return facts


def _column(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _sheet(book: Any, title: str, headers: list[str]) -> Any:
    try:
        worksheet = book.worksheet(title)
    except Exception:
        worksheet = book.add_worksheet(title=title, rows=1000, cols=max(16, len(headers)))
    if worksheet.col_count < len(headers):
        worksheet.resize(cols=len(headers))
    worksheet.update(range_name=f"A1:{_column(len(headers))}1", values=[headers], value_input_option="USER_ENTERED")
    worksheet.freeze(rows=1)
    worksheet.format(f"A1:{_column(len(headers))}1", {
        "backgroundColor": {"red": 0.15, "green": 0.25, "blue": 0.40},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER",
    })
    return worksheet


def _replace(worksheet: Any, headers: list[str], rows: list[list[Any]]) -> None:
    worksheet.batch_clear([f"A2:{_column(len(headers))}{worksheet.row_count}"])
    if rows:
        worksheet.update(
            range_name=f"A2:{_column(len(headers))}{len(rows)+1}",
            values=rows,
            value_input_option="USER_ENTERED",
        )


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _add_availability_rows(app: dict[str, Any], facts: list[dict[str, Any]], days: list[date], pre_release: bool) -> list[dict[str, Any]]:
    existing = {str(fact["Date"]) for fact in facts}
    now = datetime.now(timezone.utc).isoformat()
    for day in days:
        source_date = day.isoformat()
        if source_date in existing:
            continue
        status = "pre_release_no_data" if pre_release else "pending"
        fact = {
            "Date": source_date, "App ID": str(app["id"]),
            "Bundle ID": str((app.get("attributes") or {}).get("bundleId") or ""),
            "Store": "App Store", "Aggregation Scope": "TOTAL", "Territory": "",
            "Platform": "", "Source Type": "", "Source Detail": "", "Campaign ID": "",
            "Unique Impressions": None, "Impressions": None,
            "Unique Product Page Views": None, "Product Page Views": None,
            "First-Time Downloads": None, "Total Downloads": None, "Redownloads": None,
            "Purchases": None, "Data Status": status, "Attribution Status": "unavailable",
            "Apple Report Name": "", "Apple Report Instance ID": "", "Retrieved At": now,
            "Updated At": now,
            "Notes": "App is awaiting its first release." if pre_release else "Apple report is not available yet; retry scheduled.",
        }
        fact["Record Key"] = _record_key(fact)
        facts.append(fact)
    return sorted(facts, key=lambda fact: (fact["Date"], fact["Record Key"]))


def _facts_values(facts: list[dict[str, Any]]) -> list[list[Any]]:
    return [["" if fact.get(header) is None else fact.get(header, "") for header in FACT_HEADERS] for fact in facts]


def _status_by_date(facts: list[dict[str, Any]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for fact in facts:
        current = statuses.get(fact["Date"])
        status = fact["Data Status"]
        if current is None or status == "complete":
            statuses[fact["Date"]] = status
    return statuses


def _total_values(days: list[date], facts: list[dict[str, Any]]) -> list[list[Any]]:
    status = _status_by_date(facts)
    retrieved = {fact["Date"]: fact["Retrieved At"] for fact in facts}
    sums: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fact in facts:
        if fact["Aggregation Scope"] == "TOTAL" or fact["Data Status"] != "complete":
            continue
        for field in ("Impressions", "Product Page Views", "First-Time Downloads", "Total Downloads", "Redownloads"):
            if fact.get(field) is not None:
                sums[fact["Date"]][field] += int(fact[field])
    rows: list[list[Any]] = []
    for row_number, day in enumerate(days, start=2):
        aggregate = sums.get(day.isoformat(), {})
        rows.append([
            day.isoformat(), status.get(day.isoformat(), "pending"), "",
            aggregate.get("Impressions", ""), "", aggregate.get("Product Page Views", ""),
            aggregate.get("First-Time Downloads", ""), aggregate.get("Total Downloads", ""),
            aggregate.get("Redownloads", ""), "",
            f'=IF(OR(B{row_number}<>"complete",C{row_number}=""),"",D{row_number}/C{row_number})',
            f'=IF(OR(B{row_number}<>"complete",E{row_number}=""),"",F{row_number}/E{row_number})',
            f'=IF(OR(B{row_number}<>"complete",C{row_number}="",C{row_number}=0),"",E{row_number}/C{row_number})',
            f'=IF(OR(B{row_number}<>"complete",E{row_number}="",E{row_number}=0),"",G{row_number}/E{row_number})',
            f'=IF(OR(B{row_number}<>"complete",C{row_number}="",C{row_number}=0),"",G{row_number}/C{row_number})',
            retrieved.get(day.isoformat(), ""),
        ])
    return rows


def _commerce_daily(files: list[ReportFile], days: list[date]) -> dict[str, dict[str, int]]:
    """Calendar-date trial and paid events from Apple's reports."""
    available_dates = {day.isoformat() for day in days}
    newest: dict[tuple[str, str], ReportFile] = {}
    for file in files:
        for item in file.rows:
            source_date = str(item.get("Event Date") or item.get("Date") or "")
            if source_date not in available_dates:
                continue
            key = (file.name, source_date)
            if key not in newest or file.processing_date >= newest[key].processing_date:
                newest[key] = file

    metrics: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for file in {item.instance_id: item for item in newest.values()}.values():
        for item in file.rows:
            source_date = str(item.get("Event Date") or item.get("Date") or "")
            if newest.get((file.name, source_date)) is not file:
                continue
            if file.name == "App Store Purchases Standard":
                sales = float(str(item.get("Sales in USD") or "0").replace(",", "") or 0)
                if sales > 0:
                    metrics[source_date]["Direct Paid Purchases"] += _number(item, "Purchases") or 0
                continue
            if file.name != "App Store Subscription Event Report Standard":
                continue
            event_group = str(item.get("Event Group") or "").lower()
            event_name = str(item.get("Event Name") or "").lower()
            count = _number(item, "Counts") or 0
            if event_group == "offer start" and "free trial" in event_name:
                metrics[source_date]["Free Trial Starts"] += count
            elif event_group == "conversion to standard price" and "introductory offer" in event_name:
                metrics[source_date]["Paid from Trial"] += count
    return metrics


def _complete_funnel_dates(files: list[ReportFile], days: list[date]) -> set[str]:
    """Dates for which Apple has published both top-funnel report types.

    Apple publishes Downloads and Discovery reports independently. A date with
    only one of them is not a usable funnel row, even if one metric is present.
    """
    expected = {day.isoformat() for day in days}
    coverage: dict[str, set[str]] = defaultdict(set)
    source_reports_seen = False
    for file in files:
        if "App Store Discovery and Engagement" in file.name:
            report_type = "engagement"
        elif "App Downloads" in file.name:
            report_type = "downloads"
        else:
            continue
        source_reports_seen = True
        for item in file.rows:
            source_date = str(item.get("Date") or "")
            if source_date in expected:
                coverage[source_date].add(report_type)
    if not source_reports_seen:
        return expected
    return {source_date for source_date, types in coverage.items() if types == {"engagement", "downloads"}}


def _funnel_values(
    days: list[date], facts: list[dict[str, Any]], files: list[ReportFile] | None = None,
) -> list[list[Any]]:
    """Apple calendar funnel; downstream ratios are explicitly daily snapshots."""
    commerce = _commerce_daily(files or [], days)
    complete_dates = _complete_funnel_dates(files or [], days)
    sums: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fact in facts:
        if fact["Aggregation Scope"] == "TOTAL" or fact["Data Status"] != "complete":
            continue
        for field in ("Impressions", "Product Page Views", "First-Time Downloads"):
            value = fact.get(field)
            if value not in (None, ""):
                sums[fact["Date"]][field] += int(value)

    rows: list[list[Any]] = []
    for row_number, day in enumerate(days, start=3):
        if day.isoformat() not in complete_dates:
            rows.append([day.isoformat(), "", "", "", "", "", "", "", "", "", ""])
            continue
        aggregate = sums.get(day.isoformat(), {})
        commercial = commerce.get(day.isoformat(), {})
        rows.append([
            day.isoformat(),
            aggregate.get("Impressions", ""),
            f'=IF(D{row_number}="";"";IFERROR(D{row_number}/B{row_number};""))',
            aggregate.get("Product Page Views", ""),
            f'=IF(F{row_number}="";"";IFERROR(F{row_number}/D{row_number};""))',
            aggregate.get("First-Time Downloads", ""),
            f'=IF(F{row_number}="";"";IFERROR(H{row_number}/F{row_number};""))',
            commercial.get("Free Trial Starts", 0),
            f'=IF(H{row_number}=0;"";IFERROR(J{row_number}/H{row_number};""))',
            commercial.get("Paid from Trial", 0),
            commercial.get("Direct Paid Purchases", 0),
        ])

    end_row = len(rows) + 2
    return [[
        "TOTAL", f'=SUM(B3:B{end_row})', '=IF(D2="";"";IFERROR(D2/B2;""))',
        f'=SUM(D3:D{end_row})', '=IF(F2="";"";IFERROR(F2/D2;""))',
        f'=IF(COUNTA(F3:F{end_row})=0;"";SUM(F3:F{end_row}))',
        '=IF(F2=0;"";IFERROR(H2/F2;""))', f'=SUM(H3:H{end_row})',
        '=IF(H2=0;"";IFERROR(J2/H2;""))', f'=SUM(J3:J{end_row})',
        f'=SUM(K3:K{end_row})',
    ], *rows]


def _asa_values(days: list[date], facts: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for fact in facts:
        # A campaign token proves App Analytics campaign attribution. App Store
        # Search alone is not ASA because Apple explicitly includes organic search.
        if not fact.get("Campaign ID"):
            continue
        row_number = len(rows) + 2
        rows.append([
            fact["Date"], fact["Data Status"], fact["Source Type"], fact["Campaign ID"],
            fact["Campaign ID"], fact["Territory"], fact["Unique Impressions"] or "",
            fact["Impressions"] or "", fact["Unique Product Page Views"] or "",
            fact["Product Page Views"] or "", fact["First-Time Downloads"] or "",
            fact["Total Downloads"] or "", fact["Redownloads"] or "", "",
            f'=IF(OR(B{row_number}<>"complete",G{row_number}="",G{row_number}=0),"",I{row_number}/G{row_number})',
            f'=IF(OR(B{row_number}<>"complete",I{row_number}="",I{row_number}=0),"",K{row_number}/I{row_number})',
            f'=IF(OR(B{row_number}<>"complete",G{row_number}="",G{row_number}=0),"",K{row_number}/G{row_number})',
            "attributed", fact["Retrieved At"],
        ])
    if rows:
        return rows
    # App Store search combines organic search and Search Ads. Until an Apple
    # Ads API or a confirmed campaign token exists, keep ASA explicitly empty.
    return [
        [
            day.isoformat(), "unavailable_requires_apple_ads_api", "", "", "", "",
            "", "", "", "", "", "", "", "", "", "", "",
            "not_attributed", "",
        ]
        for day in days
    ]


def _viral_values(days: list[date], facts: list[dict[str, Any]]) -> list[list[Any]]:
    statuses = _status_by_date(facts)
    rows: list[list[Any]] = []
    for row_number, day in enumerate(days, start=2):
        rows.append([
            day.isoformat(),
            f'=IF(COUNTIF(\'Marketing Daily\'!A:A,A{row_number})>0,"complete","no_data")',
            f'=IFNA(XLOOKUP(A{row_number},\'Marketing Daily\'!A:A,\'Marketing Daily\'!B:B),"")',
            f'=IFNA(XLOOKUP(A{row_number},\'Marketing Daily\'!A:A,\'Marketing Daily\'!C:C),"")',
            f'=IFNA(XLOOKUP(A{row_number},\'Marketing Daily\'!A:A,\'Marketing Daily\'!D:D),"")',
            statuses.get(day.isoformat(), "pending"), "store-reported; unattributed",
            "", "", "", "", "",
            f'=IF(OR(F{row_number}<>"complete",H{row_number}="",H{row_number}=0),"",J{row_number}/H{row_number})',
            f'=IF(OR(F{row_number}<>"complete",J{row_number}="",J{row_number}=0),"",L{row_number}/J{row_number})',
            "correlational", "Viral views are not user-level attributed to App Store outcomes.",
        ])
    return rows


def _overview_values(days: list[date], facts: list[dict[str, Any]]) -> list[list[Any]]:
    statuses = _status_by_date(facts)
    rows: list[list[Any]] = []
    channels = (
        ("Viral Content", "correlational", "Marketing Daily + store total"),
        ("Apple Search Ads", "attributed", "campaign token only"),
        ("Organic Store", "store-reported", "App Store source rows"),
        ("Store Total", "aggregate", "App Store TOTAL"),
    )
    for day in days:
        for channel, relationship, scope in channels:
            row_number = len(rows) + 2
            marketing = (
                f'=IFNA(XLOOKUP(A{row_number},\'Marketing Daily\'!A:A,\'Marketing Daily\'!B:B),"")'
                if channel == "Viral Content" else ""
            )
            rows.append([
                day.isoformat(), channel, relationship, statuses.get(day.isoformat(), "pending"),
                marketing, "", "", "", "",
                f'=IF(OR(C{row_number}<>"attributed",E{row_number}="",E{row_number}=0,F{row_number}=""),"",F{row_number}/E{row_number})',
                f'=IF(OR(F{row_number}="",F{row_number}=0,G{row_number}=""),"",G{row_number}/F{row_number})',
                f'=IF(OR(G{row_number}="",G{row_number}=0,H{row_number}=""),"",H{row_number}/G{row_number})',
                f'=IF(OR(F{row_number}="",F{row_number}=0,H{row_number}=""),"",H{row_number}/F{row_number})',
                scope, "No user-level attribution without campaign token or Custom Product Page.",
            ])
    return rows


def _quality_values(facts: list[dict[str, Any]]) -> list[list[Any]]:
    now = datetime.now(timezone.utc).isoformat()
    rows: list[list[Any]] = []
    for fact in facts:
        if fact["Data Status"] not in {"pending", "pre_release_no_data"}:
            continue
        rows.append([
            now, fact["Date"], fact["Aggregation Scope"],
            "PRE_RELEASE_NO_DATA" if fact["Data Status"] == "pre_release_no_data" else "PENDING_APPLE_DATA",
            "info", "open", "Apple report rows", fact["Data Status"], fact["Record Key"],
            fact["Notes"], now, now, "",
        ])
    if any(fact.get("Unique Impressions") is not None and fact["Aggregation Scope"] != "TOTAL" for fact in facts):
        rows.append([
            now, "", "source", "UNIQUE_AGGREGATED_ACROSS_DIMENSIONS", "warning", "open",
            "Do not sum unique source rows", "source-grain unique metrics present", "",
            "Store Total unique metrics stay blank until an authoritative all-up total exists.",
            now, now, "",
        ])
    rows.append([
        now, "", "ASA", "ASA_SOURCE_UNAVAILABLE", "warning", "open",
        "Apple Ads API or confirmed campaign token", "App Store search mixes paid and organic", "",
        "ASA is intentionally not inferred from App Store search.", now, now, "",
    ])
    return rows


def sync_app_store_analytics(*, start_date: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    start = date.fromisoformat(start_date or config.APPSTORE_ANALYTICS_START_DATE)
    end = (
        date.fromisoformat(target_date)
        if target_date
        else datetime.now(ZoneInfo(config.APPSTORE_ANALYTICS_TIMEZONE)).date() - timedelta(days=1)
    )
    if end < start:
        return {"status": "no_dates", "start": start.isoformat(), "end": end.isoformat()}

    client = AppStoreAnalyticsClient()
    app = client.app()
    pre_release = client.first_release_pending(str(app["id"]))
    files = client.report_files(str(app["id"]))
    facts = _extract_facts(app, files, start, end)
    days = _date_range(start, end)
    facts = _add_availability_rows(app, facts, days, pre_release)

    book = _open_spreadsheet(_get_client())
    payloads = {FUNNEL_SHEET: (FUNNEL_HEADERS, _funnel_values(days, facts, files))}
    for title, (headers, rows) in payloads.items():
        worksheet = _sheet(book, title, headers)
        if title == FUNNEL_SHEET:
            worksheet.batch_clear([f"H2:K{worksheet.row_count}"])
        _replace(worksheet, headers, rows)
        if rows:
            percent_columns = {
                TOTAL_SHEET: ("K", "O"), ASA_SHEET: ("O", "Q"),
                FUNNEL_SHEET: ("C", "I"),
                VIRAL_SHEET: ("M", "N"), OVERVIEW_SHEET: ("J", "M"),
            }.get(title)
            if percent_columns:
                if title == FUNNEL_SHEET:
                    for column in ("C", "E", "G", "I"):
                        worksheet.format(
                            f"{column}2:{column}{len(rows)+1}",
                            {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
                        )
                    for column in ("B", "D", "F", "H", "J", "K"):
                        worksheet.format(
                            f"{column}2:{column}{len(rows)+1}",
                            {"numberFormat": {"type": "NUMBER", "pattern": "0"}},
                        )
                else:
                    worksheet.format(
                        f"{percent_columns[0]}2:{percent_columns[1]}{len(rows)+1}",
                        {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
                    )

    status = "success" if files else ("pre_release_no_data" if pre_release else "pending")
    return {
        "status": status, "app_id": str(app["id"]), "start": start.isoformat(),
        "end": end.isoformat(), "dates": len(days), "facts": len(facts), "report_files": len(files),
    }
