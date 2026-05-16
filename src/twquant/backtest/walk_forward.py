"""Walk-forward 分析。

對 `docs/BACKTEST.md § Walk-Forward Analysis` 的最小實作：
- 用滾動窗口跑多次回測
- 每窗 = (train_period, test_period)
- Phase 1 策略無參數要 train（fast=10, slow=60 固定），所以 train 只用來暖機
- 把所有 test 段拼起來，得到準樣本外的權益曲線與指標

如此可以：
1. 驗證績效在不同時期穩定（不只靠單一區間運氣）
2. 為未來加入參數最佳化保留擴充點
3. 揭露策略對特定 regime（多頭 / 空頭 / 震盪）的依賴
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from twquant.backtest.metrics import PerformanceMetrics, compute_metrics
from twquant.backtest.runner import BacktestResult, run_backtest
from twquant.data.session import TAIPEI
from twquant.events import BarEvent
from twquant.execution import CostModel
from twquant.risk import RiskManager
from twquant.strategies.base import Strategy


@dataclass
class WalkForwardWindow:
    train_start: date
    train_end: date            # exclusive of test_start
    test_start: date
    test_end: date


@dataclass
class WalkForwardResult:
    strategy_name: str
    windows: list[WalkForwardWindow]
    per_window_metrics: list[PerformanceMetrics]
    combined_equity: pd.DataFrame      # ts, equity（拼接後）
    combined_trades: pd.DataFrame
    combined_metrics: PerformanceMetrics


def _months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month)


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # 月底日截斷
    from calendar import monthrange
    last = monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def make_windows(
    start: date,
    end: date,
    train_months: int,
    test_months: int,
) -> list[WalkForwardWindow]:
    """從 start 起，每個 test_months 推進一次，產出 train + test 配對。"""
    windows: list[WalkForwardWindow] = []
    test_start = _add_months(start, train_months)
    while test_start < end:
        test_end = min(_add_months(test_start, test_months), end)
        windows.append(WalkForwardWindow(
            train_start=_add_months(test_start, -train_months),
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
        ))
        test_start = test_end
    return windows


def _slice_bars(bars: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """取 [start, end) 區間（用 ts 的 Taipei 日期判斷）。"""
    if bars.empty:
        return bars
    ts_date = bars["ts"].dt.tz_convert(TAIPEI).dt.date \
        if hasattr(bars["ts"].dt, "tz_convert") and bars["ts"].dt.tz is not None \
        else bars["ts"].dt.date
    mask = (ts_date >= start) & (ts_date < end)
    return bars[mask].copy()


def run_walk_forward(
    bars: pd.DataFrame,
    strategy_factory,
    *,
    train_months: int = 24,
    test_months: int = 6,
    initial_cash: float = 1_000_000,
    cost_model: CostModel | None = None,
    risk_factory=None,  # callable returning a fresh RiskManager per window
) -> WalkForwardResult:
    """跑 walk-forward。

    Args:
        bars: 全期 bars（含 train + test 區間）
        strategy_factory: 每窗叫一次 `strategy_factory()` 產生全新 Strategy（避免狀態洩漏）
        train_months / test_months: 窗口大小
    """
    if bars.empty:
        empty = pd.DataFrame()
        return WalkForwardResult(
            strategy_name=strategy_factory().name,
            windows=[],
            per_window_metrics=[],
            combined_equity=empty,
            combined_trades=empty,
            combined_metrics=compute_metrics(empty, empty),
        )

    start_d = bars["ts"].min().date()
    end_d = bars["ts"].max().date() + timedelta(days=1)
    windows = make_windows(start_d, end_d, train_months, test_months)

    if not windows:
        # 不夠資料；退化為單窗
        rm = risk_factory() if risk_factory else None
        result = run_backtest(bars, strategy_factory(),
                              initial_cash=initial_cash,
                              cost_model=cost_model,
                              risk_manager=rm)
        return WalkForwardResult(
            strategy_name=result.strategy_name,
            windows=[],
            per_window_metrics=[result.metrics],
            combined_equity=result.equity_curve,
            combined_trades=result.trades,
            combined_metrics=result.metrics,
        )

    per_window_metrics: list[PerformanceMetrics] = []
    eq_pieces: list[pd.DataFrame] = []
    trade_pieces: list[pd.DataFrame] = []
    current_cash = initial_cash

    name = strategy_factory().name

    for w in windows:
        # 將整個 train+test 區間餵給策略（train 暖機、test 才計入結果）
        full_slice = _slice_bars(bars, w.train_start, w.test_end)
        if full_slice.empty:
            continue

        strat = strategy_factory()
        rm = risk_factory() if risk_factory else None
        # 暖機部分跑一遍，不計權益；用一個小技巧：跑完整 run 再切掉 train 段
        res = run_backtest(full_slice, strat,
                           initial_cash=current_cash,
                           cost_model=cost_model,
                           risk_manager=rm)

        # 切出 test 段
        eq = res.equity_curve.copy()
        eq_test = eq[eq["ts"].dt.tz_convert(TAIPEI).dt.date >= w.test_start].copy()
        # 重設權益起點（讓視覺上不會跳）；但統計用原始
        eq_pieces.append(eq_test)

        if not res.trades.empty:
            tr = res.trades.copy()
            tr_test = tr[tr["entry_ts"].dt.tz_convert(TAIPEI).dt.date >= w.test_start].copy()
            trade_pieces.append(tr_test)

        # 計算 test 段績效
        wm = compute_metrics(eq_test, res.trades[
            res.trades["entry_ts"].dt.tz_convert(TAIPEI).dt.date >= w.test_start
        ] if not res.trades.empty else pd.DataFrame())
        per_window_metrics.append(wm)

        # 下一窗從這窗 test 結束時的權益接續
        if not eq_test.empty:
            current_cash = float(eq_test["equity"].iloc[-1])

    combined_eq = pd.concat(eq_pieces, ignore_index=True) if eq_pieces else pd.DataFrame()
    combined_tr = pd.concat(trade_pieces, ignore_index=True) if trade_pieces else pd.DataFrame()
    combined_metrics = compute_metrics(combined_eq, combined_tr)

    return WalkForwardResult(
        strategy_name=name,
        windows=windows,
        per_window_metrics=per_window_metrics,
        combined_equity=combined_eq,
        combined_trades=combined_tr,
        combined_metrics=combined_metrics,
    )
