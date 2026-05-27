"""Shioaji 1 分 K 歷史資料拉取器（永豐證券官方 SDK）。

需求:
    pip install shioaji
    永豐證券帳戶 + API key/secret（申請 API 程式交易）

設計:
- shioaji 為可選相依（lazy import），無 key 時不應 import error
- TXFR1 = 連續近月合約（永豐已自動處理 rollover）
- 一次最多查 30 日，本模組自動 chunk
- 回傳標準化 1m bars DataFrame，欄位對齊 bar_aggregator 輸出
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from twquant.data.session import TAIPEI

log = logging.getLogger(__name__)

# Shioaji API 一次請求最多 30 日，保險用 25
CHUNK_DAYS = 25


class ShioajiNotInstalled(RuntimeError):
    """提示使用者執行 pip install shioaji。"""


class ShioajiError(RuntimeError):
    pass


@dataclass
class ShioajiClient:
    """Shioaji 包裝層；只 import 在 connect() 內，缺套件時可建構不爆炸。"""

    api_key: str
    secret_key: str
    simulation: bool = False
    _api: Any = field(default=None, repr=False)

    def __post_init__(self):
        if not self.api_key or not self.secret_key:
            raise ValueError("api_key and secret_key required")

    def connect(self) -> None:
        if self._api is not None:
            return
        try:
            import shioaji as sj
        except ImportError as e:
            raise ShioajiNotInstalled(
                "shioaji 未安裝。請先執行：pip install shioaji"
            ) from e
        log.info("Connecting to Shioaji (simulation=%s)", self.simulation)
        self._api = sj.Shioaji(simulation=self.simulation)
        accounts = self._api.login(api_key=self.api_key, secret_key=self.secret_key)
        log.info("Shioaji logged in; %d account(s)", len(accounts))

    def logout(self) -> None:
        if self._api is not None:
            try:
                self._api.logout()
            except Exception as e:  # noqa: BLE001
                log.warning("logout failed: %s", e)
            self._api = None

    def __enter__(self) -> "ShioajiClient":
        self.connect()
        return self

    def __exit__(self, *_a) -> None:
        self.logout()

    def _resolve_contract(self, symbol: str):
        """以字串符號 (TXFR1 / TXFR2 / MXFR1) 取得 Shioaji Futures contract 物件。"""
        self.connect()
        futures = self._api.Contracts.Futures
        # 嘗試屬性存取（永豐慣用：TXFR1 → 近月）
        if hasattr(futures, symbol):
            return getattr(futures, symbol)
        # 退路：嘗試從字典/list 找
        try:
            return futures[symbol]
        except Exception as e:
            raise ShioajiError(f"contract not found: {symbol}") from e

    def fetch_1m_kbars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """取得指定符號 [start, end] 區間的 1 分 K，自動 chunk 並合併。

        Returns:
            標準化 DataFrame，欄位:
                ts (Asia/Taipei tz-aware), timeframe='1m',
                product, contract_month='CONT', open, high, low, close, volume
        """
        self.connect()
        contract = self._resolve_contract(symbol)

        pieces: list[pd.DataFrame] = []
        d = start
        while d <= end:
            chunk_end = min(d + timedelta(days=CHUNK_DAYS - 1), end)
            log.info("Fetching %s %s..%s", symbol, d, chunk_end)
            kbars = self._api.kbars(
                contract=contract,
                start=d.isoformat(),
                end=chunk_end.isoformat(),
            )
            df = _kbars_to_dataframe(kbars)
            if not df.empty:
                pieces.append(df)
            d = chunk_end + timedelta(days=1)

        if not pieces:
            return pd.DataFrame(columns=["ts", "timeframe", "product",
                                         "contract_month", "open", "high",
                                         "low", "close", "volume"])

        raw = pd.concat(pieces, ignore_index=True)
        return normalize_kbars(raw, symbol)


def _kbars_to_dataframe(kbars: Any) -> pd.DataFrame:
    """Shioaji kbars 回傳物件 → pandas DataFrame（API 版本相容處理）。"""
    if kbars is None:
        return pd.DataFrame()
    if isinstance(kbars, pd.DataFrame):
        return kbars
    # Shioaji 新版回傳具 ts/Open/High/Low/Close/Volume 等 list-like 屬性的物件
    try:
        return pd.DataFrame({
            "ts": list(kbars.ts),
            "Open": list(kbars.Open),
            "High": list(kbars.High),
            "Low": list(kbars.Low),
            "Close": list(kbars.Close),
            "Volume": list(kbars.Volume),
        })
    except AttributeError:
        # 退路：try dict-like
        try:
            return pd.DataFrame(dict(kbars))
        except Exception as e:
            raise ShioajiError(f"unknown kbars format: {type(kbars)}") from e


def normalize_kbars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Shioaji raw KBar DataFrame → 標準化為我們的 1m bars schema。"""
    if df.empty:
        return pd.DataFrame(columns=["ts", "timeframe", "product",
                                     "contract_month", "open", "high",
                                     "low", "close", "volume"])

    out = pd.DataFrame()

    # ts：Shioaji 回傳 ns epoch 或 datetime；統一轉 Asia/Taipei tz-aware
    ts_raw = df["ts"] if "ts" in df.columns else df.index
    ts = pd.to_datetime(ts_raw)
    if ts.dt.tz is None:
        # Shioaji 預設台北本地時間；補上 tz
        ts = ts.dt.tz_localize(TAIPEI)
    else:
        ts = ts.dt.tz_convert(TAIPEI)
    out["ts"] = ts

    out["timeframe"] = "1m"

    # symbol → product: TXFR1/TXFR2/TXF202506 → TX；MXFR1/MXF... → MTX
    if symbol.startswith("TXF") or symbol.startswith("TXFR"):
        out["product"] = "TX"
    elif symbol.startswith("MXF") or symbol.startswith("MXFR"):
        out["product"] = "MTX"
    else:
        out["product"] = symbol

    # TXFR1 永豐已自動連續處理，視為 CONT
    out["contract_month"] = "CONT"

    out["open"] = df["Open"].astype(float)
    out["high"] = df["High"].astype(float)
    out["low"] = df["Low"].astype(float)
    out["close"] = df["Close"].astype(float)
    out["volume"] = df["Volume"].astype(int)

    out = out.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)
    return out


def credentials_from_env() -> tuple[str, str]:
    """從環境變數讀 API key / secret，未設則 raise。"""
    k = os.environ.get("SHIOAJI_API_KEY")
    s = os.environ.get("SHIOAJI_SECRET_KEY")
    if not k or not s:
        raise RuntimeError(
            "請設定環境變數 SHIOAJI_API_KEY 與 SHIOAJI_SECRET_KEY"
        )
    return k, s
