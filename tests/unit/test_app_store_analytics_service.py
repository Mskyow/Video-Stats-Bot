from __future__ import annotations

from datetime import date

from src.services.app_store_analytics_service import (
    ReportFile,
    _add_availability_rows,
    _extract_facts,
    _total_values,
)


APP = {"id": "6768618577", "attributes": {"bundleId": "com.relationships.otty"}}


def test_pre_release_rows_keep_metrics_blank():
    facts = _add_availability_rows(APP, [], [date(2026, 7, 7)], pre_release=True)

    assert facts[0]["Data Status"] == "pre_release_no_data"
    assert facts[0]["Unique Impressions"] is None
    assert facts[0]["First-Time Downloads"] is None
    assert facts[0]["Purchases"] is None


def test_newest_processing_date_replaces_older_download_correction():
    old = ReportFile(
        "App Store Downloads", "old", "2026-07-10",
        [{"Date": "2026-07-07", "Source Type": "Web referrer", "Territory": "US", "Device": "iPhone", "Download Type": "First-time Download", "Counts": "3"}],
    )
    corrected = ReportFile(
        "App Store Downloads", "new", "2026-07-11",
        [{"Date": "2026-07-07", "Source Type": "Web referrer", "Territory": "US", "Device": "iPhone", "Download Type": "First-time Download", "Counts": "5"}],
    )

    facts = _extract_facts(APP, [old, corrected], date(2026, 7, 7), date(2026, 7, 7))

    assert len(facts) == 1
    assert facts[0]["First-Time Downloads"] == 5
    assert facts[0]["Total Downloads"] == 5
    assert facts[0]["Apple Report Instance ID"] == "new"


def test_unique_metrics_remain_source_grain_and_store_total_unique_is_blank():
    engagement = ReportFile(
        "App Store Discovery and Engagement", "eng", "2026-07-10",
        [
            {"Date": "2026-07-07", "Source Type": "App Store search", "Territory": "US", "Device": "iPhone", "Event": "Impression", "Page Type": "No page", "Counts": "10", "Unique Counts": "8"},
            {"Date": "2026-07-07", "Source Type": "Web referrer", "Territory": "US", "Device": "iPhone", "Event": "Impression", "Page Type": "No page", "Counts": "7", "Unique Counts": "6"},
        ],
    )
    facts = _extract_facts(APP, [engagement], date(2026, 7, 7), date(2026, 7, 7))
    totals = _total_values([date(2026, 7, 7)], facts)

    assert sorted(fact["Unique Impressions"] for fact in facts) == [6, 8]
    assert totals[0][2] == ""  # unique impressions are not summed across sources
    assert totals[0][3] == 17  # event counts are additive
    assert totals[0][14].startswith("=IF(")
