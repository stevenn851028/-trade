"""事件驅動回測 runner。

事件流（每根 K 棒）：
    1. Strategy.on_bar(prev_bar) → 上一根可能產生的 SignalEvent
    2. RiskManager.on_signal(signal, position) → OrderEvent
    3. Executor.submit(order, current_bar) → FillEvent，於本根開盤成交
    4. Portfolio.on_fill(fill) 更新部位
    5. Portfolio.on_bar(current_bar) mark-to-market 寫入權益曲線
    6. Strategy.on_bar(current_bar) 產生本根訊號（供下根 K 棒執行）

關鍵：訊號於 K 棒收盤確認，**下單於下一根開盤**，避免 look-ahead bias。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd

from twquant.backtest.metrics import PerformanceMetrics, compute_metrics
from twquant.events import BarEvent, FillEvent, OrderEvent, SignalEvent
from twquant.execution import BacktestExecutor, CostModel
from twquant.portfolio import Portfolio
from twquant.risk import RiskManager
from twquant.strategies.base import Strategy


@dataclass
class BacktestResult:
    strategy_name: str
    bars: pd.DataFrame             # 輸入 bars 含 ts、open/high/low/close、volume
    signals: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: PerformanceMetrics


def _bars_to_events(bars_df: pd.DataFrame) -> list[BarEvent]:
    events: list[BarEvent] = []
    for r in bars_df.itertuples(index=False):
        events.append(BarEvent(
            ts=r.ts,
            timeframe=str(r.timeframe),
            product=str(r.product),
            contract_month=str(r.contract_month),
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=int(r.volume),
        ))
    return events


def run_backtest(
    bars_df: pd.DataFrame,
    strategy: Strategy,
    *,
    initial_cash: float = 1_000_000,
    cost_model: CostModel | None = None,
    risk_manager: RiskManager | None = None,
) -> BacktestResult:
    """跑單一回測。

    Args:
        bars_df: BarStore.query_bars() 或 aggregate_ticks_to_bars() 的輸出。
                 預期欄位含 ts, timeframe, product, contract_month, OHLCV。
        strategy: Strategy 實例（已配置 timeframe）。
        initial_cash: 起始資金（NT$）。
        cost_model: 成本模型，預設大台。
        risk_manager: 風控，預設固定 1 口。

    Returns:
        BacktestResult，含 signals / orders / fills / trades / equity / metrics。
    """
    cost_model = cost_model or CostModel.for_tx()
    risk_manager = risk_manager or RiskManager()
    executor = BacktestExecutor(cost_model)
    portfolio = Portfolio(initial_cash=initial_cash,
                          contract_multiplier=cost_model.contract_multiplier)

    bars = _bars_to_events(bars_df)
    if not bars:
        return BacktestResult(
            strategy_name=strategy.name,
            bars=bars_df,
            signals=pd.DataFrame(),
            orders=pd.DataFrame(),
            fills=pd.DataFrame(),
            trades=portfolio.trades_df(),
            equity_curve=portfolio.equity_df(),
            metrics=compute_metrics(portfolio.equity_df(), portfolio.trades_df()),
        )

    signals_log: list[SignalEvent] = []
    orders_log: list[OrderEvent] = []
    fills_log: list[FillEvent] = []

    pending_order: OrderEvent | None = None

    for bar in bars:
        # 1. 先處理「前一根收盤」產生、要在「本根開盤」執行的訂單
        if pending_order is not None:
            fill = executor.submit(pending_order, bar)
            portfolio.on_fill(fill)
            fills_log.append(fill)
            pending_order = None

        # 2. mark-to-market with current bar close
        portfolio.on_bar(bar)

        # 3. 策略消化本根 K 棒，可能產生訊號（給下一根執行）
        signal = strategy.on_bar(bar)
        if signal is not None:
            signals_log.append(signal)
            order = risk_manager.on_signal(signal, portfolio.position_lots)
            if order is not None:
                orders_log.append(order)
                pending_order = order

    # 收盤後若仍有 pending_order（最後一根產生的訊號），本回測不再有下一根可成交，丟棄
    # 實盤這情境會在下一根盤後 / 下一個交易日成交，但回測樣本到此為止
    if pending_order is not None:
        # 補一筆「last bar close」式的近似成交，避免遺漏最後出場
        last_bar = bars[-1]
        synthetic_bar = BarEvent(
            ts=last_bar.ts, timeframe=last_bar.timeframe, product=last_bar.product,
            contract_month=last_bar.contract_month,
            open=last_bar.close, high=last_bar.close, low=last_bar.close,
            close=last_bar.close, volume=0,
        )
        fill = executor.submit(pending_order, synthetic_bar)
        portfolio.on_fill(fill)
        fills_log.append(fill)

    sig_df = pd.DataFrame([asdict(s) for s in signals_log]) if signals_log else pd.DataFrame()
    ord_df = pd.DataFrame([asdict(o) for o in orders_log]) if orders_log else pd.DataFrame()
    fill_df = pd.DataFrame([asdict(f) for f in fills_log]) if fills_log else pd.DataFrame()

    # 估算 bars_per_year for metrics
    bars_per_year = _bars_per_year_for_timeframe(bars[0].timeframe)

    return BacktestResult(
        strategy_name=strategy.name,
        bars=bars_df,
        signals=sig_df,
        orders=ord_df,
        fills=fill_df,
        trades=portfolio.trades_df(),
        equity_curve=portfolio.equity_df(),
        metrics=compute_metrics(portfolio.equity_df(), portfolio.trades_df(),
                                bars_per_year=bars_per_year),
    )


def _bars_per_year_for_timeframe(tf: str) -> float:
    """估算每年 bar 數，給 Sharpe 年化用。"""
    days_per_year = 252.0
    per_day = {"1m": 76 * 15, "5m": 76 * 3, "15m": 76, "30m": 38, "1h": 19, "1d": 1}
    return per_day.get(tf, 38) * days_per_year
