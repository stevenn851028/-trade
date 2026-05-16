"""rollover.py 單元測試（合成多月份資料）。"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from twquant.data.rollover import (
    CONTINUOUS_MONTH_CODE,
    RolloverEvent,
    build_continuous_series,
    detect_rollovers,
)
from twquant.data.session import TAIPEI


def make_bar(ts: datetime, month: str, close: float,
             product: str = "TX", tf: str = "30m", volume: int = 100) -> dict:
    return {
        "ts": ts,
        "timeframe": tf,
        "product": product,
        "contract_month": month,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": volume,
    }


def _bars_for_day(d: date, month: str, base_close: float, volume: int = 100,
                  hours: tuple[int, ...] = (9, 11, 13)) -> list[dict]:
    """產生一日中數根 bars（簡化用）。"""
    bars = []
    for i, h in enumerate(hours):
        ts = datetime(d.year, d.month, d.day, h, 0, tzinfo=TAIPEI)
        bars.append(make_bar(ts, month, base_close + i, volume=volume))
    return bars


class TestDetectRollovers:
    def test_no_rollover_when_single_month(self):
        rows = []
        for i in range(3):
            rows += _bars_for_day(date(2026, 4, 13) + timedelta(days=i), "202604", 17000.0)
        df = pd.DataFrame(rows)
        assert detect_rollovers(df) == []

    def test_single_rollover_detected_with_correct_offset(self):
        rows = []
        d1, d2, d3 = date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 16)
        # day 1 & 2: 202604 主力（volume 高）；同日 202605 也有交易（volume 低）
        rows += _bars_for_day(d1, "202604", 17000.0, volume=1000)
        rows += _bars_for_day(d1, "202605", 17050.0, volume=100)
        rows += _bars_for_day(d2, "202604", 17010.0, volume=1000)
        rows += _bars_for_day(d2, "202605", 17060.0, volume=100)
        # day 3: 202605 變主力
        rows += _bars_for_day(d3, "202604", 17020.0, volume=50)
        rows += _bars_for_day(d3, "202605", 17070.0, volume=1000)
        df = pd.DataFrame(rows)

        events = detect_rollovers(df)
        assert len(events) == 1
        ev = events[0]
        assert ev.date == d2          # 換月發生在 d2 → d3 之間，標記在 d2
        assert ev.from_month == "202604"
        assert ev.to_month == "202605"
        # offset = 202605_close - 202604_close on d2 (last bar)
        # last bar prices: 202604=17010+2=17012, 202605=17060+2=17062
        assert ev.offset == pytest.approx(50.0)

    def test_skips_rollover_when_target_has_no_overlap(self):
        d1, d2 = date(2026, 4, 14), date(2026, 4, 15)
        rows = _bars_for_day(d1, "202604", 17000.0, volume=1000)
        rows += _bars_for_day(d2, "202605", 17070.0, volume=1000)
        # 沒有重疊日
        df = pd.DataFrame(rows)
        events = detect_rollovers(df)
        assert events == []


class TestBuildContinuous:
    def test_no_rollover_returns_input_as_cont(self):
        rows = []
        for i in range(3):
            rows += _bars_for_day(date(2026, 4, 13) + timedelta(days=i), "202604", 17000.0)
        df = pd.DataFrame(rows)
        cont = build_continuous_series(df)
        assert set(cont["contract_month"]) == {CONTINUOUS_MONTH_CODE}
        assert len(cont) == len(df)
        assert list(cont["close"]) == list(df["close"])

    def test_panama_backward_adjustment_applied(self):
        d1, d2, d3 = date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 16)
        rows = []
        rows += _bars_for_day(d1, "202604", 17000.0, volume=1000)
        rows += _bars_for_day(d1, "202605", 17050.0, volume=100)
        rows += _bars_for_day(d2, "202604", 17010.0, volume=1000)
        rows += _bars_for_day(d2, "202605", 17060.0, volume=100)
        rows += _bars_for_day(d3, "202604", 17020.0, volume=50)
        rows += _bars_for_day(d3, "202605", 17070.0, volume=1000)
        df = pd.DataFrame(rows)

        cont = build_continuous_series(df)
        # 應該只有 9 根 bar（每日 3 根 × 3 日）
        assert len(cont) == 9
        # 最近一日（d3 = 202605 主力）不應調整
        d3_bars = cont[cont["ts"].dt.date == d3].sort_values("ts")
        assert list(d3_bars["close"]) == [17070.0, 17071.0, 17072.0]
        # 前兩日（202604 主力，offset=+50）應被調整
        d1_bars = cont[cont["ts"].dt.date == d1].sort_values("ts")
        assert list(d1_bars["close"]) == [17050.0, 17051.0, 17052.0]
        d2_bars = cont[cont["ts"].dt.date == d2].sort_values("ts")
        assert list(d2_bars["close"]) == [17060.0, 17061.0, 17062.0]

    def test_two_rollovers_cumulative_offset(self):
        """三個月份合約鏈：M1 → M2 → M3，分別 +50、+30 offset。
        最早一段應累積 +80 調整。"""
        d1, d2, d3, d4 = (date(2026, 3, 15), date(2026, 3, 16),
                          date(2026, 4, 15), date(2026, 4, 16))
        rows = []
        # d1: M1 主力，M2 也有
        rows += _bars_for_day(d1, "202604", 17000.0, volume=1000)
        rows += _bars_for_day(d1, "202605", 17050.0, volume=100)
        # d2: M2 變主力（offset M1→M2 = +50）
        rows += _bars_for_day(d2, "202604", 17010.0, volume=100)
        rows += _bars_for_day(d2, "202605", 17060.0, volume=1000)
        # d3: M2 主力，M3 有少量
        rows += _bars_for_day(d3, "202605", 17070.0, volume=1000)
        rows += _bars_for_day(d3, "202606", 17100.0, volume=100)
        # d4: M3 變主力（offset M2→M3 = +30）
        rows += _bars_for_day(d4, "202605", 17080.0, volume=100)
        rows += _bars_for_day(d4, "202606", 17110.0, volume=1000)
        df = pd.DataFrame(rows)

        cont = build_continuous_series(df)
        # d4 (M3 主力) 不調整：17110, 17111, 17112
        d4_bars = cont[cont["ts"].dt.date == d4].sort_values("ts")
        assert list(d4_bars["close"]) == [17110.0, 17111.0, 17112.0]
        # d3 (M2 主力) 加 +30：原 17070,71,72 → 17100,01,02
        d3_bars = cont[cont["ts"].dt.date == d3].sort_values("ts")
        assert list(d3_bars["close"]) == [17100.0, 17101.0, 17102.0]
        # d2 (M2 主力) 加 +30：原 17060,61,62 → 17090,91,92
        d2_bars = cont[cont["ts"].dt.date == d2].sort_values("ts")
        assert list(d2_bars["close"]) == [17090.0, 17091.0, 17092.0]
        # d1 (M1 主力) 累積 +30 + +50 = +80：原 17000,01,02 → 17080,81,82
        d1_bars = cont[cont["ts"].dt.date == d1].sort_values("ts")
        assert list(d1_bars["close"]) == [17080.0, 17081.0, 17082.0]

    def test_continuity_no_gap_at_rollover(self):
        """換月日前後相鄰 bar 的價差應反映真實市場變動，不含合約跳空。"""
        d1, d2 = date(2026, 4, 15), date(2026, 4, 16)
        rows = []
        # M1 主力，M2 比 M1 貴 50 點
        rows += _bars_for_day(d1, "202604", 17000.0, volume=1000)
        rows += _bars_for_day(d1, "202605", 17050.0, volume=100)
        # 換月：M2 主力，且 M2 從 17070 開盤
        rows += _bars_for_day(d2, "202605", 17070.0, volume=1000)
        df = pd.DataFrame(rows)
        cont = build_continuous_series(df).sort_values("ts").reset_index(drop=True)

        # d1 最後一根（已調整）的 close 應接近 d2 第一根的 close
        # d1 末筆 = 17002 + 50 = 17052
        # d2 首筆 = 17070
        # 真實變動 = 17070 - 17052 = 18（這就是 M2 contract 在這段時間內的真實變動）
        # 而非合約跳空的 50 點
        d1_last_close = cont[cont["ts"].dt.date == d1].sort_values("ts").iloc[-1]["close"]
        d2_first_close = cont[cont["ts"].dt.date == d2].sort_values("ts").iloc[0]["close"]
        gap = d2_first_close - d1_last_close
        assert abs(gap) < 30  # 不應該是 ~50 的合約跳空
