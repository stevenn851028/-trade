"""FinMind 免費 daily futures data 拉取器。

FinMind API v4：
    https://api.finmindtrade.com/api/v4/data

免費版可取得 `TaiwanFuturesDaily` dataset（每日 OHLCV，2010+ 至今）。
1 分 K 與逐筆 tick 屬贊助版（NT$399/月）。

設計：
- token 為選用（無 token 也可呼叫，但 rate limit 較緊）
- 一次拉一個 product code（TX、MTX...）的整段歷史
- 同一日同一月份合約可能有 `regular`（日盤）與 `after_market`（夜盤）兩筆
  → 合併為單一日線：open=regular.open、close=after_market.close（無則 regular.close）
- 每日主力月（成交量最大）為「日線連續合約」候選 → 由 rollover 模組進一步處理
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from twquant.data.session import TAIPEI

log = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
DEFAULT_TIMEOUT = 30


class FinMindError(RuntimeError):
    pass


@dataclass
class FinMindClient:
    token: str | None = None
    base_url: str = FINMIND_URL
    timeout: int = DEFAULT_TIMEOUT

    def __post_init__(self):
        if self.token is None:
            self.token = os.environ.get("FINMIND_TOKEN")

    def _request(self, params: dict) -> dict:
        if self.token:
            params = dict(params, token=self.token)
        url = f"{self.base_url}?{urlencode(params)}"
        log.debug("FinMind GET %s", url.replace(self.token or "", "***") if self.token else url)
        req = Request(url, headers={"User-Agent": "twquant/0.1"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except HTTPError as e:
            raise FinMindError(f"HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            raise FinMindError(f"network: {e.reason}") from e

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise FinMindError(f"bad JSON: {e}") from e

        if int(payload.get("status", 0)) != 200:
            raise FinMindError(f"FinMind error: {payload.get('msg')} (status={payload.get('status')})")
        return payload

    def fetch_taiwan_futures_daily(
        self,
        product: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """取得指定產品的每日 OHLCV，可能含多月份合約與日 / 夜盤兩筆。"""
        payload = self._request({
            "dataset": "TaiwanFuturesDaily",
            "data_id": product,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })
        rows = payload.get("data", [])
        if not rows:
            log.warning("FinMind returned 0 rows for %s %s..%s", product, start, end)
            return pd.DataFrame()
        return pd.DataFrame(rows)


def consolidate_daily_bars(raw_df: pd.DataFrame) -> pd.DataFrame:
    """將 FinMind raw 日線（含 regular / after_market）合併為單一日線記錄。

    輸入欄位（FinMind TaiwanFuturesDaily）:
        date, futures_id, contract_date, open, high, low, close, volume,
        settlement_price, open_interest, trading_session

    輸出欄位（對齊 sqlite bars schema 風格）:
        ts (Taipei tz, 設為 13:45 為日盤收盤；夜盤合併進來），timeframe='1d',
        product, contract_month, open, high, low, close, volume, oi
    """
    if raw_df.empty:
        return pd.DataFrame(columns=["ts", "timeframe", "product", "contract_month",
                                     "open", "high", "low", "close", "volume", "oi"])

    df = raw_df.copy()
    df["contract_date"] = df["contract_date"].astype(str).str.strip()

    grouped = df.groupby(["date", "futures_id", "contract_date"], as_index=False)

    def _combine(g):
        # 用 trading_session 排序：regular 先、after_market 後
        order = {"regular": 0, "after_market": 1}
        g = g.copy()
        g["_o"] = g["trading_session"].map(order).fillna(0)
        g = g.sort_values("_o")
        return pd.Series({
            "open": g.iloc[0]["open"],
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": g.iloc[-1]["close"],
            "volume": int(g["volume"].sum()),
            "oi": int(g["open_interest"].iloc[-1]) if "open_interest" in g.columns else None,
        })

    combined = grouped.apply(_combine, include_groups=False).reset_index()

    # 標準化時間戳：日盤收盤 13:45 Asia/Taipei（夜盤資料合進來）
    combined["ts"] = pd.to_datetime(combined["date"]).dt.tz_localize(
        TAIPEI).dt.normalize() + pd.Timedelta(hours=13, minutes=45)

    combined = combined.rename(columns={"futures_id": "product",
                                        "contract_date": "contract_month"})
    combined["timeframe"] = "1d"
    return combined[["ts", "timeframe", "product", "contract_month",
                     "open", "high", "low", "close", "volume", "oi"]]
