#!/usr/bin/env python3
"""Walk-forward 結果互動視覺化（可縮放 HTML）。

使用方式：
    python scripts/plot_wf.py \\
        --results results/wf_stress_20_120_atr7_1p5 \\
        --db data/db/bars.sqlite \\
        --symbol TX --timeframe 30m \\
        --fast 20 --slow 120

輸出：results/.../chart.html（瀏覽器開啟，可縮放 / 拖曳）
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# ── 把 src 加進 path（直接執行時用）──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("請先安裝 plotly：pip install plotly")
    sys.exit(1)

from twquant.data.session import TAIPEI
from twquant.data.sqlite_store import BarStore


# ─────────────────────────────────────────────────────────────────────────────

def load_bars(db: str, symbol: str, timeframe: str,
              start: str, end: str) -> pd.DataFrame:
    s = datetime.fromisoformat(start).replace(tzinfo=TAIPEI)
    e = datetime.fromisoformat(end).replace(tzinfo=TAIPEI)
    with BarStore(db) as store:
        bars = store.query_bars(symbol, timeframe, start=s, end=e,
                                month_code="CONT")
    return bars


def add_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def localize_col(col: pd.Series) -> pd.Series:
    if col.dt.tz is None:
        return col.dt.tz_localize(TAIPEI)
    return col.dt.tz_convert(TAIPEI)


# ─────────────────────────────────────────────────────────────────────────────

def plot_wf(results_dir: str, db: str, symbol: str, timeframe: str,
            fast: int, slow: int, output: str | None) -> None:

    res = Path(results_dir)
    trades_path  = res / "wf_trades.csv"
    equity_path  = res / "wf_equity.csv"
    windows_path = res / "wf_windows.csv"

    if not trades_path.exists():
        print(f"找不到 {trades_path}")
        sys.exit(1)

    trades = pd.read_csv(trades_path, parse_dates=["entry_ts", "exit_ts"])
    equity = pd.read_csv(equity_path, parse_dates=["ts"])

    if trades.empty:
        print("沒有交易紀錄可畫。")
        sys.exit(1)

    # ── 載入 K 棒 ────────────────────────────────────────────────────────
    start_str = equity["ts"].min().strftime("%Y-%m-%d")
    end_str   = equity["ts"].max().strftime("%Y-%m-%d")
    print(f"載入 {symbol} {timeframe} bars {start_str} ~ {end_str} ...")
    bars = load_bars(db, symbol, timeframe, start_str, end_str)
    if bars.empty:
        print("查無 bar 資料。")
        sys.exit(1)

    bars["ts"]       = pd.to_datetime(bars["ts"])
    bars["ema_fast"] = add_ema(bars["close"], fast)
    bars["ema_slow"] = add_ema(bars["close"], slow)

    trades["entry_ts"] = localize_col(trades["entry_ts"])
    trades["exit_ts"]  = localize_col(trades["exit_ts"])

    # ── 圖表配置 ──────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.04,
        subplot_titles=["", ""],
    )

    BG      = "#0f1117"
    PLOT_BG = "#1a1a2e"
    GRID    = "#2a2a3e"
    TEXT    = "#cccccc"

    # ── 收盤價 ────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=bars["ts"], y=bars["close"],
        mode="lines", line=dict(color="#555577", width=0.8),
        name="收盤價", hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    # ── EMA ───────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=bars["ts"], y=bars["ema_fast"],
        mode="lines", line=dict(color="#4fc3f7", width=1.2),
        name=f"EMA{fast}",
        hovertemplate=f"EMA{fast}: %{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=bars["ts"], y=bars["ema_slow"],
        mode="lines", line=dict(color="#ffb74d", width=1.5),
        name=f"EMA{slow}",
        hovertemplate=f"EMA{slow}: %{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)

    # ── 持倉底色 + 進出場標記 ─────────────────────────────────────────────
    entry_xs, entry_ys, entry_texts = [], [], []
    atr_xs,   atr_ys,   atr_texts   = [], [], []
    dc_xs,    dc_ys,    dc_texts     = [], [], []

    for _, t in trades.iterrows():
        pnl    = t["net_pnl"]
        color  = "rgba(76,175,80,0.07)" if pnl >= 0 else "rgba(244,67,54,0.07)"
        reason = str(t.get("exit_reason", ""))
        pnl_str = f"{pnl:+,.0f}"

        # 持倉底色
        fig.add_vrect(
            x0=t["entry_ts"], x1=t["exit_ts"],
            fillcolor=color, line_width=0,
            layer="below", row=1, col=1,
        )

        # 進場
        entry_xs.append(t["entry_ts"])
        entry_ys.append(t["entry_price"])
        entry_texts.append(
            f"進場 {t['entry_ts'].strftime('%m/%d %H:%M')}<br>"
            f"價格：{t['entry_price']:,.0f}<br>"
            f"原因：{t.get('entry_reason','')}"
        )

        # 出場
        txt = (
            f"出場 {t['exit_ts'].strftime('%m/%d %H:%M')}<br>"
            f"價格：{t['exit_price']:,.0f}<br>"
            f"損益：{pnl_str}<br>"
            f"原因：{reason}"
        )
        if "atr" in reason:
            atr_xs.append(t["exit_ts"])
            atr_ys.append(t["exit_price"])
            atr_texts.append(txt)
        else:
            dc_xs.append(t["exit_ts"])
            dc_ys.append(t["exit_price"])
            dc_texts.append(txt)

    # 進場標記（綠三角上）
    fig.add_trace(go.Scatter(
        x=entry_xs, y=entry_ys,
        mode="markers",
        marker=dict(symbol="triangle-up", color="#00e676", size=10,
                    line=dict(color="#004d20", width=1)),
        name="進場", text=entry_texts, hoverinfo="text",
    ), row=1, col=1)

    # ATR 停利出場（橘三角下）
    if atr_xs:
        fig.add_trace(go.Scatter(
            x=atr_xs, y=atr_ys,
            mode="markers",
            marker=dict(symbol="triangle-down", color="#ff9800", size=10,
                        line=dict(color="#7f4000", width=1)),
            name="ATR停利", text=atr_texts, hoverinfo="text",
        ), row=1, col=1)

    # 死亡交叉出場（紅三角下）
    if dc_xs:
        fig.add_trace(go.Scatter(
            x=dc_xs, y=dc_ys,
            mode="markers",
            marker=dict(symbol="triangle-down", color="#f44336", size=10,
                        line=dict(color="#7f0000", width=1)),
            name="死亡交叉", text=dc_texts, hoverinfo="text",
        ), row=1, col=1)

    # ── OOS 窗口分隔線 ────────────────────────────────────────────────────
    if windows_path.exists():
        windows = pd.read_csv(windows_path, parse_dates=["test_start"])
        for _, w in windows.iterrows():
            ts_start = pd.Timestamp(w["test_start"])
            if ts_start.tzinfo is None:
                ts_start = ts_start.tz_localize(TAIPEI)
            fig.add_vline(
                x=ts_start.timestamp() * 1000,
                line=dict(color="#445566", width=1, dash="dot"),
                row=1, col=1,
            )

    # ── 權益曲線 ──────────────────────────────────────────────────────────
    init_eq  = equity["equity"].iloc[0]
    final_eq = equity["equity"].iloc[-1]
    total_ret = (final_eq / init_eq - 1) * 100

    fig.add_trace(go.Scatter(
        x=equity["ts"], y=equity["equity"] / 1e4,
        mode="lines", line=dict(color="#4fc3f7", width=1.5),
        fill="tozeroy", fillcolor="rgba(79,195,247,0.08)",
        name="權益（萬）",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f} 萬<extra></extra>",
    ), row=2, col=1)

    # ── Layout ───────────────────────────────────────────────────────────
    n_entry = len(entry_xs)
    n_atr   = len(atr_xs)
    n_dc    = len(dc_xs)

    title = (
        f"{symbol} {timeframe} | EMA{fast}/{slow} | "
        f"進場:{n_entry}  ATR停利:{n_atr}  死亡交叉:{n_dc} | "
        f"總報酬:{total_ret:+.1f}%  初始:{init_eq/1e4:.0f}萬→最終:{final_eq/1e4:.1f}萬"
    )

    fig.update_layout(
        title=dict(text=title, font=dict(color=TEXT, size=13), x=0.01),
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT, size=11),
        hovermode="x unified",
        legend=dict(
            bgcolor="#1e1e2e", bordercolor="#444",
            borderwidth=1, font=dict(size=10),
            x=0.01, y=0.98,
        ),
        margin=dict(l=60, r=20, t=60, b=40),
        xaxis=dict(gridcolor=GRID, showgrid=True, rangeslider=dict(visible=False)),
        xaxis2=dict(gridcolor=GRID, showgrid=True),
        yaxis=dict(gridcolor=GRID, showgrid=True, tickformat=",.0f",
                   title="點數", title_font=dict(size=10)),
        yaxis2=dict(gridcolor=GRID, showgrid=True, tickformat=".1f",
                    title="萬元", title_font=dict(size=10)),
        height=800,
    )

    # ── 輸出 ──────────────────────────────────────────────────────────────
    out_path = output or str(res / "chart.html")
    fig.write_html(out_path, include_plotlyjs="cdn")
    print(f"互動圖表已儲存：{out_path}")
    print("用瀏覽器開啟後可縮放 / 拖曳 / hover 看詳情。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward 互動視覺化")
    ap.add_argument("--results",   required=True, help="walk-forward 輸出目錄")
    ap.add_argument("--db",        default="data/db/bars.sqlite")
    ap.add_argument("--symbol",    default="TX")
    ap.add_argument("--timeframe", default="30m")
    ap.add_argument("--fast",      type=int, default=20)
    ap.add_argument("--slow",      type=int, default=120)
    ap.add_argument("--output",    default=None, help="輸出路徑（預設 results/chart.html）")
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
