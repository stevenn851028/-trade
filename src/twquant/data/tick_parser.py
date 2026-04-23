"""解析 TAIFEX 盤後逐筆成交 CSV。

官方格式（Big5 編碼 ZIP 內含單一 CSV）：

原始欄位（含前後空白）：
    成交日期,商品代號,到期月份(週別),成交時間,成交價格,成交數量(B+S),近月價格,遠月價格,開盤集合競價

範例列：
    20240620   ,TX      ,202406  ,084500000,17500.00,2, , ,*

本模組將欄位正規化為下方 `Tick` 資料類別，並做必要的清洗：
- `trade_date` 解析為 `date`
- `trade_time` 支援 `HHMMSS` 或 `HHMMSSfff` 格式，轉為 Taipei timezone-aware datetime
- `product` / `contract_month` 去除空白
- `price` / `volume` 轉為數值
- 過濾鉅額交易（若有 T 欄位，但 Daily CSV 已不含鉅額）
- 組合競價標記 `is_opening_auction` 為布林

URL 格式（官方）：
    https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_YYYY_MM_DD.zip

僅提供最近 ~30 個交易日；更早資料需向 TAIFEX 申請。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO, Iterable, Iterator

import pandas as pd

from twquant.data.session import TAIPEI

# 原始欄位名稱（CSV header，中文含括號）
_COL_DATE = "成交日期"
_COL_PRODUCT = "商品代號"
_COL_MONTH = "到期月份(週別)"
_COL_TIME = "成交時間"
_COL_PRICE = "成交價格"
_COL_VOLUME = "成交數量(B+S)"
_COL_OPEN_AUCTION = "開盤集合競價"

RAW_COLUMNS = [
    _COL_DATE,
    _COL_PRODUCT,
    _COL_MONTH,
    _COL_TIME,
    _COL_PRICE,
    _COL_VOLUME,
    "近月價格",
    "遠月價格",
    _COL_OPEN_AUCTION,
]


@dataclass(frozen=True, slots=True)
class Tick:
    ts: datetime             # timezone-aware Taipei
    product: str             # 'TX' / 'MTX' / ...
    contract_month: str      # '202406' / '202406W3' ...
    price: float
    volume: int
    is_opening_auction: bool = False


def _parse_time_field(raw: str) -> tuple[int, int, int, int]:
    """TAIFEX 時間欄位可為 HHMMSS 或 HHMMSSfff（毫秒）。"""
    s = str(raw).strip()
    if len(s) < 6:
        s = s.zfill(6)
    hh = int(s[0:2])
    mm = int(s[2:4])
    ss = int(s[4:6])
    ms = int(s[6:9]) if len(s) >= 9 else 0
    return hh, mm, ss, ms


def _compose_datetime(trade_date: str, trade_time: str) -> datetime:
    """合併成交日期 (YYYYMMDD) 與成交時間；夜盤時間 > 23:59 的例外處理。"""
    d = datetime.strptime(str(trade_date).strip(), "%Y%m%d").date()
    hh, mm, ss, ms = _parse_time_field(trade_time)

    if hh >= 24:
        # 理論上不會發生，但以防萬一：減 24 並加一天
        hh -= 24
        d = d + timedelta(days=1)

    return datetime(d.year, d.month, d.day, hh, mm, ss, ms * 1000, tzinfo=TAIPEI)


def read_taifex_csv(
    source: str | Path | IO[bytes],
    products: Iterable[str] = ("TX",),
    encoding: str = "big5",
) -> pd.DataFrame:
    """讀取 TAIFEX Daily CSV（可為檔案路徑或 file-like）並轉為正規化 DataFrame。

    參數:
        source: CSV 路徑或 file-like object
        products: 只保留指定商品代號的資料（預設只留大台 TX）
        encoding: 原始檔編碼，TAIFEX 舊檔為 Big5，部份新檔可能為 UTF-8

    回傳欄位:
        ts, product, contract_month, price, volume, is_opening_auction
    """
    df = pd.read_csv(source, encoding=encoding, dtype=str, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"TAIFEX CSV missing columns: {missing}; got: {list(df.columns)}")

    for col in [_COL_DATE, _COL_PRODUCT, _COL_MONTH, _COL_TIME,
                _COL_PRICE, _COL_VOLUME, _COL_OPEN_AUCTION]:
        df[col] = df[col].astype(str).str.strip()

    df = df[df[_COL_PRODUCT].isin(set(products))].copy()
    if df.empty:
        return pd.DataFrame(columns=["ts", "product", "contract_month", "price",
                                     "volume", "is_opening_auction"])

    df["ts"] = [
        _compose_datetime(d, t)
        for d, t in zip(df[_COL_DATE], df[_COL_TIME])
    ]
    df["price"] = pd.to_numeric(df[_COL_PRICE], errors="coerce")
    df["volume"] = pd.to_numeric(df[_COL_VOLUME], errors="coerce").astype("Int64")
    df["is_opening_auction"] = df[_COL_OPEN_AUCTION].eq("*")
    df = df.rename(columns={_COL_PRODUCT: "product", _COL_MONTH: "contract_month"})

    df = df[["ts", "product", "contract_month", "price", "volume", "is_opening_auction"]]
    df = df.dropna(subset=["ts", "price", "volume"])
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def read_taifex_zip(
    zip_path: str | Path,
    products: Iterable[str] = ("TX",),
    encoding: str = "big5",
) -> pd.DataFrame:
    """讀取 TAIFEX Daily_*.zip，自動解壓並解析內含 CSV。"""
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"{zip_path}: zip contains no CSV")
        with zf.open(csv_names[0]) as f:
            return read_taifex_csv(io.BytesIO(f.read()), products=products, encoding=encoding)


def ticks_from_df(df: pd.DataFrame) -> Iterator[Tick]:
    """DataFrame → Tick iterator。"""
    for row in df.itertuples(index=False):
        yield Tick(
            ts=row.ts,
            product=row.product,
            contract_month=row.contract_month,
            price=float(row.price),
            volume=int(row.volume),
            is_opening_auction=bool(row.is_opening_auction),
        )


def pick_dominant_month(df: pd.DataFrame, trade_date: date) -> str | None:
    """找出當日成交量最大的月份（通常即近月主力），做連續合約用。"""
    if df.empty:
        return None
    agg = df.groupby("contract_month")["volume"].sum().sort_values(ascending=False)
    return str(agg.index[0]) if len(agg) else None
