from __future__ import annotations

from datetime import date

from src.services.app_store_analytics_service import (
    ReportFile,
    _add_availability_rows,
    _extract_facts,
    _funnel_values,
    _is_supported_report,
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


def test_funnel_values_have_total_row_and_step_conversions():
    engagement = ReportFile(
        "App Store Discovery and Engagement", "eng", "2026-07-10",
        [
            {"Date": "2026-07-07", "Source Type": "App Store search", "Territory": "US", "Device": "iPhone", "Event": "Impression", "Page Type": "No page", "Counts": "10"},
            {"Date": "2026-07-07", "Source Type": "App Store search", "Territory": "US", "Device": "iPhone", "Event": "Page View", "Page Type": "Product Page", "Counts": "4"},
        ],
    )
    downloads = ReportFile(
        "App Store Downloads", "downloads", "2026-07-10",
        [{"Date": "2026-07-07", "Source Type": "App Store search", "Territory": "US", "Device": "iPhone", "Download Type": "First-time Download", "Counts": "2"}],
    )
    facts = _extract_facts(APP, [engagement, downloads], date(2026, 7, 7), date(2026, 7, 7))

    funnel = _funnel_values([date(2026, 7, 7)], facts)

    assert funnel[0][0] == "TOTAL"
    assert funnel[0][1] == "=SUM(B3:B3)"
    assert funnel[1][1] == 10
    assert funnel[1][3] == 4
    assert funnel[1][5] == 2
    assert funnel[1][2].startswith("=IF(")


def test_funnel_values_accept_blank_strings_from_a_sheet_readback():
    facts = [{
        "Date": "2026-07-07", "Aggregation Scope": "source", "Data Status": "complete",
        "Retrieved At": "2026-07-10T00:00:00+00:00", "Impressions": "10",
        "Product Page Views": "", "First-Time Downloads": "",
    }]

    funnel = _funnel_values([date(2026, 7, 7)], facts)

    assert funnel[1][1] == 10
    assert funnel[1][3] == ""


def test_funnel_leaves_partially_published_apple_date_blank():
    downloads = ReportFile(
        "App Downloads Standard", "downloads", "2026-07-20",
        [{"Date": "2026-07-19", "Download Type": "First-time Download", "Counts": "18"}],
    )
    facts = [{
        "Date": "2026-07-19", "Aggregation Scope": "source", "Data Status": "complete",
        "Impressions": "", "Product Page Views": "", "First-Time Downloads": "18",
    }]

    funnel = _funnel_values([date(2026, 7, 19)], facts, [downloads])

    assert funnel[1] == ["2026-07-19", "", "", "", "", "", "", "", "", "", ""]


def test_funnel_includes_apple_trial_starts_on_their_event_dates():
    facts = [{
        "Date": "2026-07-07", "Aggregation Scope": "source", "Data Status": "complete",
        "Impressions": "", "Product Page Views": "", "First-Time Downloads": "",
    }]
    trials = ReportFile(
        "App Store Subscription Event Report Standard", "trials", "2026-07-10",
        [{"Event Date": "2026-07-07", "Event Group": "Offer start", "Event Name": "Free trial start activation", "Counts": "3"}],
    )
    funnel = _funnel_values([date(2026, 7, 7)], facts, [trials])

    assert funnel[1][6:] == ['=IF(F3="";"";IFERROR(H3/F3;""))', 3, '=IF(H3=0;"";IFERROR(J3/H3;""))', 0, 0]


def test_supported_reports_include_current_apple_download_report_name():
    assert _is_supported_report("App Store Discovery and Engagement Standard")
    assert _is_supported_report("App Downloads Standard")
    assert _is_supported_report("App Store Purchases Standard")
    assert _is_supported_report("App Store Subscription Event Report Standard")
    assert not _is_supported_report("App Downloads Detailed")
