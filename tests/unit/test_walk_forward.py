"""walk_forward.py 單元測試。

特別驗證 v2 修正：訓練期建立之部位不應將浮動損益洩漏到測試期報酬。
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from twquant.backtest.walk_forward import (
    _chain_equity_curves,
    _warmup_strategy,
    make_windows,
    run_walk_forward,
)
from twquant.data.session import TAIPEI
from twquant.events import Direction
from twquant.strategies.ema_crossover import EmaCrossover


def _make_daily_bars(prices: list[float]) -> pd.DataFrame:
    """產生每日 1d bars。"""
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


class TestMakeWindows:
    def test_basic_window_generation(self):
        ws = make_windows(date(2020, 1, 1), date(2023, 1, 1),
                          train_months=12, test_months=6)
        assert len(ws) == 4
        assert ws[0].train_start == date(2020, 1, 1)
        assert ws[0].test_start == date(2021, 1, 1)
        assert ws[0].test_end == date(2021, 7, 1)


class TestWarmup:
    def test_warmup_updates_ema_state_without_signals(self):
        strat = EmaCrossover(fast_period=3, slow_period=5, timeframe="1d")
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108]
        bars = _make_daily_bars(prices)
        _warmup_strategy(strat, bars)
        assert strat._fast.ready
        assert strat._slow.ready

    def test_warmup_resets_position_target(self):
        strat = EmaCrossover(fast_period=2, slow_period=4, timeframe="1d")
        prices = [100, 100, 100, 100, 200, 200, 200]
        bars = _make_daily_bars(prices)
        _warmup_strategy(strat, bars)
        assert strat.current_target == Direction.FLAT


class TestChainEquity:
    def test_chains_compound_capital(self):
        seg1 = pd.DataFrame({"ts": pd.date_range("2024-01-01", periods=3, tz=TAIPEI),
                             "equity": [1_000_000, 1_100_000, 1_200_000]})
        seg2 = pd.DataFrame({"ts": pd.date_range("2024-02-01", periods=3, tz=TAIPEI),
                             "equity": [1_000_000, 1_050_000, 1_100_000]})
        chained = _chain_equity_curves([seg1, seg2], initial_cash=1_000_000)
        assert chained["equity"].iloc[0] == 1_000_000
        assert chained["equity"].iloc[2] == pytest.approx(1_200_000)
        assert chained["equity"].iloc[3] == pytest.approx(1_200_000)
        assert chained["equity"].iloc[-1] == pytest.approx(1_320_000)


class TestNoSignalLeak:
    """關鍵回歸：訓練期建立的浮動部位不該讓測試期出現「0 trades 卻有報酬」。"""

    def test_flat_test_period_has_zero_return_when_no_trades(self):
        train_prices = [100.0] * 5 + list(range(100, 200, 5))
        test_prices = [200.0] * 60
        all_prices = train_prices + test_prices
        bars = _make_daily_bars(all_prices)

        result = run_walk_forward(
            bars,
            strategy_factory=lambda: EmaCrossover(fast_period=3, slow_period=10, timeframe="1d"),
            train_months=1, test_months=1, initial_cash=1_000_000,
        )

        if result.per_window_metrics:
            for m in result.per_window_metrics:
                if m.num_trades == 0:
                    assert abs(m.total_return_pct) < 0.01, \
                        f"訊號洩漏：0 trades 卻有 {m.total_return_pct:.4f}% 報酬"
