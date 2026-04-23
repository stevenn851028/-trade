"""台指期交易時段（日盤、夜盤）邏輯。

台北時間（UTC+8）：
- 日盤：08:45 – 13:45
- 夜盤：15:00 – 次日 05:00（跨日）

本模組負責：
1. 判斷 tick 屬於哪個時段
2. 計算 tick 落在哪根 bar 的結束時間（end-label）
3. 產生一個交易日預期的 bar 時間序列（供資料品質檢查）

bar 結束時間規則：使用「**左閉右閉**」區間，即 bar 以 (start, end] 涵蓋該時間桶的 tick，
其中 end 為該 bar 的 canonical timestamp，與策略層「K 棒收盤確認訊號」一致。

特別注意：
- 日盤第一根 15m bar 為 (08:45, 09:00]；第一根 30m bar 為 (08:45, 09:15]
- 日盤 08:45:00 的開盤 tick 歸屬於該根 bar（透過 open-tick 例外處理）
- 夜盤第一根 15m bar 為 (15:00, 15:15]；30m 為 (15:00, 15:30]
- 夜盤跨日 05:00:00 之 tick 已屬於收盤集合競價，歸到當日夜盤最後一根
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

DAY_OPEN = time(8, 45)
DAY_CLOSE = time(13, 45)
NIGHT_OPEN = time(15, 0)
NIGHT_CLOSE = time(5, 0)  # next day


class SessionKind(str, Enum):
    DAY = "day"
    NIGHT = "night"


@dataclass(frozen=True)
class Session:
    """單一 session 的時間區間，`start` 與 `end` 皆為 timezone-aware datetime。"""

    kind: SessionKind
    start: datetime
    end: datetime

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts <= self.end

    def expected_bar_count(self, bar_size_min: int) -> int:
        total_min = int((self.end - self.start).total_seconds() // 60)
        return total_min // bar_size_min


def day_session(d: date) -> Session:
    start = datetime.combine(d, DAY_OPEN, tzinfo=TAIPEI)
    end = datetime.combine(d, DAY_CLOSE, tzinfo=TAIPEI)
    return Session(SessionKind.DAY, start, end)


def night_session(d: date) -> Session:
    """d 代表夜盤開盤當日；結束在 d+1 的 05:00。"""
    start = datetime.combine(d, NIGHT_OPEN, tzinfo=TAIPEI)
    end = datetime.combine(d + timedelta(days=1), NIGHT_CLOSE, tzinfo=TAIPEI)
    return Session(SessionKind.NIGHT, start, end)


def classify(ts: datetime) -> Session | None:
    """判斷 tick 屬於哪個 session；不屬任何 session 則回傳 None。"""
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")
    ts = ts.astimezone(TAIPEI)

    day = day_session(ts.date())
    if day.contains(ts):
        return day

    night_today = night_session(ts.date())
    if night_today.contains(ts):
        return night_today

    # 可能屬於前一日的夜盤（凌晨 00:00-05:00）
    night_prev = night_session(ts.date() - timedelta(days=1))
    if night_prev.contains(ts):
        return night_prev

    return None


def bar_end(ts: datetime, bar_size_min: int) -> datetime | None:
    """回傳 `ts` 所屬 bar 的結束時間（canonical timestamp）。

    使用左開右閉 (start, end] 區間；若 ts 正好等於 session.start（開盤 tick），
    視為該 session 第一根 bar 的成員。

    非交易時段回傳 None。
    """
    sess = classify(ts)
    if sess is None:
        return None

    # 相對於 session start 的分鐘數
    elapsed_sec = (ts - sess.start).total_seconds()
    if elapsed_sec == 0:
        # 開盤 tick → 第一根 bar
        return sess.start + timedelta(minutes=bar_size_min)

    # ceil(elapsed_sec / (bar_size * 60)) * bar_size
    elapsed_min_float = elapsed_sec / 60
    import math
    bucket = math.ceil(elapsed_min_float / bar_size_min)
    end = sess.start + timedelta(minutes=bucket * bar_size_min)

    # clamp 到 session.end（收盤後 0 秒內也算最後一根）
    if end > sess.end:
        end = sess.end

    return end


def expected_bar_ends(d: date, bar_size_min: int, include_night: bool = True) -> list[datetime]:
    """產生交易日 d 的所有預期 bar 結束時間，供資料品質檢查使用。

    Args:
        d: 交易日（Taipei 日期）
        bar_size_min: K 棒分鐘數
        include_night: 是否包含夜盤（夜盤屬於 d 當晚 15:00 至 d+1 05:00）
    """
    ends: list[datetime] = []

    day = day_session(d)
    for i in range(1, day.expected_bar_count(bar_size_min) + 1):
        ends.append(day.start + timedelta(minutes=i * bar_size_min))

    if include_night:
        night = night_session(d)
        for i in range(1, night.expected_bar_count(bar_size_min) + 1):
            ends.append(night.start + timedelta(minutes=i * bar_size_min))

    return ends
