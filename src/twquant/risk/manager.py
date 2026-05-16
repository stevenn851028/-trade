"""風控管理器（Phase 1 起點）。

職責：將 SignalEvent 轉為具體的 OrderEvent（決定口數與下單方向）。

Phase 1 設計刻意極簡：固定 1 口、無動態 sizing、無硬停損、
無 Kill Switch；用最小可運作版本先把 pipeline 跑通。
所有風控規則待 Phase 1 通過後逐步加入（見 `docs/RISK_MANAGEMENT.md`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from twquant.events import Direction, OrderEvent, Side, SignalEvent


@dataclass
class RiskConfig:
    fixed_lots: int = 1


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def on_signal(
        self,
        signal: SignalEvent,
        current_position_lots: int,
    ) -> OrderEvent | None:
        """以目前部位與訊號目標決定下單。"""
        if signal.target == Direction.LONG and current_position_lots == 0:
            return OrderEvent(
                ts=signal.ts,
                side=Side.BUY,
                quantity=self.config.fixed_lots,
                reason=signal.reason,
            )
        if signal.target == Direction.FLAT and current_position_lots > 0:
            return OrderEvent(
                ts=signal.ts,
                side=Side.SELL,
                quantity=current_position_lots,
                reason=signal.reason,
            )
        return None
