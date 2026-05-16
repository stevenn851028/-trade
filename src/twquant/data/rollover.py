"""連續月合約串接（Panama backward adjustment）。

問題：台指期每月結算換倉，DB 中各月份合約的歷史價格不能直接拼接，
否則每月換月日會出現假跳空（contango / backwardation 造成的價差）。

Panama 方法（backward-adjusted）：
1. 偵測每日「主力月」（成交量最大者）
2. 主力月變動的日子 = 換月日 (rollover date)
3. 在換月日，計算 offset = next_month_close - current_month_close（同一日最後一根 bar）
4. 將該換月日以前**所有歷史 bar** 的價格加上 offset（cumulative）
5. 最近的合約不調整（保留真實價格），歷史價格被回溯調整以保持連續性

優點：
- 最近價格 = 真實市場價（實盤下單可直接用）
- 跨月報酬計算正確（沒有假跳空）
- 老舊價格可能不等於當時真實值，但對 backtest 沒影響（報酬率才是訊號）

存回 DB 時以 `month_code='CONT'` 與原始月份合約並存，
策略層查詢 CONT 即可取得連續序列。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)

CONTINUOUS_MONTH_CODE = "CONT"


@dataclass(frozen=True)
class RolloverEvent:
    date: date              # 換月日（前一主力月最後一個交易日）
    from_month: str         # 舊主力月
    to_month: str           # 新主力月
    offset: float           # to_month_close - from_month_close on 換月日


def _daily_dominant_month(bars_df: pd.DataFrame) -> pd.Series:
    """產生 Series：index=date, value=當日成交量最大的 contract_month。"""
    df = bars_df.copy()
    df["date"] = df["ts"].dt.date
    daily_vol = (df.groupby(["date", "contract_month"])["volume"]
                   .sum().unstack(fill_value=0))
    return daily_vol.idxmax(axis=1)


def detect_rollovers(bars_df: pd.DataFrame) -> list[RolloverEvent]:
    """偵測所有換月事件。

    輸入：包含多個 contract_month 的 bars DataFrame（不含 CONT）
    輸出：依時間排序的 RolloverEvent 清單
    """
    if bars_df.empty:
        return []

    df = bars_df.copy()
    df["date"] = df["ts"].dt.date
    daily_dom = _daily_dominant_month(df).sort_index()
    dates = list(daily_dom.index)

    events: list[RolloverEvent] = []
    for i in range(1, len(dates)):
        prev_d, cur_d = dates[i - 1], dates[i]
        prev_dom = daily_dom.loc[prev_d]
        cur_dom = daily_dom.loc[cur_d]
        if cur_dom == prev_dom:
            continue

        # 在 prev_d 同時取得 prev_dom 與 cur_dom 的最後一根 bar
        day_bars = df[df["date"] == prev_d]
        from_bars = day_bars[day_bars["contract_month"] == prev_dom].sort_values("ts")
        to_bars = day_bars[day_bars["contract_month"] == cur_dom].sort_values("ts")

        if from_bars.empty or to_bars.empty:
            # 新主力月在換月日當天還沒成交，跳過此換月（保守作法）
            log.warning("rollover %s → %s on %s: no overlap bars, skipping",
                        prev_dom, cur_dom, prev_d)
            continue

        offset = float(to_bars.iloc[-1]["close"] - from_bars.iloc[-1]["close"])
        events.append(RolloverEvent(prev_d, prev_dom, cur_dom, offset))

    return events


def build_continuous_series(
    bars_df: pd.DataFrame,
    rollovers: list[RolloverEvent] | None = None,
) -> pd.DataFrame:
    """以 Panama backward 法產生連續合約 bars。

    Args:
        bars_df: 含多個 contract_month 的原始 bars（不該含 CONT）
        rollovers: 若提供則直接用；否則自動 `detect_rollovers()`

    Returns:
        與輸入同欄位的 DataFrame，但所有 row 的 contract_month 已改為 'CONT'，
        price 欄位 (open/high/low/close) 已套用累積 offset。
    """
    if bars_df.empty:
        return bars_df.copy()

    if rollovers is None:
        rollovers = detect_rollovers(bars_df)

    df = bars_df.copy()
    df["_date"] = df["ts"].dt.date

    # 每日主力月
    daily_dom = _daily_dominant_month(df)
    df["_dominant"] = df["_date"].map(daily_dom)

    # 只保留主力月的 bar（即連續序列上的點）
    cont = df[df["contract_month"] == df["_dominant"]].copy()

    # Panama backward：愈早的 bar 累積愈多 offset
    # 將 rollovers 依時間倒序處理，累加 offset；該換月日（含）以前的 bar 都加
    cum_offset = 0.0
    cont["_adjustment"] = 0.0
    for ev in sorted(rollovers, key=lambda e: e.date, reverse=True):
        cum_offset += ev.offset
        mask = cont["_date"] <= ev.date
        cont.loc[mask, "_adjustment"] = cum_offset

    for col in ("open", "high", "low", "close"):
        cont[col] = cont[col] + cont["_adjustment"]

    cont["contract_month"] = CONTINUOUS_MONTH_CODE
    drop_cols = [c for c in ("_date", "_dominant", "_adjustment") if c in cont.columns]
    cont = cont.drop(columns=drop_cols).reset_index(drop=True)

    return cont
