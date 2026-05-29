"""EMA10/60 交叉策略。

對應 `docs/STRATEGY.md`：
- 快線 EMA10 與 慢線 EMA60
- 黃金交叉（快線由下往上穿越慢線）→ 做多
- 死亡交叉（快線由上往下穿越慢線）→ 平倉
- 訊號於 K 棒收盤確認，下單由執行層在下一根 K 棒開盤完成
- 暖機期：兩條 EMA 都需要至少 `slow_period` 根才產出訊號

策略類別與 timeframe 解耦（傳入 '15m' / '30m'），符合 Phase 1 雙候選的設計。

ATR 移動停利（可選）：
- 啟用條件：atr_period > 0
- 停利水準 = peak_high − atr_mult × ATR（前一根 K 棒結束時的 ATR 值）
- 使用前根 ATR 而非當根，避免急跌時 TR 暴增反推停利點遠離
- 優先於死亡交叉判斷，確保停利出場 reason="atr_trail_stop"
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


class IncrementalATR:
    """EMA-based ATR；TR 值由呼叫端傳入（需由策略持有 prev_close）。

    種子採前 N 根 TR 的 SMA，與 IncrementalEMA 一致。
    alpha = 2 / (period + 1)，使用 EMA 而非 Wilder's smoothing。
    """

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
            raise RuntimeError("ATR not ready yet")
        return self._value

    def update(self, tr: float) -> float | None:
        if self._value is None:
            self._seed_buf.append(tr)
            if len(self._seed_buf) >= self.period:
                self._value = sum(self._seed_buf) / len(self._seed_buf)
                self._seed_buf = []
                return self._value
            return None
        self._value = self.alpha * tr + (1 - self.alpha) * self._value
        return self._value


@dataclass
class EmaCrossover(Strategy):
    """EMA10 / EMA60 黃金 / 死亡交叉。可選用較長 EMA 作為趨勢濾網，
    以及 ATR 移動停利。"""

    fast_period: int = 10
    slow_period: int = 60
    timeframe: str = "15m"
    trend_period: int = 0     # 0 = 無濾網；> 0 則啟用 close > EMA(trend) 作為進場條件
    atr_period: int = 0       # 0 = 無移動停利；> 0 啟用 ATR 移動停利
    atr_mult: float = 2.0     # 移動停利水準 = peak_high − atr_mult × ATR

    def __post_init__(self):
        suffix = f"_t{self.trend_period}" if self.trend_period > 0 else ""
        self.name = f"ema_cross_{self.timeframe}{suffix}"
        self._fast = IncrementalEMA(self.fast_period)
        self._slow = IncrementalEMA(self.slow_period)
        self._trend: IncrementalEMA | None = (
            IncrementalEMA(self.trend_period) if self.trend_period > 0 else None
        )
        self._atr: IncrementalATR | None = (
            IncrementalATR(self.atr_period) if self.atr_period > 0 else None
        )
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._target: Direction = Direction.FLAT
        self._peak_high: float | None = None   # 持倉期間的最高價
        self._atr_prev: float | None = None    # 前一根結束時的 ATR（用於停利計算）
        self._prev_close: float | None = None  # 計算 TR 需要前一根收盤

    @property
    def current_target(self) -> Direction:
        return self._target

    def reset_position(self) -> None:
        """重設 position-tracking 狀態（不影響 EMA / ATR 內部狀態）。

        用途：walk-forward 在訓練期暖機完後，將策略「視為剛開始」
        的乾淨狀態進入測試期，避免訓練期的部位狀態洩漏到測試報酬。
        """
        self._target = Direction.FLAT
        self._peak_high = None

    # ──────────────────────────────────────────────────────────────────────
    def _update_atr(self, tr: float | None) -> None:
        """更新 ATR 並快取前一根值（供下一根 on_bar 使用）。"""
        if self._atr is None or tr is None:
            return
        self._atr.update(tr)
        self._atr_prev = self._atr.value if self._atr.ready else None

    # ──────────────────────────────────────────────────────────────────────
    def on_bar(self, bar: BarEvent) -> SignalEvent | None:
        # 完整性檢查（避免被錯誤週期的資料汙染）
        if bar.timeframe != self.timeframe:
            raise ValueError(
                f"strategy timeframe={self.timeframe} got bar timeframe={bar.timeframe}"
            )

        # ── True Range（需 prev_close，在更新任何狀態前計算）────────────
        tr: float | None = None
        if self._atr is not None and self._prev_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )

        # ── EMA 更新 ─────────────────────────────────────────────────────
        self._fast.update(bar.close)
        self._slow.update(bar.close)
        if self._trend is not None:
            self._trend.update(bar.close)

        # 三條 EMA（若啟用 trend）都要 ready 才能產訊號
        if not (self._fast.ready and self._slow.ready):
            self._update_atr(tr)
            self._prev_close = bar.close
            return None
        if self._trend is not None and not self._trend.ready:
            self._update_atr(tr)
            self._prev_close = bar.close
            return None

        f, s = self._fast.value, self._slow.value
        signal: SignalEvent | None = None

        # ── 1. ATR 移動停利（優先於交叉訊號）────────────────────────────
        # 使用 atr_prev（前一根的 ATR），而非當根，
        # 避免急跌造成 TR 暴增、停利點反向拉遠的問題。
        if self._target == Direction.LONG and self._atr is not None:
            if self._peak_high is None or bar.high > self._peak_high:
                self._peak_high = bar.high
            if self._atr_prev is not None:
                trail_stop = self._peak_high - self.atr_mult * self._atr_prev
                if bar.close < trail_stop:
                    self._target = Direction.FLAT
                    self._peak_high = None
                    self._prev_fast, self._prev_slow = f, s
                    self._update_atr(tr)
                    self._prev_close = bar.close
                    return SignalEvent(bar.ts, Direction.FLAT, "atr_trail_stop")

        # ── 2. EMA 交叉訊號 ───────────────────────────────────────────────
        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and f > s
            crossed_down = self._prev_fast >= self._prev_slow and f < s

            # 進場：黃金交叉 + 趨勢濾網（若啟用）
            if crossed_up and self._target == Direction.FLAT:
                trend_ok = (self._trend is None) or (bar.close > self._trend.value)
                if trend_ok:
                    self._target = Direction.LONG
                    self._peak_high = bar.high  # 進場時初始化高水位
                    signal = SignalEvent(bar.ts, Direction.LONG, "golden_cross")
                # 否則：訊號被濾掉，但 prev_fast / prev_slow 照常更新

            # 出場：死亡交叉一律允許
            elif crossed_down and self._target == Direction.LONG:
                self._target = Direction.FLAT
                self._peak_high = None
                signal = SignalEvent(bar.ts, Direction.FLAT, "death_cross")

        self._prev_fast, self._prev_slow = f, s
        self._update_atr(tr)
        self._prev_close = bar.close
        return signal
