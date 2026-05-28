"""KdEmaStrategy + IncrementalKD 單元測試。"""

from datetime import datetime, timedelta

import pytest

from twquant.data.session import TAIPEI
from twquant.events import BarEvent, Direction
from twquant.strategies.kd_ema import IncrementalKD, KdEmaStrategy


def make_bar(ts, close, high=None, low=None, tf="15m"):
    high = close if high is None else high
    low = close if low is None else low
    return BarEvent(ts=ts, timeframe=tf, product="TX", contract_month="CONT",
                    open=close, high=high, low=low, close=close, volume=100)


class TestIncrementalKD:
    def test_not_ready_before_period(self):
        kd = IncrementalKD(rsv_period=3)
        assert kd.update(10, 8, 9) is None
        assert kd.update(11, 9, 10) is None
        assert not kd.ready
        res = kd.update(12, 10, 11)
        assert kd.ready
        assert res is not None

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            IncrementalKD(rsv_period=0)

    def test_k_d_within_bounds(self):
        kd = IncrementalKD(rsv_period=3)
        import random
        random.seed(1)
        for _ in range(50):
            c = random.uniform(100, 200)
            kd.update(c + 5, c - 5, c)
            if kd.ready:
                assert 0 <= kd.k <= 100
                assert 0 <= kd.d <= 100

    def test_price_at_top_pushes_k_up(self):
        kd = IncrementalKD(rsv_period=3)
        # 一路在區間頂端收盤 → RSV=100 → K 應往上爬
        for i in range(10):
            kd.update(high=100 + i, low=90 + i, close=100 + i)  # close = high
        assert kd.k > 80

    def test_price_at_bottom_pushes_k_down(self):
        kd = IncrementalKD(rsv_period=3)
        for i in range(10):
            kd.update(high=100 - i, low=90 - i, close=90 - i)  # close = low
        assert kd.k < 20

    def test_flat_range_no_div_by_zero(self):
        kd = IncrementalKD(rsv_period=3)
        for _ in range(5):
            r = kd.update(100, 100, 100)  # hh == ll
        assert kd.ready
        # RSV 退化為 50，K/D 應趨向 50，不應 NaN/Inf
        assert 0 <= kd.k <= 100


class TestKdEmaStrategy:
    def _warmup(self, s, t0, n=70, price=100.0):
        """餵 n 根平盤 bar 讓 EMA + KD 都 ready。"""
        for i in range(n):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), price,
                              high=price + 1, low=price - 1))

    def test_name(self):
        assert KdEmaStrategy(timeframe="30m").name == "kd_ema_30m"

    def test_timeframe_mismatch_raises(self):
        s = KdEmaStrategy(timeframe="15m")
        with pytest.raises(ValueError, match="timeframe"):
            s.on_bar(make_bar(datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI), 100, tf="30m"))

    def test_no_signal_during_warmup(self):
        s = KdEmaStrategy(ema_period=10, rsv_period=5, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        for i in range(5):
            assert s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100)) is None

    def test_entry_requires_uptrend(self):
        """KD 黃金交叉但 close 在趨勢線之下（空頭）→ 不進場。"""
        s = KdEmaStrategy(ema_period=10, rsv_period=5,
                          kd_entry_max=100, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        sig_seen = []
        price = 200.0
        for i in range(40):
            price -= 2  # 持續下跌 → close < EMA
            jitter = 5 if i % 2 == 0 else -5
            sig = s.on_bar(make_bar(t0 + timedelta(minutes=15 * i),
                                    price + jitter, high=price + 10, low=price - 10))
            if sig:
                sig_seen.append(sig)
        assert all(x.target != Direction.LONG for x in sig_seen)

    def test_full_entry_then_exit_cycle(self):
        """穩定上升趨勢（close > EMA60）+ 溫和回檔讓 K 下探 + 反彈 KD 黃金交叉
        → 進場；之後大殺 KD 死亡交叉 → 出場。"""
        s = KdEmaStrategy(ema_period=20, rsv_period=9,
                          kd_entry_max=60, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)

        bars = []
        price = 100.0
        for i in range(45):       # 強多頭：close 遠在 EMA20 之上
            price += 2
            bars.append((price, price + 3, price - 1))
        for i in range(10):       # 溫和回檔：K 下探但 close 仍 > EMA20
            price -= 2
            bars.append((price, price + 1, price - 3))
        for i in range(6):        # 反彈製造 KD 低檔黃金交叉
            price += 3
            bars.append((price, price + 4, price - 1))
        for i in range(12):       # 大殺出場
            price -= 4
            bars.append((price, price + 1, price - 5))

        entered = exited = False
        for i, (c, h, l) in enumerate(bars):
            sig = s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), c, high=h, low=l))
            if sig and sig.target == Direction.LONG:
                entered = True
            if sig and sig.target == Direction.FLAT and entered:
                exited = True
        assert entered, "應該要有一次做多進場"
        assert exited, "應該要有一次平倉出場"

    def test_reset_position(self):
        s = KdEmaStrategy(timeframe="15m")
        s._target = Direction.LONG
        s.reset_position()
        assert s.current_target == Direction.FLAT
