"""TAIFEX 資料管線 CLI。

使用範例:
    # 每日排程：補齊最近 30 天遺漏的 Daily ZIP（推薦給 cron / systemd timer）
    python -m twquant.data.cli archive --out-dir data/raw

    # 手動下載指定區間
    python -m twquant.data.cli fetch --start 2026-04-01 --end 2026-04-15

    # 單日驗證：解析 → 聚合 15m/30m → 品質檢查報告
    python -m twquant.data.cli verify --date 2026-04-15 --raw-dir data/raw

    # 載入 raw ZIP 到 SQLite DB（解析 → 聚合 → upsert，預設 15m + 30m）
    python -m twquant.data.cli load --start 2026-04-01 --end 2026-04-15 \\
        --raw-dir data/raw --db data/db/bars.sqlite

    # 查詢 DB 統計
    python -m twquant.data.cli stats --db data/db/bars.sqlite
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from twquant.data.archive import run_archive
from twquant.data.bar_aggregator import aggregate_ticks_to_bars
from twquant.data.quality import check_bars
from twquant.data.sqlite_store import BarStore
from twquant.data.taifex_downloader import download_date_range
from twquant.data.tick_parser import pick_dominant_month, read_taifex_zip

log = logging.getLogger("twquant.data.cli")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def cmd_archive(args: argparse.Namespace) -> int:
    """每日排程執行：補齊最近 N 天所有遺漏的 Daily ZIP。"""
    summary = run_archive(
        out_dir=args.out_dir,
        lookback_days=args.lookback,
        timeout=args.timeout,
    )
    print(summary.summary())
    for r in summary.results:
        if r.status == "error":
            print(f"  ERROR {r.trade_date}: {r.error}", file=sys.stderr)
    return 1 if summary.errors else 0


def cmd_fetch(args: argparse.Namespace) -> int:
    results = download_date_range(
        start=args.start,
        end=args.end,
        out_dir=args.out_dir,
        timeout=args.timeout,
    )
    ok = sum(1 for r in results if r.status in ("ok", "cached"))
    nf = sum(1 for r in results if r.status == "not_found")
    err = sum(1 for r in results if r.status == "error")
    total_bytes = sum(r.bytes_downloaded for r in results)
    print(f"Fetched: ok/cached={ok}, not_found={nf} (holidays), error={err}; "
          f"bytes={total_bytes:,}")
    for r in results:
        if r.status == "error":
            print(f"  ERROR {r.trade_date}: {r.error}", file=sys.stderr)
    return 0 if err == 0 else 1


def _verify_one_day(
    trade_date: date,
    raw_dir: Path,
    products: tuple[str, ...],
    timeframes: tuple[int, ...],
    out_dir: Path | None,
    encoding: str,
) -> int:
    zip_name = f"Daily_{trade_date.year}_{trade_date.month:02d}_{trade_date.day:02d}.zip"
    zip_path = raw_dir / zip_name
    if not zip_path.exists():
        print(f"  MISS {trade_date}: {zip_path} not found (run `fetch` first, or holiday)")
        return 0

    try:
        ticks = read_taifex_zip(zip_path, products=products, encoding=encoding)
    except Exception as e:  # noqa: BLE001 — we want to surface parser issues loudly
        print(f"  PARSE FAIL {trade_date}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if ticks.empty:
        print(f"  EMPTY {trade_date}: no ticks for products {products}")
        return 0

    dom = pick_dominant_month(ticks, trade_date)
    ticks_dom = ticks[ticks["contract_month"] == dom]
    print(f"  {trade_date}: {len(ticks):,} ticks total, "
          f"dominant month={dom} ({len(ticks_dom):,} ticks)")

    for tf in timeframes:
        bars = aggregate_ticks_to_bars(ticks_dom, bar_size_min=tf)
        report = check_bars(bars, trade_date=trade_date, bar_size_min=tf)
        marker = "OK " if report.is_clean() else "WARN"
        print(f"    [{marker}] {report.summary()}")
        if report.missing_bar_ends and len(report.missing_bar_ends) <= 5:
            for miss in report.missing_bar_ends:
                print(f"          missing bar end: {miss}")

        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"bars_{tf}m_{trade_date.isoformat()}.csv"
            bars.to_csv(out_file, index=False)

    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir) if args.out_dir else None
    products = tuple(args.products.split(","))
    timeframes = tuple(int(x) for x in args.timeframes.split(","))

    dates: list[date] = []
    if args.date:
        dates = [args.date]
    else:
        d = args.start
        while d <= args.end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)

    print(f"Verifying {len(dates)} trading day(s) from {raw_dir}")
    failures = 0
    for d in dates:
        failures += _verify_one_day(d, raw_dir, products, timeframes, out_dir,
                                    encoding=args.encoding)
    print(f"Done. Failures: {failures}")
    return 1 if failures else 0


def cmd_load(args: argparse.Namespace) -> int:
    """解析 raw ZIP → 聚合 → upsert 到 SQLite DB。"""
    raw_dir = Path(args.raw_dir)
    products = tuple(args.products.split(","))
    timeframes = tuple(int(x) for x in args.timeframes.split(","))

    dates: list[date] = []
    if args.date:
        dates = [args.date]
    else:
        d = args.start
        while d <= args.end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)

    print(f"Loading {len(dates)} trading day(s) into {args.db}")
    total_bars = 0
    misses = 0
    with BarStore(args.db) as store:
        for trade_date in dates:
            zip_name = f"Daily_{trade_date.year}_{trade_date.month:02d}_{trade_date.day:02d}.zip"
            zip_path = raw_dir / zip_name
            if not zip_path.exists():
                print(f"  MISS {trade_date}: {zip_path} not found")
                misses += 1
                continue

            try:
                ticks = read_taifex_zip(zip_path, products=products, encoding=args.encoding)
            except Exception as e:  # noqa: BLE001
                print(f"  PARSE FAIL {trade_date}: {type(e).__name__}: {e}", file=sys.stderr)
                continue

            if ticks.empty:
                print(f"  EMPTY {trade_date}")
                continue

            dom = pick_dominant_month(ticks, trade_date)
            ticks_dom = ticks[ticks["contract_month"] == dom]

            day_bars = 0
            for tf in timeframes:
                bars = aggregate_ticks_to_bars(ticks_dom, bar_size_min=tf)
                day_bars += store.upsert_bars(bars)
            total_bars += day_bars
            print(f"  OK   {trade_date}: dominant={dom}, upserted={day_bars}")

    print(f"Done. Total upserted bars: {total_bars}, missing days: {misses}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """列出 DB 內 (symbol, tf) 的筆數與時間範圍。"""
    with BarStore(args.db) as store:
        stats = store.stats()
    if not stats:
        print(f"{args.db}: empty (no bars)")
        return 0
    print(f"{args.db}:")
    for (sym, tf), info in stats.items():
        print(f"  {sym} {tf}: {info['count']:>8,} bars  "
              f"[{info['min_ts']} → {info['max_ts']}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="twquant.data.cli",
                                description="TAIFEX data pipeline CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("archive",
                        help="每日排程用：補齊最近 N 天所有遺漏的 Daily ZIP")
    pa.add_argument("--out-dir", default="data/raw")
    pa.add_argument("--lookback", type=int, default=30,
                    help="回溯天數（預設 30 = TAIFEX 公開窗口）")
    pa.add_argument("--timeout", type=int, default=30)
    pa.set_defaults(func=cmd_archive)

    pf = sub.add_parser("fetch", help="下載 TAIFEX Daily ZIP（手動指定區間）")
    pf.add_argument("--start", type=_parse_date, required=True)
    pf.add_argument("--end", type=_parse_date, required=True)
    pf.add_argument("--out-dir", default="data/raw")
    pf.add_argument("--timeout", type=int, default=30)
    pf.set_defaults(func=cmd_fetch)

    pv = sub.add_parser("verify", help="解析 → 聚合 → 品質檢查")
    pv.add_argument("--date", type=_parse_date, default=None,
                    help="單日驗證（YYYY-MM-DD）")
    pv.add_argument("--start", type=_parse_date, default=None)
    pv.add_argument("--end", type=_parse_date, default=None)
    pv.add_argument("--raw-dir", default="data/raw")
    pv.add_argument("--out-dir", default=None,
                    help="若指定則輸出聚合後 bars CSV")
    pv.add_argument("--products", default="TX",
                    help="逗號分隔商品代號（例 TX,MTX）")
    pv.add_argument("--timeframes", default="15,30",
                    help="逗號分隔 K 棒分鐘數")
    pv.add_argument("--encoding", default="big5",
                    help="TAIFEX CSV 編碼，預設 big5；若為新檔可能是 utf-8")
    pv.set_defaults(func=cmd_verify)

    pl = sub.add_parser("load", help="解析 raw ZIP → 聚合 → upsert 到 SQLite")
    pl.add_argument("--date", type=_parse_date, default=None)
    pl.add_argument("--start", type=_parse_date, default=None)
    pl.add_argument("--end", type=_parse_date, default=None)
    pl.add_argument("--raw-dir", default="data/raw")
    pl.add_argument("--db", default="data/db/bars.sqlite")
    pl.add_argument("--products", default="TX")
    pl.add_argument("--timeframes", default="15,30")
    pl.add_argument("--encoding", default="big5")
    pl.set_defaults(func=cmd_load)

    ps = sub.add_parser("stats", help="列出 DB 內 (symbol, tf) 的筆數與時間範圍")
    ps.add_argument("--db", default="data/db/bars.sqlite")
    ps.set_defaults(func=cmd_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.cmd in ("verify", "load") and not args.date \
            and (args.start is None or args.end is None):
        print(f"{args.cmd}: either --date or --start/--end is required", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
