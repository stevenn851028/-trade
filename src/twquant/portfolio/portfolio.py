"""部位與權益狀態追蹤。

職責：
- 接收 FillEvent，更新口數、平均成本、已實現／未實現損益
- 每根 BarEvent 用收盤價更新權益（mark-to-market）
- 紀錄完整 trades（進場 → 出場 配對）
- 紀錄權益曲線（每根 K 棒）

Phase 1 只支援單向 LONG（口數 ≥ 0），SHORT 留給後續擴充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from twquant.events import BarEvent, FillEvent, Side


@dataclass
class TradeRecord:
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    quantity: int
    entry_reason: str
    exit_reason: str
    gross_pnl: float       # (exit - entry) × multiplier × qty
    cost: float            # commission + tax for both legs
    net_pnl: float         # gross - cost

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price / self.entry_price - 1) * 100.0


class Portfolio:
    """單一商品、單向 LONG 部位的 Portfolio。"""

    def __init__(self, initial_cash: float, contract_multiplier: float):
        self.initial_cash = initial_cash
        self.contract_multiplier = contract_multiplier

        self.cash = initial_cash
        self.position_lots = 0
        self.avg_entry_price = 0.0
        self.entry_ts: datetime | None = None
        self.entry_reason: str = ""
        self.open_cost: float = 0.0  # 累積進場 leg 的手續費 + 稅

        self.trades: list[TradeRecord] = []
        self.equity_curve: list[tuple[datetime, float]] = []  # (ts, equity)

    @property
    def equity(self) -> float:
        """純現金（含已實現），未含 mark-to-market；mark-to-market 由 on_bar 更新到 equity_curve。"""
        return self.cash

    def _mark_to_market(self, mark_price: float) -> float:
        unrealized = (mark_price - self.avg_entry_price) * \
            self.contract_multiplier * self.position_lots
        return self.cash + unrealized

    def on_bar(self, bar: BarEvent) -> None:
        equity = self._mark_to_market(bar.close)
        self.equity_curve.append((bar.ts, equity))

    def on_fill(self, fill: FillEvent) -> None:
        fee = fill.commission + fill.tax

        if fill.side == Side.BUY:
            if self.position_lots > 0:
                # 加碼（Phase 1 通常用不到；保留一般化處理）
                total_lots = self.position_lots + fill.quantity
                self.avg_entry_price = (
                    self.avg_entry_price * self.position_lots
                    + fill.fill_price * fill.quantity
                ) / total_lots
                self.position_lots = total_lots
            else:
                self.position_lots = fill.quantity
                self.avg_entry_price = fill.fill_price
                self.entry_ts = fill.ts
                self.entry_reason = fill.reason
                self.open_cost = 0.0
            self.cash -= fee
            self.open_cost += fee

        elif fill.side == Side.SELL:
            if self.position_lots <= 0:
                raise RuntimeError("attempt to SELL with no LONG position (Phase 1 forbids SHORT)")

            qty = fill.quantity
            if qty > self.position_lots:
                raise RuntimeError(f"oversell: have {self.position_lots} sell {qty}")

            gross = (fill.fill_price - self.avg_entry_price) * self.contract_multiplier * qty
            # 出場 leg 手續費 + 稅；進場 leg 的成本之前已記在 open_cost
            entry_portion_cost = self.open_cost * (qty / self.position_lots)
            total_cost = fee + entry_portion_cost
            net = gross - total_cost
            self.cash += gross - fee

            trade = TradeRecord(
                entry_ts=self.entry_ts or fill.ts,
                exit_ts=fill.ts,
                entry_price=self.avg_entry_price,
                exit_price=fill.fill_price,
                quantity=qty,
                entry_reason=self.entry_reason,
                exit_reason=fill.reason,
                gross_pnl=gross,
                cost=total_cost,
                net_pnl=net,
            )
            self.trades.append(trade)

            self.position_lots -= qty
            self.open_cost -= entry_portion_cost
            if self.position_lots == 0:
                self.avg_entry_price = 0.0
                self.entry_ts = None
                self.entry_reason = ""
                self.open_cost = 0.0

    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[
                "entry_ts", "exit_ts", "entry_price", "exit_price", "quantity",
                "entry_reason", "exit_reason", "gross_pnl", "cost", "net_pnl",
                "return_pct",
            ])
        rows = [
            {**t.__dict__, "return_pct": t.return_pct}
            for t in self.trades
        ]
        return pd.DataFrame(rows)

    def equity_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.equity_curve, columns=["ts", "equity"])
