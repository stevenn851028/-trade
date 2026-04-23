"""tick_parser.py 單元測試。

構造最小化的 TAIFEX CSV 內容進行解析驗證，
不依賴實際 TAIFEX 下載。
"""

import io
from datetime import date, datetime

import pandas as pd
import pytest

from twquant.data.session import TAIPEI
from twquant.data.tick_parser import RAW_COLUMNS, pick_dominant_month, read_taifex_csv


def _make_csv_bytes(rows: list[dict], encoding: str = "big5") -> io.BytesIO:
    header = ",".join(RAW_COLUMNS)
    lines = [header]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in RAW_COLUMNS))
    text = "\n".join(lines)
    return io.BytesIO(text.encode(encoding))


class TestParser:
    def test_basic_row_parsing(self):
        rows = [
            {"成交日期": "20260415", "商品代號": "TX", "到期月份(週別)": "202604",
             "成交時間": "084500", "成交價格": "17500.0", "成交數量(B+S)": "2",
             "開盤集合競價": "*"},
        ]
        df = read_taifex_csv(_make_csv_bytes(rows))
        assert len(df) == 1
        row = df.iloc[0]
        # 注意：pandas Series 有 .product() 聚合方法，故用 row["product"]
        assert row["product"] == "TX"
        assert row["contract_month"] == "202604"
        assert row["price"] == 17500.0
        assert row["volume"] == 2
        assert bool(row["is_opening_auction"]) is True
        assert row["ts"] == datetime(2026, 4, 15, 8, 45, tzinfo=TAIPEI)

    def test_time_with_milliseconds(self):
        rows = [
            {"成交日期": "20260415", "商品代號": "TX", "到期月份(週別)": "202604",
             "成交時間": "084500123", "成交價格": "17500.0", "成交數量(B+S)": "2",
             "開盤集合競價": ""},
        ]
        df = read_taifex_csv(_make_csv_bytes(rows))
        assert df.iloc[0]["ts"] == datetime(2026, 4, 15, 8, 45, 0, 123_000, tzinfo=TAIPEI)

    def test_multiple_products_filtering(self):
        rows = [
            {"成交日期": "20260415", "商品代號": "TX", "到期月份(週別)": "202604",
             "成交時間": "084500", "成交價格": "17500", "成交數量(B+S)": "2",
             "開盤集合競價": ""},
            {"成交日期": "20260415", "商品代號": "MTX", "到期月份(週別)": "202604",
             "成交時間": "084500", "成交價格": "17500", "成交數量(B+S)": "5",
             "開盤集合競價": ""},
            {"成交日期": "20260415", "商品代號": "TE", "到期月份(週別)": "202604",
             "成交時間": "084500", "成交價格": "1000", "成交數量(B+S)": "1",
             "開盤集合競價": ""},
        ]
        df = read_taifex_csv(_make_csv_bytes(rows), products=("TX", "MTX"))
        assert set(df["product"]) == {"TX", "MTX"}

    def test_sorted_by_ts(self):
        rows = [
            {"成交日期": "20260415", "商品代號": "TX", "到期月份(週別)": "202604",
             "成交時間": "094500", "成交價格": "17500", "成交數量(B+S)": "1",
             "開盤集合競價": ""},
            {"成交日期": "20260415", "商品代號": "TX", "到期月份(週別)": "202604",
             "成交時間": "084500", "成交價格": "17400", "成交數量(B+S)": "1",
             "開盤集合競價": ""},
        ]
        df = read_taifex_csv(_make_csv_bytes(rows))
        assert list(df["price"]) == [17400.0, 17500.0]

    def test_whitespace_tolerant(self):
        rows = [
            {"成交日期": " 20260415 ", "商品代號": " TX ",
             "到期月份(週別)": " 202604 ", "成交時間": " 084500 ",
             "成交價格": " 17500 ", "成交數量(B+S)": " 2 ",
             "開盤集合競價": " * "},
        ]
        df = read_taifex_csv(_make_csv_bytes(rows))
        assert len(df) == 1
        assert df.iloc[0]["product"] == "TX"
        assert bool(df.iloc[0]["is_opening_auction"]) is True

    def test_missing_columns_raises(self):
        bad = io.BytesIO("成交日期,商品代號\n20260415,TX\n".encode("big5"))
        with pytest.raises(ValueError, match="missing columns"):
            read_taifex_csv(bad)


class TestDominantMonth:
    def test_pick_month_with_most_volume(self):
        df = pd.DataFrame([
            {"contract_month": "202604", "volume": 100},
            {"contract_month": "202605", "volume": 300},
            {"contract_month": "202606", "volume": 50},
        ])
        assert pick_dominant_month(df, date(2026, 4, 15)) == "202605"

    def test_empty_df_returns_none(self):
        df = pd.DataFrame(columns=["contract_month", "volume"])
        assert pick_dominant_month(df, date(2026, 4, 15)) is None
