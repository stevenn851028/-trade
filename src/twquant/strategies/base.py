"""策略基礎介面。

策略只負責：根據 BarEvent 維護內部狀態，決定「應該持有什麼部位」，
產出 SignalEvent。它不關心口數、保證金、訂單路由 — 那是 Risk 與 Executor 的事。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from twquant.events import BarEvent, Direction, SignalEvent


class Strategy(ABC):
    """所有策略的共同介面。"""

    name: str = "base"

    @abstractmethod
    def on_bar(self, bar: BarEvent) -> SignalEvent | None:
        """每根 K 棒收盤後呼叫；回傳一個訊號或 None。"""

    @property
    @abstractmethod
    def current_target(self) -> Direction:
        """策略目前希望持有的方向。"""
