"""quality.py 單元測試。"""

from datetime import date, timedelta

import pandas as pd

from twquant.data.bar_aggregator import aggregate_ticks_to_bars
from twquant.data.quality import check_bars
from twquant.data.session import TAIPEI, day_session


D = date(2026, 4, 15)


def _make_ticks(trade_date):
    ticks = []
    sess = day_session(trade_date)
    total_min = int((sess.end - sess.start).total_seconds() // 60)
    for m in range(total_min + 1):
        ticks.append({
            "ts": sess.start + timedelta(minutes=m),
            "product": "TX",
            "contract_month": "202604",
            "price": 17000.0 + m,
            "volume": 1,
            "is_opening_auction": False,
        })
    return pd.DataFrame(ticks)


class TestCheckBars:
    def test_clean_day_session_30m_reports_no_issues_ignoring_night(self):
        bars = aggregate_ticks_to_bars(_make_ticks(D), 30)
        report = check_bars(bars, D, 30, include_night=False)
        assert report.is_clean()
        assert report.actual_bars == 10
        assert report.expected_bars == 10
        assert report.missing_count == 0

    def test_missing_bars_detected(self):
        bars = aggregate_ticks_to_bars(_make_ticks(D), 30)
        bars_drop = bars.iloc[1:].reset_index(drop=True)  # 少了一根
        report = check_bars(bars_drop, D, 30, include_night=False)
        assert report.missing_count == 1
        assert not report.is_clean()

    def test_ohlc_violation_detected(self):
        bars = aggregate_ticks_to_bars(_make_ticks(D), 30).copy()
        bars.loc[0, "low"] = bars.loc[0, "high"] + 1  # 製造違規
        report = check_bars(bars, D, 30, include_night=False)
        assert report.ohlc_violations >= 1

    def test_gap_flag_when_open_diverges_from_prev_close(self):
        bars = aggregate_ticks_to_bars(_make_ticks(D), 30).copy()
        bars.loc[1, "open"] = bars.loc[0, "close"] * 1.10  # 10% 跳空 > 3% 門檻
        report = check_bars(bars, D, 30, include_night=False)
        assert report.gap_flags >= 1

    def test_summary_returns_string(self):
        bars = aggregate_ticks_to_bars(_make_ticks(D), 30)
        report = check_bars(bars, D, 30, include_night=False)
        assert "30m" in report.summary()
