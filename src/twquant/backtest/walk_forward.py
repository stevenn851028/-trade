"""Walk-forward 分析。

正確實作（修正 v2）：
1. 每窗：以乾淨 Strategy 跑「訓練期」只更新 EMA 狀態、丟棄所有訊號
2. 訓練期結束後呼叫 strategy.reset_position() 清掉部位狀態
3. 在「測試期」跑正常 backtest（fresh portfolio + risk manager）
4. 每窗統計獨立計算
5. Combined 權益曲線由各窗之相對報酬鏈接而成（compound chaining）

如此避免訓練期建立之浮動部位之 mark-to-market 被誤算進測試報酬，
也避免訊號穿越窗口邊界。
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
    combined_equity: pd.DataFrame      # ts, equity（鏈接後）
    combined_trades: pd.DataFrame
    combined_metrics: PerformanceMetrics


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    from calendar import monthrange
    last = monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def make_windows(
    start: date,
    end: date,
    train_months: int,
    test_months: int,
) -> list[WalkForwardWindow]:
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


def _warmup_strategy(strategy: Strategy, train_bars: pd.DataFrame) -> None:
    """以訓練期 bars 暖機策略（更新 EMA），丟棄所有訊號。"""
    if train_bars.empty:
        return
    for r in train_bars.itertuples(index=False):
        bar = BarEvent(
            ts=r.ts, timeframe=str(r.timeframe), product=str(r.product),
            contract_month=str(r.contract_month),
            open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            volume=int(r.volume),
        )
        strategy.on_bar(bar)
    # 清掉訓練期累積的目標部位狀態，讓測試期視為乾淨
    if hasattr(strategy, "reset_position"):
        strategy.reset_position()


def _chain_equity_curves(pieces: list[pd.DataFrame], initial_cash: float) -> pd.DataFrame:
    """將多個獨立窗的權益曲線以複利方式鏈接。

    每段以該窗起點為 initial_cash，按比例重新標度後串接，
    使視覺上連續、計算總報酬時為複利累積。
    """
    chained = []
    current_capital = initial_cash
    for piece in pieces:
        if piece.empty:
            continue
        first_eq = float(piece["equity"].iloc[0])
        if first_eq <= 0:
            continue
        rebased = piece.copy()
        rebased["equity"] = piece["equity"] / first_eq * current_capital
        chained.append(rebased)
        current_capital = float(rebased["equity"].iloc[-1])
    if not chained:
        return pd.DataFrame(columns=["ts", "equity"])
    return pd.concat(chained, ignore_index=True)


def run_walk_forward(
    bars: pd.DataFrame,
    strategy_factory,
    *,
    train_months: int = 24,
    test_months: int = 6,
    initial_cash: float = 1_000_000,
    cost_model: CostModel | None = None,
    risk_factory=None,
) -> WalkForwardResult:
    """跑 walk-forward；每窗獨立、不洩漏部位狀態。"""
    name = strategy_factory().name

    if bars.empty:
        empty = pd.DataFrame()
        return WalkForwardResult(
            strategy_name=name,
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
        # 不夠資料：退化為單窗
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

    for w in windows:
        train_slice = _slice_bars(bars, w.train_start, w.test_start)
        test_slice = _slice_bars(bars, w.test_start, w.test_end)
        if test_slice.empty:
            continue

        strat = strategy_factory()
        rm = risk_factory() if risk_factory else None

        # Phase 1: 訓練期暖機（不開單、不計權益）
        _warmup_strategy(strat, train_slice)

        # Phase 2: 測試期跑正規 backtest（fresh portfolio + risk manager）
        res = run_backtest(test_slice, strat,
                           initial_cash=initial_cash,
                           cost_model=cost_model,
                           risk_manager=rm)

        per_window_metrics.append(res.metrics)
        eq_pieces.append(res.equity_curve)
        if not res.trades.empty:
            trade_pieces.append(res.trades)

    combined_eq = _chain_equity_curves(eq_pieces, initial_cash)
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
