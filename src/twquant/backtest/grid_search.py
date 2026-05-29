"""EMA 參數普查（grid search over walk-forward）。

目的：一次性回答「EMA 交叉這條路是死是活」，以及 ATR 移動停利是否改善績效。

對每組 (fast, slow, trend, atr_period, atr_mult) 參數跑完整 walk-forward，
收集樣本外（quasi-OOS）績效，最後排名。判讀原則：
- 若**所有**組合 OOS Sharpe 皆為負 → EMA 交叉沒救，埋葬
- 若**只有零星孤立**幾組為正 → 極可能過擬合，不可信
- 若**一整片相鄰參數**都為正 → 可能有真 edge（仍需謹慎）

⚠️ 警告：grid search 本身有資料窺探（data-snooping）風險。
「挑出最好的那組」≠「那組未來會賺」。本工具用來看**整片參數的穩健性**，
不是用來挑單一最佳參數。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from twquant.backtest.walk_forward import run_walk_forward
from twquant.execution import CostModel
from twquant.strategies.ema_crossover import EmaCrossover


@dataclass
class GridResult:
    fast: int
    slow: int
    trend: int
    atr_period: int        # 0 = ATR 停利停用
    atr_mult: float        # 僅 atr_period > 0 時有意義
    timeframe: str
    oos_total_return_pct: float
    oos_sharpe: float
    oos_calmar: float
    oos_max_drawdown_pct: float
    oos_trades: int
    oos_win_rate_pct: float


def run_grid_search(
    bars: pd.DataFrame,
    timeframe: str,
    *,
    fast_periods: list[int],
    slow_periods: list[int],
    trend_periods: list[int] | None = None,
    atr_periods: list[int] | None = None,
    atr_mults: list[float] | None = None,
    train_months: int = 12,
    test_months: int = 3,
    initial_cash: float = 1_000_000,
    cost_model: CostModel | None = None,
) -> list[GridResult]:
    """對 (fast, slow, trend, atr_period, atr_mult) 笛卡兒積跑 walk-forward，
    回傳結果清單（依 OOS Sharpe 遞減排序）。

    只保留 fast < slow 的組合。trend=0 表示無濾網，atr_period=0 表示無 ATR 停利。
    atr_mults 只在 atr_period > 0 的組合中生效；atr_period=0 時強制 atr_mult=0.0。
    """
    trend_periods = trend_periods or [0]
    atr_periods = atr_periods or [0]
    atr_mults = atr_mults or [2.0]

    results: list[GridResult] = []

    for fast in fast_periods:
        for slow in slow_periods:
            if fast >= slow:
                continue
            for trend in trend_periods:
                for atr_p in atr_periods:
                    # atr_period=0 時不需要掃 atr_mult
                    mults = [0.0] if atr_p == 0 else atr_mults
                    for atr_m in mults:
                        def factory(
                            f=fast, s=slow, t=trend,
                            ap=atr_p, am=atr_m, tf=timeframe,
                        ):
                            return EmaCrossover(
                                fast_period=f, slow_period=s,
                                timeframe=tf, trend_period=t,
                                atr_period=ap, atr_mult=am,
                            )

                        wf = run_walk_forward(
                            bars, factory,
                            train_months=train_months,
                            test_months=test_months,
                            initial_cash=initial_cash,
                            cost_model=cost_model,
                        )
                        m = wf.combined_metrics
                        results.append(GridResult(
                            fast=fast, slow=slow, trend=trend,
                            atr_period=atr_p, atr_mult=atr_m,
                            timeframe=timeframe,
                            oos_total_return_pct=m.total_return_pct,
                            oos_sharpe=m.sharpe,
                            oos_calmar=m.calmar,
                            oos_max_drawdown_pct=m.max_drawdown_pct,
                            oos_trades=m.num_trades,
                            oos_win_rate_pct=m.win_rate_pct,
                        ))

    results.sort(key=lambda r: r.oos_sharpe, reverse=True)
    return results


def results_to_df(results: list[GridResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        row: dict = {
            "fast": r.fast,
            "slow": r.slow,
            "trend": r.trend,
            "atr_period": r.atr_period,
            "atr_mult": r.atr_mult if r.atr_period > 0 else "-",
            "oos_return%": round(r.oos_total_return_pct, 2),
            "oos_sharpe": round(r.oos_sharpe, 2),
            "oos_calmar": round(r.oos_calmar, 2),
            "oos_mdd%": round(r.oos_max_drawdown_pct, 2),
            "trades": r.oos_trades,
            "win%": round(r.oos_win_rate_pct, 1),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def verdict(results: list[GridResult]) -> str:
    """根據整片參數的 OOS Sharpe 分布給白話判決。"""
    if not results:
        return "no results"
    total = len(results)
    positive = [r for r in results if r.oos_sharpe > 0]
    strong = [r for r in results if r.oos_sharpe >= 0.5]
    pct_pos = len(positive) / total * 100

    lines = [
        f"共測試 {total} 組參數",
        f"OOS Sharpe > 0   ：{len(positive)} 組 ({pct_pos:.0f}%)",
        f"OOS Sharpe >= 0.5：{len(strong)} 組",
    ]
    if len(positive) == 0:
        lines.append("判決：全軍覆沒 → EMA 交叉在此時間框沒有 edge，應埋葬此方向。")
    elif pct_pos < 25:
        lines.append("判決：僅零星參數為正 → 極可能過擬合，不可信賴，視同無 edge。")
    elif pct_pos < 60:
        lines.append("判決：約半數為正 → 邊緣，需更謹慎驗證（更長資料、不同市場）。")
    else:
        lines.append("判決：大片參數皆正 → 可能存在真 edge，值得進一步研究（仍須防過擬合）。")
    return "\n".join(lines)
