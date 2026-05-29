"""grid_search.py 單元測試。"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from twquant.backtest.grid_search import GridResult, results_to_df, run_grid_search, verdict
from twquant.data.session import TAIPEI


def _make_daily_bars(prices: list[float]) -> pd.DataFrame:
    t0 = datetime(2018, 1, 2, 13, 45, tzinfo=TAIPEI)
    rows = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i > 0 else p
        rows.append({
            "ts": t0 + timedelta(days=i),
            "timeframe": "1d",
            "product": "TX",
            "contract_month": "CONT",
            "open": float(prev),
            "high": max(prev, p),
            "low": min(prev, p),
            "close": float(p),
            "volume": 1000,
        })
    return pd.DataFrame(rows)


# 足夠長的假資料：上漲→橫盤→下跌，讓 walk-forward 有多個窗口
_PRICES = (
    [100.0] * 20
    + list(range(100, 160, 2))   # 30 根漲
    + [160.0] * 30               # 橫盤
    + list(range(160, 110, -2))  # 25 根跌
    + [110.0] * 20
) * 2   # 重複兩次，確保有足夠日數


class TestRunGridSearch:
    def test_returns_list_of_grid_results(self):
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[2, 3],
            slow_periods=[5],
            train_months=3,
            test_months=1,
        )
        assert len(results) > 0
        assert all(isinstance(r, GridResult) for r in results)

    def test_skips_fast_ge_slow(self):
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[5],
            slow_periods=[3, 5],   # 5>=3 and 5>=5 → all skipped
            train_months=3,
            test_months=1,
        )
        assert results == []

    def test_sorted_by_oos_sharpe_descending(self):
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[2, 3, 5],
            slow_periods=[8, 10],
            train_months=3,
            test_months=1,
        )
        sharpes = [r.oos_sharpe for r in results]
        assert sharpes == sorted(sharpes, reverse=True)

    def test_atr_period_zero_sets_atr_mult_zero(self):
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[2],
            slow_periods=[5],
            atr_periods=[0],
            atr_mults=[1.5, 2.0, 3.0],  # 應被忽略（atr_period=0）
            train_months=3,
            test_months=1,
        )
        # atr_period=0 時只會有一個組合，atr_mult 強制為 0.0
        assert len(results) == 1
        assert results[0].atr_period == 0
        assert results[0].atr_mult == 0.0

    def test_atr_sweep_produces_correct_count(self):
        """fast=2, slow=5 + atr_periods=[0,3] + atr_mults=[1.5,2.0]
        → (0: 1組) + (3: 2組) = 3 組，而非 2*2=4 組。"""
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[2],
            slow_periods=[5],
            atr_periods=[0, 3],
            atr_mults=[1.5, 2.0],
            train_months=3,
            test_months=1,
        )
        assert len(results) == 3  # (0,0.0), (3,1.5), (3,2.0)

    def test_timeframe_recorded_in_result(self):
        bars = _make_daily_bars(_PRICES)
        results = run_grid_search(
            bars, "1d",
            fast_periods=[2],
            slow_periods=[5],
            train_months=3,
            test_months=1,
        )
        assert all(r.timeframe == "1d" for r in results)


class TestResultsToDf:
    def _sample_result(self, atr_period=0, atr_mult=0.0):
        return GridResult(
            fast=5, slow=20, trend=0,
            atr_period=atr_period, atr_mult=atr_mult,
            timeframe="15m",
            oos_total_return_pct=10.0,
            oos_sharpe=0.8,
            oos_calmar=0.5,
            oos_max_drawdown_pct=-15.0,
            oos_trades=12,
            oos_win_rate_pct=55.0,
        )

    def test_returns_dataframe(self):
        df = results_to_df([self._sample_result()])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1

    def test_atr_mult_shown_as_dash_when_disabled(self):
        df = results_to_df([self._sample_result(atr_period=0)])
        assert df["atr_mult"].iloc[0] == "-"

    def test_atr_mult_shown_as_float_when_enabled(self):
        df = results_to_df([self._sample_result(atr_period=14, atr_mult=2.0)])
        assert df["atr_mult"].iloc[0] == 2.0


class TestVerdict:
    def _make_results(self, sharpes: list[float]) -> list[GridResult]:
        return [
            GridResult(
                fast=2, slow=5, trend=0, atr_period=0, atr_mult=0.0, timeframe="1d",
                oos_total_return_pct=0, oos_sharpe=s, oos_calmar=0,
                oos_max_drawdown_pct=0, oos_trades=0, oos_win_rate_pct=0,
            )
            for s in sharpes
        ]

    def test_empty_returns_no_results(self):
        assert verdict([]) == "no results"

    def test_all_negative_says_dead(self):
        v = verdict(self._make_results([-0.5, -0.3, -0.1]))
        assert "全軍覆沒" in v

    def test_sparse_positive_says_overfit(self):
        v = verdict(self._make_results([-0.5] * 9 + [0.3]))  # 10% positive
        assert "過擬合" in v

    def test_majority_positive_says_edge(self):
        v = verdict(self._make_results([0.6] * 7 + [-0.1] * 3))  # 70% positive
        assert "edge" in v
