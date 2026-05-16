"""績效指標計算。

參考 `docs/BACKTEST.md § 績效指標`。所有指標以扣除成本後的權益曲線為輸入。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass
class PerformanceMetrics:
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float
    volatility_annualized_pct: float
    num_trades: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_trade_pnl: float


def _annualization_factor(bars_per_year: float) -> float:
    return math.sqrt(bars_per_year)


def compute_metrics(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    bars_per_year: float = 252.0 * 38.0,  # ~38 30m bars per trading day
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """從 equity_curve + trades 計算所有指標。

    `bars_per_year` 視 timeframe 而定：
        - 15m: 252 × 76 ≈ 19,152
        - 30m: 252 × 38 ≈ 9,576
        - 1d:  252
    """
    if equity_df.empty:
        return PerformanceMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    eq = equity_df["equity"].astype(float).values
    initial = float(eq[0])
    final = float(eq[-1])

    total_return = (final / initial - 1) if initial > 0 else 0.0

    # 年化（依時間區間）
    if len(eq) > 1:
        n_bars = len(eq)
        years = n_bars / bars_per_year
        cagr = (final / initial) ** (1 / years) - 1 if years > 0 and initial > 0 else 0.0

        returns = pd.Series(eq).pct_change().dropna()
        ann_factor = _annualization_factor(bars_per_year)
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        ann_vol = std * ann_factor

        mean_r = float(returns.mean())
        sharpe = ((mean_r - risk_free_rate / bars_per_year) / std) * ann_factor \
            if std > 0 else 0.0

        downside = returns[returns < 0]
        d_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
        sortino = ((mean_r - risk_free_rate / bars_per_year) / d_std) * ann_factor \
            if d_std > 0 else 0.0
    else:
        cagr = 0.0
        ann_vol = 0.0
        sharpe = 0.0
        sortino = 0.0

    # 最大回撤
    running_max = pd.Series(eq).cummax()
    drawdown = (pd.Series(eq) - running_max) / running_max
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    # 交易統計
    if trades_df.empty:
        num_trades = 0
        win_rate = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0
        avg_trade_pnl = 0.0
    else:
        wins = trades_df[trades_df["net_pnl"] > 0]["net_pnl"]
        losses = trades_df[trades_df["net_pnl"] <= 0]["net_pnl"]
        num_trades = len(trades_df)
        win_rate = len(wins) / num_trades * 100 if num_trades else 0.0
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss = float(losses.mean()) if len(losses) else 0.0
        total_wins = float(wins.sum())
        total_losses = float(losses.sum())
        profit_factor = (total_wins / abs(total_losses)) if total_losses < 0 else float("inf")
        avg_trade_pnl = float(trades_df["net_pnl"].mean())

    return PerformanceMetrics(
        total_return_pct=total_return * 100,
        cagr_pct=cagr * 100,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown_pct=max_dd * 100,
        volatility_annualized_pct=ann_vol * 100,
        num_trades=num_trades,
        win_rate_pct=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
    )
