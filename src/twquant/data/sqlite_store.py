"""SQLite bar 儲存層。

對應 `docs/DATA_PIPELINE.md § Phase 1：SQLite` 的單表設計，
所有時間週期共用 `bars` 表，以 `tf` 欄位區分（'1m' / '15m' / '30m' / '1d'）。

設計重點：
- `ts` 以 epoch seconds (UTC) INTEGER 儲存，便於範圍查詢、體積小
- 讀回時轉為 Asia/Taipei timezone-aware datetime
- `upsert_bars()` 用 `INSERT OR REPLACE`，允許重跑 pipeline 而不產生重複
- PRIMARY KEY (symbol, tf, month_code, ts) 阻止重複；連續合約以 'CONT' 為月份碼
- 商品代號儲存 TAIFEX 原始代碼（TX / MTX），不做轉換
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd

from twquant.data.session import TAIPEI

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT    NOT NULL,
    tf          TEXT    NOT NULL,
    month_code  TEXT    NOT NULL DEFAULT 'CONT',
    ts          INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    oi          INTEGER,
    PRIMARY KEY (symbol, tf, month_code, ts)
);
CREATE INDEX IF NOT EXISTS idx_bars_tf_symbol_ts ON bars(tf, symbol, ts);
"""

REQUIRED_COLS = ("ts", "timeframe", "product", "contract_month",
                 "open", "high", "low", "close", "volume")


def _to_epoch(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")
    return int(ts.timestamp())


def _from_epoch(epoch: int) -> datetime:
    return datetime.fromtimestamp(int(epoch), tz=TAIPEI)


class BarStore:
    """SQLite 連線包裝。可直接使用或用作 context manager。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.executescript(SCHEMA_SQL)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "BarStore":
        self.connect()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def upsert_bars(self, bars_df: pd.DataFrame) -> int:
        """Upsert bars 至 DB。

        輸入 DataFrame 欄位需符合 `bar_aggregator.aggregate_ticks_to_bars()` 輸出：
            ts, timeframe, product, contract_month, open, high, low, close, volume
        """
        if bars_df.empty:
            return 0

        missing = [c for c in REQUIRED_COLS if c not in bars_df.columns]
        if missing:
            raise ValueError(f"bars_df missing columns: {missing}")

        rows = []
        for r in bars_df.itertuples(index=False):
            rows.append((
                str(r.product),
                str(r.timeframe),
                str(r.contract_month) if r.contract_month else "CONT",
                _to_epoch(r.ts),
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                int(r.volume),
                None,  # oi 暫不寫入
            ))

        conn = self.connect()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO bars "
                "(symbol, tf, month_code, ts, open, high, low, close, volume, oi) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        month_code: str | None = None,
    ) -> pd.DataFrame:
        """以 symbol + timeframe 範圍查詢 bars。

        Args:
            symbol: 'TX' / 'MTX'
            timeframe: '15m' / '30m' / ...
            start, end: 包含區間（含），timezone-aware
            month_code: 若指定則只回該月（'202606' / 'CONT'）；None 回所有月

        回傳欄位與 `aggregate_ticks_to_bars()` 一致，ts 為 Asia/Taipei datetime。
        """
        sql = ["SELECT symbol, tf, month_code, ts, open, high, low, close, volume, oi "
               "FROM bars WHERE symbol = ? AND tf = ?"]
        params: list = [symbol, timeframe]

        if month_code is not None:
            sql.append("AND month_code = ?")
            params.append(month_code)
        if start is not None:
            sql.append("AND ts >= ?")
            params.append(_to_epoch(start))
        if end is not None:
            sql.append("AND ts <= ?")
            params.append(_to_epoch(end))
        sql.append("ORDER BY ts ASC")

        conn = self.connect()
        cur = conn.execute(" ".join(sql), params)
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=["ts", "timeframe", "product", "contract_month",
                                         "open", "high", "low", "close", "volume", "oi"])

        df = pd.DataFrame(rows, columns=["symbol", "tf", "month_code", "ts",
                                         "open", "high", "low", "close", "volume", "oi"])
        df["ts"] = [_from_epoch(e) for e in df["ts"]]
        df = df.rename(columns={"symbol": "product", "tf": "timeframe",
                                "month_code": "contract_month"})
        return df[["ts", "timeframe", "product", "contract_month",
                   "open", "high", "low", "close", "volume", "oi"]]

    def list_trade_dates(
        self,
        symbol: str,
        timeframe: str,
        month_code: str | None = None,
    ) -> list[date]:
        """列出 DB 內已有資料的 Taipei 交易日。"""
        sql = "SELECT DISTINCT ts FROM bars WHERE symbol = ? AND tf = ?"
        params: list = [symbol, timeframe]
        if month_code is not None:
            sql += " AND month_code = ?"
            params.append(month_code)

        conn = self.connect()
        cur = conn.execute(sql, params)
        return sorted({_from_epoch(r[0]).date() for r in cur.fetchall()})

    def stats(self) -> dict:
        """簡易統計：每 (symbol, tf) 的筆數與時間範圍。"""
        conn = self.connect()
        cur = conn.execute(
            "SELECT symbol, tf, COUNT(*), MIN(ts), MAX(ts) "
            "FROM bars GROUP BY symbol, tf ORDER BY symbol, tf"
        )
        out = {}
        for sym, tf, n, mn, mx in cur.fetchall():
            out[(sym, tf)] = {
                "count": n,
                "min_ts": _from_epoch(mn).isoformat() if mn else None,
                "max_ts": _from_epoch(mx).isoformat() if mx else None,
            }
        return out
