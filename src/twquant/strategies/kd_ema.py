"""KD + EMA 組合策略（兩種變體）。

KdEmaStrategy（方式 1）：EMA 單趨勢線定方向 + KD 低檔黃金交叉抓時機。

KdEmaCrossover（方式 2，5m 新策略）：
- 進場：EMA fast/slow 黃金交叉 **且** KD 黃金交叉同步發生於同一根 K 棒
- 出場：EMA 死亡交叉（優先） 或 ATR 移動停利
- 設計理念：EMA 交叉確保趨勢起動，KD 同步交叉確保此時動能從低檔翻揚，
  避免純 EMA 交叉「在 KD 高位追高」導致的假突破高頻虧損。

KD 算法（台灣標準 9,3,3）：
    RSV = (close - LL_9) / (HH_9 - LL_9) × 100
    K   = (1 - 1/3) × K_prev + (1/3) × RSV   初始值 50
    D   = (1 - 1/3) × D_prev + (1/3) × K     初始值 50
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from twquant.events import BarEvent, Direction, SignalEvent
from twquant.strategies.base import Strategy
from twquant.strategies.ema_crossover import IncrementalATR, IncrementalEMA


class IncrementalKD:
    """逐根更新的 KD 隨機指標。"""

    def __init__(self, rsv_period: int = 9, k_smooth: int = 3, d_smooth: int = 3):
        if rsv_period < 1 or k_smooth < 1 or d_smooth < 1:
            raise ValueError("periods must be >= 1")
        self.rsv_period = rsv_period
        self.k_alpha = 1.0 / k_smooth
        self.d_alpha = 1.0 / d_smooth
        self._highs: deque[float] = deque(maxlen=rsv_period)
        self._lows: deque[float] = deque(maxlen=rsv_period)
        self._k: float = 50.0
        self._d: float = 50.0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def k(self) -> float:
        return self._k

    @property
    def d(self) -> float:
        return self._d

    def update(self, high: float, low: float, close: float) -> tuple[float, float] | None:
        self._highs.append(high)
        self._lows.append(low)
        if len(self._highs) < self.rsv_period:
            return None
        hh = max(self._highs)
        ll = min(self._lows)
        rng = hh - ll
        rsv = 50.0 if rng == 0 else (close - ll) / rng * 100.0
        self._k = (1 - self.k_alpha) * self._k + self.k_alpha * rsv
        self._d = (1 - self.d_alpha) * self._d + self.d_alpha * self._k
        self._ready = True
        return self._k, self._d


@dataclass
class KdEmaStrategy(Strategy):
    """EMA 定方向 + KD 抓時機（方式 1）。

    「EMA 定方向」用**單一條趨勢線** `close > EMA(ema_period)` 判斷多頭，
    而非 fast/slow 交叉——後者本身會在回檔時翻空、與 KD 低檔交叉錯開，
    正是純 EMA 交叉被洗的毛病。穩定趨勢線在正常回檔時不翻空，
    才能與 KD 低檔黃金交叉對齊。
    """

    ema_period: int = 60         # 趨勢線（穩定、不因小回檔翻空）
    rsv_period: int = 9
    k_smooth: int = 3
    d_smooth: int = 3
    kd_entry_max: float = 50.0   # 進場時 K 必須低於此值（避免追高）
    timeframe: str = "15m"

    def __post_init__(self):
        self.name = f"kd_ema_{self.timeframe}"
        self._ema = IncrementalEMA(self.ema_period)
        self._kd = IncrementalKD(self.rsv_period, self.k_smooth, self.d_smooth)
        self._prev_k: float | None = None
        self._prev_d: float | None = None
        self._target: Direction = Direction.FLAT

    @property
    def current_target(self) -> Direction:
        return self._target

    def reset_position(self) -> None:
        self._target = Direction.FLAT

    def on_bar(self, bar: BarEvent) -> SignalEvent | None:
        if bar.timeframe != self.timeframe:
            raise ValueError(
                f"strategy timeframe={self.timeframe} got bar timeframe={bar.timeframe}"
            )

        self._ema.update(bar.close)
        self._kd.update(bar.high, bar.low, bar.close)

        if not (self._ema.ready and self._kd.ready):
            return None

        k, d = self._kd.k, self._kd.d
        trend_up = bar.close > self._ema.value
        signal: SignalEvent | None = None

        if self._prev_k is not None and self._prev_d is not None:
            kd_golden = self._prev_k <= self._prev_d and k > d
            kd_death = self._prev_k >= self._prev_d and k < d

            if self._target == Direction.FLAT:
                # 進場：close 在趨勢線之上 + KD 低檔黃金交叉
                if trend_up and kd_golden and k < self.kd_entry_max:
                    self._target = Direction.LONG
                    signal = SignalEvent(bar.ts, Direction.LONG, "kd_golden_in_uptrend")
            elif self._target == Direction.LONG:
                # 出場：KD 死亡交叉 或 跌破趨勢線
                if kd_death or not trend_up:
                    self._target = Direction.FLAT
                    reason = "kd_death" if kd_death else "ema_trend_break"
                    signal = SignalEvent(bar.ts, Direction.FLAT, reason)

        self._prev_k, self._prev_d = k, d
        return signal


# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class KdEmaCrossover(Strategy):
    """EMA10/60 黃金交叉 + KD 黃金交叉同步進場（5m 短週期策略）。

    進場條件（同一根 K 棒同時滿足）：
        EMA(fast) 由下往上穿越 EMA(slow)  → 趨勢起動
        KD K 線由下往上穿越 D 線          → 動能從低檔翻揚同步確認

    出場條件（優先序）：
        1. ATR 移動停利 trail_stop = peak_high − atr_mult × ATR(prev bar)
        2. EMA 死亡交叉（fast 跌破 slow）

    name 格式：kd_cross_{tf}_{fast}_{slow}_kd{rsv}[_atr{p}x{m}]
    """

    fast_period: int = 10
    slow_period: int = 60
    timeframe: str = "5m"
    rsv_period: int = 9
    k_smooth: int = 3
    d_smooth: int = 3
    atr_period: int = 0
    atr_mult: float = 2.0

    def __post_init__(self):
        atr_s = f"_atr{self.atr_period}x{self.atr_mult}" if self.atr_period > 0 else ""
        self.name = (
            f"kd_cross_{self.timeframe}"
            f"_{self.fast_period}_{self.slow_period}"
            f"_kd{self.rsv_period}{atr_s}"
        )
        self._fast = IncrementalEMA(self.fast_period)
        self._slow = IncrementalEMA(self.slow_period)
        self._kd   = IncrementalKD(self.rsv_period, self.k_smooth, self.d_smooth)
        self._atr: IncrementalATR | None = (
            IncrementalATR(self.atr_period) if self.atr_period > 0 else None
        )
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._prev_k: float | None = None
        self._prev_d: float | None = None
        self._prev_close: float | None = None
        self._atr_prev: float | None = None
        self._peak_high: float | None = None
        self._target: Direction = Direction.FLAT

    @property
    def current_target(self) -> Direction:
        return self._target

    def reset_position(self) -> None:
        """清除部位狀態及 EMA/KD 前一根記憶，防止 walk-forward 邊界假訊號。"""
        self._target = Direction.FLAT
        self._peak_high = None
        self._prev_fast = None
        self._prev_slow = None
        self._prev_k = None
        self._prev_d = None

    def _update_atr(self, tr: float | None) -> None:
        if self._atr is None or tr is None:
            return
        self._atr.update(tr)
        self._atr_prev = self._atr.value if self._atr.ready else None

    def on_bar(self, bar: BarEvent) -> SignalEvent | None:
        if bar.timeframe != self.timeframe:
            raise ValueError(
                f"strategy timeframe={self.timeframe} got bar timeframe={bar.timeframe}"
            )

        # True Range（在更新 ATR 前計算，用前一根 close）
        tr: float | None = None
        if self._atr is not None and self._prev_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )

        # EMA + KD 更新
        self._fast.update(bar.close)
        self._slow.update(bar.close)
        self._kd.update(bar.high, bar.low, bar.close)

        if not (self._fast.ready and self._slow.ready and self._kd.ready):
            self._update_atr(tr)
            self._prev_close = bar.close
            return None

        f, s = self._fast.value, self._slow.value
        k, d = self._kd.k, self._kd.d
        signal: SignalEvent | None = None

        # ── 1. ATR 移動停利（優先）────────────────────────────────────────
        if self._target == Direction.LONG and self._atr is not None:
            if self._peak_high is None or bar.high > self._peak_high:
                self._peak_high = bar.high
            if self._atr_prev is not None:
                trail_stop = self._peak_high - self.atr_mult * self._atr_prev
                if bar.close < trail_stop:
                    self._target = Direction.FLAT
                    self._peak_high = None
                    self._prev_fast, self._prev_slow = f, s
                    self._prev_k, self._prev_d = k, d
                    self._update_atr(tr)
                    self._prev_close = bar.close
                    return SignalEvent(bar.ts, Direction.FLAT, "atr_trail_stop")

        # ── 2. EMA 交叉 + KD 同步交叉 ────────────────────────────────────
        if self._prev_fast is not None and self._prev_k is not None:
            ema_up   = self._prev_fast <= self._prev_slow and f > s
            ema_down = self._prev_fast >= self._prev_slow and f < s
            kd_up    = self._prev_k <= self._prev_d and k > d

            # 進場：EMA 黃金交叉 + KD 黃金交叉同步
            if ema_up and kd_up and self._target == Direction.FLAT:
                self._target = Direction.LONG
                self._peak_high = bar.high
                signal = SignalEvent(bar.ts, Direction.LONG, "ema_kd_golden")

            # 出場：EMA 死亡交叉（不要求 KD 同步，只要趨勢破壞就出）
            elif ema_down and self._target == Direction.LONG:
                self._target = Direction.FLAT
                self._peak_high = None
                signal = SignalEvent(bar.ts, Direction.FLAT, "ema_death")

        self._prev_fast, self._prev_slow = f, s
        self._prev_k, self._prev_d = k, d
        self._update_atr(tr)
        self._prev_close = bar.close
        return signal
