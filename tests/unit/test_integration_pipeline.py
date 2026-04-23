"""End-to-end pipeline 整合測試：合成 TAIFEX CSV → ZIP → 解析 → 聚合 → 品檢。"""

import io
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from twquant.data.bar_aggregator import aggregate_ticks_to_bars
from twquant.data.quality import check_bars
from twquant.data.session import TAIPEI, day_session, night_session
from twquant.data.tick_parser import RAW_COLUMNS, pick_dominant_month, read_taifex_zip


def build_taifex_zip(path: Path, trade_date: date, minutes_per_bar: int = 1,
                     base_price: float = 17500.0) -> None:
    """建立與官方格式一致的合成 Daily_YYYY_MM_DD.zip。"""
    rows = []
    for sess in (day_session(trade_date), night_session(trade_date)):
        total_min = int((sess.end - sess.start).total_seconds() // 60)
        for m in range(total_min + 1):
            ts = sess.start + timedelta(minutes=m)
            trade_date_str = ts.strftime("%Y%m%d")
            time_str = ts.strftime("%H%M%S")
            # 寫兩筆：近月與次月
            for month, vol_bias in [("202606", 100), ("202607", 10)]:
                rows.append({
                    "成交日期": trade_date_str,
                    "商品代號": "TX",
                    "到期月份(週別)": month,
                    "成交時間": time_str,
                    "成交價格": f"{base_price + (m % 50)}",
                    "成交數量(B+S)": str(vol_bias),
                    "近月價格": "",
                    "遠月價格": "",
                    "開盤集合競價": "*" if m == 0 else "",
                })

    header = ",".join(RAW_COLUMNS)
    lines = [header] + [",".join(str(r.get(c, "")) for c in RAW_COLUMNS) for r in rows]
    csv_bytes = "\n".join(lines).encode("big5")

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Daily_{trade_date.year}_{trade_date.month:02d}_{trade_date.day:02d}.csv",
                    csv_bytes)


class TestEndToEnd:
    def test_full_pipeline_single_day(self, tmp_path: Path):
        trade_date = date(2026, 4, 15)
        zip_path = tmp_path / f"Daily_2026_04_15.zip"
        build_taifex_zip(zip_path, trade_date)

        ticks = read_taifex_zip(zip_path, products=("TX",))
        assert not ticks.empty
        assert set(ticks["product"]) == {"TX"}
        assert set(ticks["contract_month"]) == {"202606", "202607"}

        dom = pick_dominant_month(ticks, trade_date)
        assert dom == "202606"  # vol_bias 較大

        ticks_dom = ticks[ticks["contract_month"] == dom]

        bars_15m = aggregate_ticks_to_bars(ticks_dom, 15)
        bars_30m = aggregate_ticks_to_bars(ticks_dom, 30)

        assert len(bars_15m) == 76
        assert len(bars_30m) == 38

        rep_15 = check_bars(bars_15m, trade_date, 15)
        rep_30 = check_bars(bars_30m, trade_date, 30)

        assert rep_15.is_clean(), rep_15.summary()
        assert rep_30.is_clean(), rep_30.summary()
        assert rep_15.missing_count == 0
        assert rep_30.missing_count == 0
