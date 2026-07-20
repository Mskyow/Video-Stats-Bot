from datetime import date

from src.services.adapty_analytics_service import _cohort_rows


def test_cohort_rows_calculate_install_to_trial_and_paid():
    rows = _cohort_rows([date(2026, 7, 16)], {
        "2026-07-16": {"installs": 27, "trials_new": 2, "subscriptions_new": 1},
    })

    assert rows[0][0] == "TOTAL"
    assert rows[0][1] == "=SUM(B3:B3)"
    assert rows[1][:3] == ["2026-07-16", 27, 2]
    assert rows[1][3] == '=IF(B3=0;"";C3/B3)'
    assert rows[1][4:] == [1, '=IF(B3=0;"";E3/B3)']
