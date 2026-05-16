"""sqlite_store.py 單元測試。"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from twquant.data.bar_aggregator import aggregate_ticks_to_bars
from twquant.data.session import TAIPEI, day_session
from twquant.data.sqlite_store import BarStore, _from_epoch, _to_epoch


D = date(2026, 4, 15)


def _synth_ticks(d):
    ticks = []
    sess = day_session(d)
    total = int((sess.end - sess.start).total_seconds() // 60)
    for m in range(total + 1):
        ticks.append({
            "ts": sess.start + timedelta(minutes=m),
            "product": "TX",
            "contract_month": "202604",
            "price": 17000.0 + m,
            "volume": 1,
            "is_opening_auction": False,
        })
    return pd.DataFrame(ticks)


class TestEpochRoundtrip:
    def test_taipei_datetime_roundtrips(self):
        ts = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        assert _from_epoch(_to_epoch(ts)) == ts

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError):
            _to_epoch(datetime(2026, 4, 15, 9, 0))


class TestBarStore:
    def test_schema_created(self, tmp_path):
        with BarStore(tmp_path / "test.db") as store:
            cur = store.connect().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bars'"
            )
            assert cur.fetchone() is not None

    def test_upsert_then_query(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        with BarStore(tmp_path / "t.db") as store:
            n = store.upsert_bars(bars)
            assert n == len(bars)
            got = store.query_bars("TX", "30m")
            assert len(got) == len(bars)
            assert list(got["ts"]) == list(bars["ts"])
            assert got["open"].iloc[0] == bars["open"].iloc[0]

    def test_upsert_is_idempotent(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars)
            store.upsert_bars(bars)  # 重跑不該重複
            got = store.query_bars("TX", "30m")
            assert len(got) == len(bars)

    def test_upsert_updates_existing_row(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars)
            modified = bars.copy()
            modified.loc[0, "close"] = 99999.0
            store.upsert_bars(modified)
            got = store.query_bars("TX", "30m")
            assert got["close"].iloc[0] == 99999.0

    def test_query_time_range_filter(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars)
            mid = bars["ts"].iloc[5]
            got = store.query_bars("TX", "30m", start=mid, end=mid)
            assert len(got) == 1
            assert got["ts"].iloc[0] == mid

    def test_query_filters_by_month_code(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30).copy()
        bars2 = bars.copy()
        bars2["contract_month"] = "202605"
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars)
            store.upsert_bars(bars2)
            got_apr = store.query_bars("TX", "30m", month_code="202604")
            got_may = store.query_bars("TX", "30m", month_code="202605")
            assert len(got_apr) == len(bars)
            assert len(got_may) == len(bars2)
            assert set(got_apr["contract_month"]) == {"202604"}

    def test_list_trade_dates(self, tmp_path):
        bars_d1 = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        bars_d2 = aggregate_ticks_to_bars(_synth_ticks(D + timedelta(days=1)), 30)
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars_d1)
            store.upsert_bars(bars_d2)
            dates = store.list_trade_dates("TX", "30m")
            assert D in dates
            assert (D + timedelta(days=1)) in dates

    def test_stats_returns_counts_and_range(self, tmp_path):
        bars = aggregate_ticks_to_bars(_synth_ticks(D), 30)
        with BarStore(tmp_path / "t.db") as store:
            store.upsert_bars(bars)
            stats = store.stats()
            assert ("TX", "30m") in stats
            assert stats[("TX", "30m")]["count"] == len(bars)

    def test_empty_df_returns_zero(self, tmp_path):
        empty = pd.DataFrame(columns=["ts", "timeframe", "product", "contract_month",
                                       "open", "high", "low", "close", "volume"])
        with BarStore(tmp_path / "t.db") as store:
            assert store.upsert_bars(empty) == 0

    def test_missing_columns_raises(self, tmp_path):
        bad = pd.DataFrame({"foo": [1]})
        with BarStore(tmp_path / "t.db") as store:
            with pytest.raises(ValueError, match="missing columns"):
                store.upsert_bars(bad)
