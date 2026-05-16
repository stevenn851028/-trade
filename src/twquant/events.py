"""事件型別。

整個系統以這些事件流串接：
    BarEvent → Strategy → SignalEvent → RiskManager → OrderEvent → Executor → FillEvent

回測與實盤共用相同事件型別，只是事件來源不同（DB vs 即時行情）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class BarEvent:
    ts: datetime          # K 棒結束時間（timezone-aware）
    timeframe: str        # '15m' / '30m'
    product: str          # 'TX' / 'MTX'
    contract_month: str   # 'CONT' / '202606' ...
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class SignalEvent:
    ts: datetime
    target: Direction     # 目標持有方向（LONG / FLAT；Phase 1 不做 SHORT）
    reason: str           # 'golden_cross' / 'death_cross' / 'risk_kill_switch' ...


@dataclass(frozen=True, slots=True)
class OrderEvent:
    ts: datetime
    side: Side
    quantity: int         # 口數，恆 > 0
    reason: str           # 對應的 SignalEvent.reason 或風控原因


@dataclass(frozen=True, slots=True)
class FillEvent:
    ts: datetime
    side: Side
    quantity: int
    fill_price: float     # 已計入滑價
    commission: float     # 手續費（NT$）
    tax: float            # 期交稅（NT$）
    reason: str

    @property
    def total_cost(self) -> float:
        return self.commission + self.tax
