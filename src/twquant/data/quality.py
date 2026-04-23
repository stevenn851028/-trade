"""資料品質檢查。

對照 `docs/DATA_PIPELINE.md § 資料品質檢查` 的規則：
- K 棒缺漏（15m 預期 76 根/日、30m 預期 38 根/日）
- OHLC 邏輯（low ≤ open, close ≤ high）
- 跳空（abs(open[t] - close[t-1]) / close[t-1] > 3%）
- 成交量異常（單根 > 30 日均值 × 10）
- 時間序列（無重複、無亂序）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from twquant.data.session import expected_bar_ends


@dataclass
class QualityReport:
    trade_date: date
    timeframe: str
    expected_bars: int
    actual_bars: int
    missing_bar_ends: list = field(default_factory=list)
    ohlc_violations: int = 0
    gap_flags: int = 0
    volume_outliers: int = 0
    duplicates: int = 0
    out_of_order: int = 0

    @property
    def missing_count(self) -> int:
        return len(self.missing_bar_ends)

    def is_clean(self) -> bool:
        return (
            self.missing_count == 0
            and self.ohlc_violations == 0
            and self.duplicates == 0
            and self.out_of_order == 0
        )

    def summary(self) -> str:
        return (
            f"[{self.timeframe}] {self.trade_date}: "
            f"bars={self.actual_bars}/{self.expected_bars}, "
            f"missing={self.missing_count}, "
            f"ohlc_violations={self.ohlc_violations}, "
            f"gaps={self.gap_flags}, "
            f"vol_outliers={self.volume_outliers}, "
            f"dups={self.duplicates}, ooo={self.out_of_order}"
        )


def check_bars(
    bars_df: pd.DataFrame,
    trade_date: date,
    bar_size_min: int,
    *,
    gap_threshold: float = 0.03,
    volume_outlier_multiplier: float = 10.0,
    rolling_window: int = 30,
    include_night: bool = True,
) -> QualityReport:
    """對單日某一週期的 bars 跑完整品質檢查。

    `bars_df` 欄位需符合 `bar_aggregator.aggregate_ticks_to_bars()` 輸出。
    """
    label = f"{bar_size_min}m"
    rep = QualityReport(
        trade_date=trade_date,
        timeframe=label,
        expected_bars=0,
        actual_bars=0,
    )

    expected = expected_bar_ends(trade_date, bar_size_min, include_night=include_night)
    rep.expected_bars = len(expected)

    if bars_df.empty:
        rep.missing_bar_ends = expected
        return rep

    tf_bars = bars_df[bars_df["timeframe"] == label].copy()
    tf_bars = tf_bars.sort_values("ts").reset_index(drop=True)
    rep.actual_bars = len(tf_bars)

    actual_ts = set(tf_bars["ts"])
    rep.missing_bar_ends = [e for e in expected if e not in actual_ts]

    # OHLC 邏輯
    ohlc_bad = (
        (tf_bars["low"] > tf_bars["open"])
        | (tf_bars["low"] > tf_bars["close"])
        | (tf_bars["high"] < tf_bars["open"])
        | (tf_bars["high"] < tf_bars["close"])
        | (tf_bars["low"] > tf_bars["high"])
    )
    rep.ohlc_violations = int(ohlc_bad.sum())

    # 跳空
    if len(tf_bars) >= 2:
        prev_close = tf_bars["close"].shift(1)
        gap = (tf_bars["open"] - prev_close).abs() / prev_close
        rep.gap_flags = int((gap > gap_threshold).fillna(False).sum())

    # 成交量異常（以自身滾動均值為基準，而非跨日 30 天，因這裡是單日函式）
    if len(tf_bars) >= rolling_window:
        rolling_mean = tf_bars["volume"].rolling(rolling_window, min_periods=rolling_window).mean()
        outlier = tf_bars["volume"] > (rolling_mean * volume_outlier_multiplier)
        rep.volume_outliers = int(outlier.fillna(False).sum())

    # 重複與亂序
    rep.duplicates = int(tf_bars["ts"].duplicated().sum())
    ts_series = tf_bars["ts"]
    rep.out_of_order = int((ts_series.diff().dropna() <= pd.Timedelta(0)).sum())

    return rep
