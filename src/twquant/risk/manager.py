"""風控管理器。

職責：將 SignalEvent 轉為具體的 OrderEvent（決定口數與下單方向），
並執行保證金檢查、Kill Switch 等保護機制。

對應 `docs/RISK_MANAGEMENT.md`：
- 固定口數 sizing（Phase 1）
- 保證金使用率上限（拒絕進場）
- Kill Switch：累計回撤超過閾值 → 強制平倉、禁止再進場
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from twquant.events import Direction, OrderEvent, Side, SignalEvent

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    fixed_lots: int = 1
    margin_per_lot: float = 184_000.0      # 大台原始保證金（NT$）
    max_margin_utilization: float = 0.50   # 保證金使用率上限
    max_drawdown_pct: float = 20.0         # 觸發 Kill Switch 的累積回撤（%）


@dataclass
class PortfolioSnapshot:
    """傳給 RiskManager 評估用的當下狀態（避免雙向依賴 Portfolio 類別）。"""
    cash: float
    equity: float
    peak_equity: float
    position_lots: int


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.kill_switch_tripped = False

    def _check_kill_switch(self, snapshot: PortfolioSnapshot) -> bool:
        if snapshot.peak_equity <= 0:
            return False
        dd_pct = (snapshot.peak_equity - snapshot.equity) / snapshot.peak_equity * 100
        if dd_pct >= self.config.max_drawdown_pct and not self.kill_switch_tripped:
            log.warning("KILL SWITCH tripped: drawdown=%.2f%% threshold=%.2f%%",
                        dd_pct, self.config.max_drawdown_pct)
            self.kill_switch_tripped = True
        return self.kill_switch_tripped

    def _margin_ok(self, snapshot: PortfolioSnapshot, lots_to_add: int) -> bool:
        new_lots = snapshot.position_lots + lots_to_add
        required = self.config.margin_per_lot * new_lots
        if snapshot.cash <= 0:
            return False
        utilization = required / snapshot.cash
        return utilization <= self.config.max_margin_utilization

    def on_signal(
        self,
        signal: SignalEvent,
        snapshot: PortfolioSnapshot,
    ) -> OrderEvent | None:
        """以目前部位與訊號目標決定下單。

        Kill switch 觸發後：禁止新進場，但允許平倉。
        """
        killed = self._check_kill_switch(snapshot)

        if signal.target == Direction.LONG and snapshot.position_lots == 0:
            if killed:
                log.info("entry blocked by kill switch")
                return None
            if not self._margin_ok(snapshot, self.config.fixed_lots):
                log.info("entry blocked by margin cap")
                return None
            return OrderEvent(
                ts=signal.ts,
                side=Side.BUY,
                quantity=self.config.fixed_lots,
                reason=signal.reason,
            )

        if signal.target == Direction.FLAT and snapshot.position_lots > 0:
            return OrderEvent(
                ts=signal.ts,
                side=Side.SELL,
                quantity=snapshot.position_lots,
                reason=signal.reason,
            )
        return None
