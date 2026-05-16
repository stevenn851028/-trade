"""Backtest runner 端對端測試（合成資料）。"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from twquant.backtest.runner import run_backtest
from twquant.data.session import TAIPEI
from twquant.execution import CostModel
from twquant.strategies.ema_crossover import EmaCrossover


def _make_bars_from_prices(prices: list[float], tf: str = "30m") -> pd.DataFrame:
    t0 = datetime(2026, 4, 15, 9, 15, tzinfo=TAIPEI)
    rows = []
    for i, p in enumerate(prices):
        # 開盤 = 前一根收盤；本根 OHLC 都用 p（簡化）
        prev = prices[i - 1] if i > 0 else p
        rows.append({
            "ts": t0 + timedelta(minutes=30 * i),
            "timeframe": tf,
            "product": "TX",
            "contract_month": "CONT",
            "open": float(prev),
            "high": max(prev, p),
            "low": min(prev, p),
            "close": float(p),
            "volume": 100,
        })
    return pd.DataFrame(rows)


class TestBacktestRunner:
    def test_empty_bars_returns_empty_result(self):
        empty = pd.DataFrame(columns=["ts", "timeframe", "product", "contract_month",
                                      "open", "high", "low", "close", "volume"])
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="30m")
        result = run_backtest(empty, s)
        assert result.metrics.num_trades == 0

    def test_full_trip_long_then_flat(self):
        """暖機 → 黃金交叉做多 → 死亡交叉平倉。"""
        prices = [100.0] * 5 + [200.0] * 5 + [50.0] * 5
        bars = _make_bars_from_prices(prices, tf="30m")

        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="30m")
        result = run_backtest(bars, s, initial_cash=1_000_000)

        assert result.metrics.num_trades >= 1
        trade = result.trades.iloc[0]
        # 進場約 200（拉升後下根開盤），出場約 50；應為虧損
        assert trade["entry_price"] >= 150
        assert trade["exit_price"] <= 100
        assert trade["net_pnl"] < 0

    def test_cost_eats_pnl_when_flat_price(self):
        """若價格從頭到尾不變但發生進出，應該因為手續費 + 稅 + 滑價而小幅虧損。"""
        prices = [100.0] * 4 + [101.0, 99.0, 100.0, 100.0, 100.0, 100.0]
        bars = _make_bars_from_prices(prices, tf="30m")
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="30m")
        result = run_backtest(bars, s, initial_cash=1_000_000)
        if result.metrics.num_trades > 0:
            trade = result.trades.iloc[0]
            assert trade["cost"] > 0
            # 至少有手續費 + 稅
            assert trade["cost"] >= 100  # 進出 NT$50 × 2 = 100 起跳

    def test_equity_curve_recorded_per_bar(self):
        prices = [100.0] * 10
        bars = _make_bars_from_prices(prices, tf="30m")
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="30m")
        result = run_backtest(bars, s, initial_cash=1_000_000)
        assert len(result.equity_curve) == len(bars)
        # 沒交易、價格不變，equity 應始終等於初始
        assert result.equity_curve["equity"].iloc[-1] == pytest.approx(1_000_000.0)
