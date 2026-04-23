"""bar_aggregator.py 單元測試（使用合成 tick 資料）。"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from twquant.data.bar_aggregator import aggregate_bars_to_higher, aggregate_ticks_to_bars
from twquant.data.session import TAIPEI, day_session, night_session


def make_tick(ts: datetime, price: float, volume: int = 1,
              product: str = "TX", month: str = "202606") -> dict:
    return {
        "ts": ts,
        "product": product,
        "contract_month": month,
        "price": price,
        "volume": volume,
        "is_opening_auction": False,
    }


def synthesize_day_ticks(trade_date: date, base_price: float = 17000.0,
                         tick_per_minute: int = 1) -> pd.DataFrame:
    """產生一日完整日盤 + 夜盤的合成 ticks，每分鐘 N 筆。"""
    ticks = []
    for sess in (day_session(trade_date), night_session(trade_date)):
        total_min = int((sess.end - sess.start).total_seconds() // 60)
        for minute in range(total_min + 1):  # inclusive of close
            ts = sess.start + timedelta(minutes=minute)
            for i in range(tick_per_minute):
                ticks.append(make_tick(
                    ts + timedelta(seconds=i * 5),
                    price=base_price + (minute % 50),
                    volume=1,
                ))
    return pd.DataFrame(ticks)


class TestAggregation:
    def test_empty_df_returns_empty_schema(self):
        bars = aggregate_ticks_to_bars(
            pd.DataFrame(columns=["ts", "product", "contract_month", "price",
                                  "volume", "is_opening_auction"]),
            bar_size_min=15,
        )
        assert list(bars.columns) == ["ts", "timeframe", "product", "contract_month",
                                      "open", "high", "low", "close", "volume"]
        assert bars.empty

    def test_single_day_15m_produces_76_bars(self):
        ticks = synthesize_day_ticks(date(2026, 4, 15), tick_per_minute=2)
        bars = aggregate_ticks_to_bars(ticks, 15)
        assert len(bars) == 76

    def test_single_day_30m_produces_38_bars(self):
        ticks = synthesize_day_ticks(date(2026, 4, 15), tick_per_minute=2)
        bars = aggregate_ticks_to_bars(ticks, 30)
        assert len(bars) == 38

    def test_ohlc_computed_correctly(self):
        d = date(2026, 4, 15)
        sess = day_session(d)
        # 第一根 bar 範圍 (08:45, 09:00]，塞 5 筆，價格 100,110,90,105,102
        times = [sess.start + timedelta(minutes=i) for i in [0, 3, 6, 9, 14]]
        prices = [100.0, 110.0, 90.0, 105.0, 102.0]
        volumes = [1, 2, 3, 4, 5]
        ticks = pd.DataFrame([
            make_tick(t, p, v) for t, p, v in zip(times, prices, volumes)
        ])
        bars = aggregate_ticks_to_bars(ticks, 15)
        first = bars.iloc[0]
        assert first.open == 100.0
        assert first.high == 110.0
        assert first.low == 90.0
        assert first.close == 102.0
        assert first.volume == 1 + 2 + 3 + 4 + 5

    def test_ticks_outside_session_are_dropped(self):
        d = date(2026, 4, 15)
        ticks = pd.DataFrame([
            make_tick(datetime(2026, 4, 15, 14, 0, tzinfo=TAIPEI), 17000, 1),  # 非交易時段
            make_tick(datetime(2026, 4, 15, 8, 50, tzinfo=TAIPEI), 17000, 1),  # 日盤
        ])
        bars = aggregate_ticks_to_bars(ticks, 15)
        assert len(bars) == 1
        assert bars.iloc[0].volume == 1

    def test_bars_sorted_by_ts(self):
        ticks = synthesize_day_ticks(date(2026, 4, 15), tick_per_minute=1)
        bars = aggregate_ticks_to_bars(ticks, 30)
        diffs = bars["ts"].diff().dropna()
        assert all(d > pd.Timedelta(0) for d in diffs)

    def test_30m_is_equivalent_to_rollup_of_15m(self):
        """核心性質：由 1m 聚合 30m 的結果，應等於先聚合 15m 再向上 rollup 的結果。"""
        ticks = synthesize_day_ticks(date(2026, 4, 15), tick_per_minute=3)
        direct_30m = aggregate_ticks_to_bars(ticks, 30)

        bars_15m = aggregate_ticks_to_bars(ticks, 15)
        rollup_30m = aggregate_bars_to_higher(bars_15m, 30)

        cols = ["ts", "product", "contract_month", "open", "high", "low", "close", "volume"]
        pd.testing.assert_frame_equal(
            direct_30m[cols].reset_index(drop=True),
            rollup_30m[cols].reset_index(drop=True),
            check_dtype=False,
        )
