"""Tick → K 棒聚合。

OHLCV 定義：
    open   = 該 bar 內第一筆 tick 成交價
    high   = 該 bar 內最高成交價
    low    = 該 bar 內最低成交價
    close  = 該 bar 內最後一筆 tick 成交價
    volume = 該 bar 內所有 tick 成交量總和

bar 時間戳 (`ts`) 採用 **bar 結束時間**（end-label），與策略層「收盤確認訊號」一致。
見 `session.py` bar_end() 的區間定義（左開右閉 (start, end]）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from twquant.data.session import bar_end


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime       # bar 結束時間（timezone-aware, Taipei）
    timeframe: str     # "1m" / "15m" / "30m"
    product: str
    contract_month: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def aggregate_ticks_to_bars(
    ticks_df: pd.DataFrame,
    bar_size_min: int,
    timeframe_label: str | None = None,
) -> pd.DataFrame:
    """將正規化的 tick DataFrame 聚合為 K 棒。

    參數:
        ticks_df: `tick_parser.read_taifex_csv()` 產出的 DataFrame
        bar_size_min: K 棒大小（分鐘），1 / 15 / 30 等
        timeframe_label: 輸出標籤，預設 "{N}m"

    回傳:
        columns = [ts, timeframe, product, contract_month, open, high, low, close, volume]
    """
    label = timeframe_label or f"{bar_size_min}m"

    if ticks_df.empty:
        return pd.DataFrame(columns=["ts", "timeframe", "product", "contract_month",
                                     "open", "high", "low", "close", "volume"])

    df = ticks_df.copy()
    df["bar_ts"] = [bar_end(t, bar_size_min) for t in df["ts"]]
    df = df.dropna(subset=["bar_ts"])  # 非交易時段的 tick 被丟棄

    grouped = df.groupby(["product", "contract_month", "bar_ts"], sort=True)

    bars = grouped.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume", "sum"),
    ).reset_index()

    bars = bars.rename(columns={"bar_ts": "ts"})
    bars["timeframe"] = label
    bars["volume"] = bars["volume"].astype(int)
    bars = bars[["ts", "timeframe", "product", "contract_month",
                 "open", "high", "low", "close", "volume"]]
    return bars.sort_values(["product", "contract_month", "ts"]).reset_index(drop=True)


def aggregate_bars_to_higher(
    bars_df: pd.DataFrame,
    target_bar_min: int,
    target_label: str | None = None,
) -> pd.DataFrame:
    """將 1m / 5m bars 聚合成更大週期（例如 1m → 15m / 30m）。

    用途：在 pipeline 內以 1m 為「單一真相來源」，再向上聚合 15m 與 30m，
    確保兩週期嚴格對齊（30m 的每根 = 連續兩根 15m）。
    """
    label = target_label or f"{target_bar_min}m"
    if bars_df.empty:
        return bars_df.assign(timeframe=label)

    df = bars_df.copy()
    # 以現有 bar 的「ts」時間點回推其所屬 tick 時間，再用 session.bar_end 推出目標 bar
    df["bar_ts"] = [bar_end(t, target_bar_min) for t in df["ts"]]
    df = df.dropna(subset=["bar_ts"])

    grouped = df.groupby(["product", "contract_month", "bar_ts"], sort=True)

    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index().rename(columns={"bar_ts": "ts"})

    bars["timeframe"] = label
    bars["volume"] = bars["volume"].astype(int)
    bars = bars[["ts", "timeframe", "product", "contract_month",
                 "open", "high", "low", "close", "volume"]]
    return bars.sort_values(["product", "contract_month", "ts"]).reset_index(drop=True)
