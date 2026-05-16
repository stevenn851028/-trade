"""回測模擬撮合。

行為：
- 訂單於「下一根 K 棒開盤」成交
- 滑價：BUY 加 N tick，SELL 減 N tick
- 手續費：固定 NT$/口 單邊
- 期交稅：成交點數 × contract_multiplier × tax_rate × 口數

預設成本參數採市場一般水準，可依券商與行情調整：
- 大台 (TX)：commission=50, multiplier=200, slippage=1 tick (=1 點)
- 小台 (MTX)：commission=30, multiplier=50, slippage=1 tick
- 期交稅率：0.00002

`BacktestExecutor` 與未來 `LiveExecutor` 將實作同一介面 `submit()`。
"""

from __future__ import annotations

from dataclasses import dataclass

from twquant.events import BarEvent, FillEvent, OrderEvent, Side


@dataclass
class CostModel:
    commission_per_lot: float = 50.0     # NT$ 單邊
    tax_rate: float = 0.00002            # 期交稅率（成交金額比例）
    contract_multiplier: float = 200.0   # 大台 1 點 = NT$200
    slippage_ticks: float = 1.0          # 1 tick = 1 點
    tick_size: float = 1.0               # 大台最小跳動 1 點

    @classmethod
    def for_tx(cls) -> "CostModel":
        return cls(commission_per_lot=50, tax_rate=0.00002,
                   contract_multiplier=200, slippage_ticks=1, tick_size=1)

    @classmethod
    def for_mtx(cls) -> "CostModel":
        return cls(commission_per_lot=30, tax_rate=0.00002,
                   contract_multiplier=50, slippage_ticks=1, tick_size=1)

    def apply_slippage(self, price: float, side: Side) -> float:
        delta = self.slippage_ticks * self.tick_size
        return price + delta if side == Side.BUY else price - delta

    def compute_fees(self, fill_price: float, quantity: int) -> tuple[float, float]:
        commission = self.commission_per_lot * quantity
        notional = fill_price * self.contract_multiplier * quantity
        tax = notional * self.tax_rate
        return commission, tax


class BacktestExecutor:
    def __init__(self, cost_model: CostModel | None = None):
        self.cost_model = cost_model or CostModel.for_tx()

    def submit(self, order: OrderEvent, next_bar: BarEvent) -> FillEvent:
        """以 next_bar.open 為基準價、加滑價、計算手續費與稅。"""
        fill_price = self.cost_model.apply_slippage(next_bar.open, order.side)
        commission, tax = self.cost_model.compute_fees(fill_price, order.quantity)
        return FillEvent(
            ts=next_bar.ts,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            commission=commission,
            tax=tax,
            reason=order.reason,
        )
