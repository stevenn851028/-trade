#!/usr/bin/env python3
"""Walk-forward 結果視覺化。

畫出：
  上圖 — 收盤價 + EMA 快慢線 + 進出場標記（綠三角=進場、紅三角=ATR停利、橘三角=死亡交叉）
  下圖 — 複利鏈接後的權益曲線

使用方式：
    python scripts/plot_wf.py \\
        --results results/wf_stress_20_120_atr7_1p5 \\
        --db data/db/bars.sqlite \\
        --symbol TX --timeframe 30m \\
        --fast 20 --slow 120 \\
        --output results/wf_stress_20_120_atr7_1p5/chart.png
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np

# ── 把 src 加進 path（直接執行時用）──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from twquant.data.session import TAIPEI
from twquant.data.sqlite_store import BarStore


def load_bars(db: str, symbol: str, timeframe: str,
              start: str, end: str) -> pd.DataFrame:
    s = datetime.fromisoformat(start).replace(tzinfo=TAIPEI)
    e = datetime.fromisoformat(end).replace(tzinfo=TAIPEI)
    with BarStore(db) as store:
        bars = store.query_bars(symbol, timeframe, start=s, end=e,
                                month_code="CONT")
    return bars


def add_ema(bars: pd.DataFrame, period: int, col: str) -> pd.DataFrame:
    bars = bars.copy()
    bars[col] = bars["close"].ewm(span=period, adjust=False).mean()
    return bars


def plot_wf(results_dir: str, db: str, symbol: str, timeframe: str,
            fast: int, slow: int, output: str | None) -> None:
    res = Path(results_dir)

    # ── 讀取結果檔 ────────────────────────────────────────────────────────
    trades_path = res / "wf_trades.csv"
    equity_path = res / "wf_equity.csv"
    windows_path = res / "wf_windows.csv"

    if not trades_path.exists():
        print(f"找不到 {trades_path}，請確認 results 目錄正確。")
        sys.exit(1)

    trades = pd.read_csv(trades_path, parse_dates=["entry_ts", "exit_ts"])
    equity = pd.read_csv(equity_path, parse_dates=["ts"])

    # ── 確定資料時間範圍 ──────────────────────────────────────────────────
    if trades.empty:
        print("trades 是空的，沒有交易可畫。")
        sys.exit(1)

    start_str = equity["ts"].min().strftime("%Y-%m-%d")
    end_str   = equity["ts"].max().strftime("%Y-%m-%d")

    # ── 載入 K 棒並計算 EMA ───────────────────────────────────────────────
    print(f"載入 {symbol} {timeframe} bars {start_str} ~ {end_str} ...")
    bars = load_bars(db, symbol, timeframe, start_str, end_str)
    if bars.empty:
        print("查無 bar 資料，請確認 --db / --symbol / --timeframe 參數。")
        sys.exit(1)

    bars = add_ema(bars, fast, "ema_fast")
    bars = add_ema(bars, slow, "ema_slow")
    bars["ts"] = pd.to_datetime(bars["ts"])

    # ── 讓 trades 的時間帶時區（與 bars 對齊）────────────────────────────
    def localize(col: pd.Series) -> pd.Series:
        if col.dt.tz is None:
            return col.dt.tz_localize(TAIPEI)
        return col.dt.tz_convert(TAIPEI)

    trades["entry_ts"] = localize(trades["entry_ts"])
    trades["exit_ts"]  = localize(trades["exit_ts"])

    # ── 畫圖 ──────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(18, 9),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=False,
    )
    fig.patch.set_facecolor("#0f0f0f")
    for ax in (ax1, ax2):
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="#cccccc", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#444444")

    ts = bars["ts"]

    # 收盤價
    ax1.plot(ts, bars["close"], color="#8888aa", linewidth=0.6,
             label="Close", zorder=1)

    # EMA 線
    ax1.plot(ts, bars["ema_fast"], color="#4fc3f7", linewidth=1.0,
             label=f"EMA{fast}", zorder=2)
    ax1.plot(ts, bars["ema_slow"], color="#ffb74d", linewidth=1.2,
             label=f"EMA{slow}", zorder=2)

    # 進出場標記
    entry_count = exit_atr = exit_dc = 0
    for _, t in trades.iterrows():
        ep = t["entry_price"]
        xp = t["exit_price"]
        ets = t["entry_ts"]
        xts = t["exit_ts"]
        reason = str(t.get("exit_reason", ""))

        # 持倉底色
        ax1.axvspan(ets, xts, alpha=0.08,
                    color="#4caf50" if t["net_pnl"] >= 0 else "#f44336",
                    zorder=0)

        # 進場 ▲
        ax1.scatter(ets, ep, marker="^", color="#00e676", s=80,
                    zorder=5, label="進場" if entry_count == 0 else "")
        ax1.annotate(f" {ep:.0f}", (ets, ep),
                     fontsize=6, color="#00e676", va="bottom")
        entry_count += 1

        # 出場 ▼（顏色依原因）
        if "atr" in reason:
            color, label = "#ff9800", "ATR停利" if exit_atr == 0 else ""
            exit_atr += 1
        else:
            color, label = "#f44336", "死亡交叉" if exit_dc == 0 else ""
            exit_dc += 1

        ax1.scatter(xts, xp, marker="v", color=color, s=80,
                    zorder=5, label=label)
        pnl_sign = "+" if t["net_pnl"] >= 0 else ""
        ax1.annotate(f" {xp:.0f}\n{pnl_sign}{t['net_pnl']:.0f}",
                     (xts, xp), fontsize=6, color=color, va="top")

    # OOS 窗口邊界
    if windows_path.exists():
        windows = pd.read_csv(windows_path, parse_dates=["test_start", "test_end"])
        for _, w in windows.iterrows():
            ts_start = pd.Timestamp(w["test_start"]).tz_localize(TAIPEI) \
                if pd.Timestamp(w["test_start"]).tzinfo is None \
                else pd.Timestamp(w["test_start"]).tz_convert(TAIPEI)
            ax1.axvline(ts_start, color="#555577", linewidth=0.5,
                        linestyle="--", alpha=0.6)

    ax1.set_title(
        f"{symbol} {timeframe}  EMA{fast}/{slow}  "
        f"進場:{entry_count}  ATR停利:{exit_atr}  死亡交叉:{exit_dc}",
        color="#eeeeee", fontsize=11, pad=8,
    )
    ax1.legend(loc="upper left", fontsize=8, facecolor="#222233",
               labelcolor="#cccccc", framealpha=0.8)
    ax1.set_ylabel("點數", color="#cccccc", fontsize=9)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # 權益曲線
    eq_ts = equity["ts"]
    ax2.plot(eq_ts, equity["equity"] / 1e4, color="#4fc3f7", linewidth=1.2)
    ax2.fill_between(eq_ts, equity["equity"] / 1e4,
                     equity["equity"].iloc[0] / 1e4,
                     where=equity["equity"] >= equity["equity"].iloc[0],
                     alpha=0.15, color="#4caf50")
    ax2.fill_between(eq_ts, equity["equity"] / 1e4,
                     equity["equity"].iloc[0] / 1e4,
                     where=equity["equity"] < equity["equity"].iloc[0],
                     alpha=0.15, color="#f44336")
    ax2.set_ylabel("萬元", color="#cccccc", fontsize=9)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right")

    final_eq = equity["equity"].iloc[-1]
    init_eq  = equity["equity"].iloc[0]
    total_ret = (final_eq / init_eq - 1) * 100
    ax2.set_title(
        f"權益曲線  初始:{init_eq/1e4:.0f}萬 → 最終:{final_eq/1e4:.1f}萬  "
        f"({total_ret:+.1f}%)",
        color="#eeeeee", fontsize=9, pad=4,
    )

    plt.tight_layout(h_pad=0.5)

    # ── 輸出 ──────────────────────────────────────────────────────────────
    out_path = output or str(res / "chart.png")
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"圖表已儲存：{out_path}")
    plt.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward 結果視覺化")
    ap.add_argument("--results", required=True, help="walk-forward 輸出目錄")
    ap.add_argument("--db", default="data/db/bars.sqlite")
    ap.add_argument("--symbol", default="TX")
    ap.add_argument("--timeframe", default="30m")
    ap.add_argument("--fast", type=int, default=20)
    ap.add_argument("--slow", type=int, default=120)
    ap.add_argument("--output", default=None, help="輸出圖片路徑（預設：results/chart.png）")
    args = ap.parse_args()

    plot_wf(
        results_dir=args.results,
        db=args.db,
        symbol=args.symbol,
        timeframe=args.timeframe,
        fast=args.fast,
        slow=args.slow,
        output=args.output,
    )


if __name__ == "__main__":
    main()
