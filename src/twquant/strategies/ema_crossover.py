"""EMA10/60 交叉策略。

對應 `docs/STRATEGY.md`：
- 快線 EMA10 與 慢線 EMA60
- 黃金交叉（快線由下往上穿越慢線）→ 做多
- 死亡交叉（快線由上往下穿越慢線）→ 平倉
- 訊號於 K 棒收盤確認，下單由執行層在下一根 K 棒開盤完成
- 暖機期：兩條 EMA 都需要至少 `slow_period` 根才產出訊號

策略類別與 timeframe 解耦（傳入 '15m' / '30m'），符合 Phase 1 雙候選的設計。
"""

from __future__ import annotations

from dataclasses import dataclass

from twquant.events import BarEvent, Direction, SignalEvent
from twquant.strategies.base import Strategy


class IncrementalEMA:
    """逐根更新的 EMA；用前 N 根的 SMA 作為種子，避免初期偏差。"""

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self._seed_buf: list[float] = []
        self._value: float | None = None

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def value(self) -> float:
        if self._value is None:
            raise RuntimeError("EMA not ready yet")
        return self._value

    def update(self, price: float) -> float | None:
        if self._value is None:
            self._seed_buf.append(price)
            if len(self._seed_buf) >= self.period:
                self._value = sum(self._seed_buf) / len(self._seed_buf)
                self._seed_buf = []
                return self._value
            return None
        self._value = self.alpha * price + (1 - self.alpha) * self._value
        return self._value


@dataclass
class EmaCrossover(Strategy):
    """EMA10 / EMA60 黃金 / 死亡交叉。可選用較長 EMA 作為趨勢濾網。"""

    fast_period: int = 10
    slow_period: int = 60
    timeframe: str = "15m"
    trend_period: int = 0     # 0 = 無濾網；> 0 則啟用 close > EMA(trend) 作為進場條件

    def __post_init__(self):
        suffix = f"_t{self.trend_period}" if self.trend_period > 0 else ""
        self.name = f"ema_cross_{self.timeframe}{suffix}"
        self._fast = IncrementalEMA(self.fast_period)
        self._slow = IncrementalEMA(self.slow_period)
        self._trend: IncrementalEMA | None = (
            IncrementalEMA(self.trend_period) if self.trend_period > 0 else None
        )
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._target: Direction = Direction.FLAT

    @property
    def current_target(self) -> Direction:
        return self._target

    def reset_position(self) -> None:
        """重設 position-tracking 狀態（不影響 EMA 內部狀態）。

        用途：walk-forward 在訓練期暖機完後，將策略「視為剛開始」
        的乾淨狀態進入測試期，避免訓練期的部位狀態洩漏到測試報酬。
        """
        self._target = Direction.FLAT

    def on_bar(self, bar: BarEvent) -> SignalEvent | None:
        # 完整性檢查（避免被錯誤週期的資料汙染）
        if bar.timeframe != self.timeframe:
            raise ValueError(
                f"strategy timeframe={self.timeframe} got bar timeframe={bar.timeframe}"
            )

        self._fast.update(bar.close)
        self._slow.update(bar.close)
        if self._trend is not None:
            self._trend.update(bar.close)

        # 三條 EMA（若啟用 trend）都要 ready 才能產訊號
        if not (self._fast.ready and self._slow.ready):
            return None
        if self._trend is not None and not self._trend.ready:
            return None

        f, s = self._fast.value, self._slow.value
        signal: SignalEvent | None = None

        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and f > s
            crossed_down = self._prev_fast >= self._prev_slow and f < s

            # 進場：黃金交叉 + 趨勢濾網（若啟用）
            if crossed_up and self._target == Direction.FLAT:
                trend_ok = (self._trend is None) or (bar.close > self._trend.value)
                if trend_ok:
                    self._target = Direction.LONG
                    signal = SignalEvent(bar.ts, Direction.LONG, "golden_cross")
                # 否則：訊號被濾掉，但 prev_fast / prev_slow 照常更新

            # 出場：死亡交叉一律允許
            elif crossed_down and self._target == Direction.LONG:
                self._target = Direction.FLAT
                signal = SignalEvent(bar.ts, Direction.FLAT, "death_cross")

        self._prev_fast, self._prev_slow = f, s
        return signal
