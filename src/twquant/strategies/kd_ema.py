"""KD + EMA 組合策略（方式 1：EMA 定方向 + KD 抓時機）。

對應使用者選定的設計：
- 進場（做多）：EMA 多頭排列（快 > 慢）**且** KD 黃金交叉**且** K 在相對低檔
  → 順勢但不追高，等回檔再買，直攻純 EMA 交叉「勝率低、追高被洗」的痛點
- 出場（平倉）：KD 死亡交叉 **或** EMA 翻空（趨勢破壞）

KD（隨機指標）預設參數 9,3,3（台股最常見）：
    RSV = (close - LL_n) / (HH_n - LL_n) * 100        n = 9
    K   = (1 - 1/k_smooth) * K_prev + (1/k_smooth) * RSV   k_smooth = 3
    D   = (1 - 1/d_smooth) * D_prev + (1/d_smooth) * K     d_smooth = 3
    K, D 初始值 50
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from twquant.events import BarEvent, Direction, SignalEvent
from twquant.strategies.base import Strategy
from twquant.strategies.ema_crossover import IncrementalEMA


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
