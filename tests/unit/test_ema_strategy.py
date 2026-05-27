"""EmaCrossover 策略單元測試。"""

from datetime import datetime, timedelta

import pytest

from twquant.events import BarEvent, Direction
from twquant.strategies.ema_crossover import EmaCrossover, IncrementalEMA
from twquant.data.session import TAIPEI


def make_bar(ts: datetime, close: float, tf: str = "15m") -> BarEvent:
    return BarEvent(ts=ts, timeframe=tf, product="TX", contract_month="CONT",
                    open=close, high=close, low=close, close=close, volume=100)


class TestIncrementalEMA:
    def test_seeded_with_sma(self):
        ema = IncrementalEMA(period=3)
        assert not ema.ready
        ema.update(10)
        ema.update(20)
        assert not ema.ready
        v = ema.update(30)
        assert ema.ready
        assert v == pytest.approx(20.0)  # SMA of 10,20,30

    def test_post_seed_updates_use_alpha(self):
        ema = IncrementalEMA(period=2)
        ema.update(10)
        ema.update(20)
        assert ema.ready
        seed = ema.value          # 15
        # alpha = 2/3
        v = ema.update(30)
        expected = (2 / 3) * 30 + (1 / 3) * seed
        assert v == pytest.approx(expected)

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            IncrementalEMA(0)


class TestEmaCrossover:
    def test_no_signal_during_warmup(self):
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        for i in range(3):
            assert s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100.0)) is None

    def test_timeframe_mismatch_raises(self):
        s = EmaCrossover(timeframe="15m")
        with pytest.raises(ValueError, match="timeframe"):
            s.on_bar(make_bar(datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI), 100.0, tf="30m"))

    def test_golden_cross_emits_long(self):
        """資料設計：先讓快慢線等高，再讓 close 跳升製造黃金交叉。"""
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)

        # 把兩 EMA 都暖機到 100
        for i in range(4):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100.0))

        # 此時 fast == slow == 100。接著拉高，fast 會先超過 slow
        sig = s.on_bar(make_bar(t0 + timedelta(minutes=60), 200.0))
        assert sig is not None
        assert sig.target == Direction.LONG
        assert sig.reason == "golden_cross"
        assert s.current_target == Direction.LONG

    def test_death_cross_emits_flat(self):
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)

        for i in range(4):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100.0))
        # 黃金交叉
        s.on_bar(make_bar(t0 + timedelta(minutes=60), 200.0))
        s.on_bar(make_bar(t0 + timedelta(minutes=75), 200.0))

        # 急殺：fast 比 slow 反應快、下穿
        sig = None
        prices = [50, 50, 50, 50]
        for i, p in enumerate(prices):
            sig = s.on_bar(make_bar(t0 + timedelta(minutes=90 + 15 * i), p))
            if sig:
                break
        assert sig is not None
        assert sig.target == Direction.FLAT
        assert sig.reason == "death_cross"
        assert s.current_target == Direction.FLAT

    def test_no_signal_when_already_in_target_direction(self):
        """已是 LONG 時，第二次黃金交叉條件不應再次發訊號。"""
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m")
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        for i in range(4):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100.0))
        first = s.on_bar(make_bar(t0 + timedelta(minutes=60), 200.0))
        assert first is not None

        # 繼續往上拉，不該再產 signal
        for i in range(5):
            assert s.on_bar(make_bar(t0 + timedelta(minutes=75 + 15 * i), 300.0)) is None


class TestTrendFilter:
    def test_name_reflects_trend_period(self):
        s = EmaCrossover(timeframe="1d", trend_period=200)
        assert s.name == "ema_cross_1d_t200"

    def test_no_filter_when_trend_period_zero(self):
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m", trend_period=0)
        assert s._trend is None
        # 不需暖機 trend，行為與無濾網版一致
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        for i in range(4):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), 100.0))
        sig = s.on_bar(make_bar(t0 + timedelta(minutes=60), 200.0))
        assert sig is not None and sig.target == Direction.LONG

    def test_trend_filter_blocks_entry_when_close_below_trend(self):
        """大波段下跌後反彈，雖快慢線黃金交叉，但 close 仍低於長期趨勢線 → 阻擋進場。

        Pattern：10x200 (trend 暖機) → 5x80 (大跌)→ 1x110 (反彈製造黃金交叉)
        經手算 EMA：第 16 根 close=110 vs trend≈129 → 濾網應擋掉訊號。
        """
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m",
                         trend_period=10)
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        bars = [200.0] * 10 + [80.0] * 5 + [110.0]
        sig = None
        for i, p in enumerate(bars):
            sig = s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), p))
        # 最後那根本來會黃金交叉，但被趨勢濾網擋掉
        assert sig is None
        assert s.current_target == Direction.FLAT

    def test_trend_filter_allows_entry_when_close_above_trend(self):
        """大波段橫盤後爆漲：close > trend → 允許進場。

        Pattern：10x100 (trend=100) → 1x150 (close 遠大於 trend)
        """
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m",
                         trend_period=10)
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)
        bars = [100.0] * 10 + [150.0]
        sig = None
        for i, p in enumerate(bars):
            sig = s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), p))
        assert sig is not None
        assert sig.target == Direction.LONG

    def test_death_cross_unaffected_by_filter(self):
        """趨勢濾網只攔進場，平倉一律允許。"""
        s = EmaCrossover(fast_period=2, slow_period=4, timeframe="15m",
                         trend_period=10)
        t0 = datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI)

        # 先建立 LONG 部位（黃金交叉）：10x100 + 1x150
        for i, p in enumerate([100.0] * 10 + [150.0]):
            s.on_bar(make_bar(t0 + timedelta(minutes=15 * i), p))
        assert s.current_target == Direction.LONG

        # 急殺：1x80 應觸發死亡交叉（即便 close < trend，亦應允許出場）
        sig = s.on_bar(make_bar(t0 + timedelta(minutes=15 * 11), 80.0))
        assert sig is not None
        assert sig.target == Direction.FLAT
        assert sig.reason == "death_cross"
