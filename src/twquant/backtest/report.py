"""產生單檔自含的 HTML 績效報告。

設計：所有圖表用 matplotlib 畫成 PNG，base64 嵌入 HTML，
無外部依賴、無 JavaScript、可直接寄出或開啟。
"""

from __future__ import annotations

import base64
import io
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from twquant.backtest.metrics import PerformanceMetrics


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _equity_chart(equity_df: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(11, 4))
    if not equity_df.empty:
        ax.plot(equity_df["ts"], equity_df["equity"], color="#1f77b4", linewidth=1)
    ax.set_title("Equity curve")
    ax.set_ylabel("Equity (TWD)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    return _fig_to_base64(fig)


def _drawdown_chart(equity_df: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(11, 3))
    if not equity_df.empty:
        eq = equity_df["equity"].values
        peak = pd.Series(eq).cummax().values
        dd = (eq - peak) / peak * 100
        ax.fill_between(equity_df["ts"], dd, 0, color="#d62728", alpha=0.4)
        ax.plot(equity_df["ts"], dd, color="#d62728", linewidth=0.8)
    ax.set_title("Drawdown (%)")
    ax.set_ylabel("DD %")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    return _fig_to_base64(fig)


def _monthly_returns_heatmap(equity_df: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(8, 3))
    if not equity_df.empty and len(equity_df) > 1:
        df = equity_df.copy()
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts").sort_index()
        monthly = df["equity"].resample("ME").last().pct_change().dropna() * 100
        if not monthly.empty:
            pivot = monthly.to_frame("ret")
            pivot["year"] = pivot.index.year
            pivot["month"] = pivot.index.month
            mat = pivot.pivot(index="year", columns="month", values="ret")
            im = ax.imshow(mat.values, cmap="RdYlGn", aspect="auto",
                           vmin=-10, vmax=10)
            ax.set_xticks(range(mat.shape[1]))
            ax.set_xticklabels([str(m) for m in mat.columns])
            ax.set_yticks(range(mat.shape[0]))
            ax.set_yticklabels([str(y) for y in mat.index])
            for i in range(mat.shape[0]):
                for j in range(mat.shape[1]):
                    v = mat.values[i, j]
                    if pd.notna(v):
                        ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                                fontsize=8, color="black")
            fig.colorbar(im, ax=ax, label="%")
    ax.set_title("Monthly returns (%)")
    return _fig_to_base64(fig)


def _trade_pnl_hist(trades_df: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(8, 3))
    if not trades_df.empty:
        ax.hist(trades_df["net_pnl"], bins=30, color="#2ca02c", edgecolor="black")
        ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title("Trade P&L distribution (NT$)")
    ax.set_xlabel("Net P&L")
    ax.grid(True, alpha=0.3)
    return _fig_to_base64(fig)


def _metrics_table(m: PerformanceMetrics) -> str:
    rows = [
        ("Total return", f"{m.total_return_pct:+.2f}%"),
        ("CAGR",         f"{m.cagr_pct:+.2f}%"),
        ("Sharpe (ann)", f"{m.sharpe:+.2f}"),
        ("Sortino (ann)", f"{m.sortino:+.2f}"),
        ("Calmar",       f"{m.calmar:+.2f}"),
        ("Max drawdown", f"{m.max_drawdown_pct:+.2f}%"),
        ("Volatility",   f"{m.volatility_annualized_pct:+.2f}%"),
        ("Trades",       f"{m.num_trades:,}"),
        ("Win rate",     f"{m.win_rate_pct:+.2f}%"),
        ("Avg win",      f"{m.avg_win:+,.0f}"),
        ("Avg loss",     f"{m.avg_loss:+,.0f}"),
        ("Profit factor", f"{m.profit_factor:+.2f}"),
        ("Avg trade",    f"{m.avg_trade_pnl:+,.0f}"),
    ]
    cells = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows
    )
    return f"<table class='metrics'><tbody>{cells}</tbody></table>"


def write_html_report(
    out_path: str | Path,
    *,
    strategy_name: str,
    config: dict,
    metrics: PerformanceMetrics,
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    eq_img = _equity_chart(equity_df)
    dd_img = _drawdown_chart(equity_df)
    heat_img = _monthly_returns_heatmap(equity_df)
    hist_img = _trade_pnl_hist(trades_df)
    metrics_html = _metrics_table(metrics)

    config_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in config.items()
    )
    trades_table = ""
    if not trades_df.empty:
        last = trades_df.tail(20).copy()
        for col in ("entry_ts", "exit_ts"):
            if col in last.columns:
                last[col] = last[col].astype(str)
        trades_table = last.to_html(index=False, classes="trades", float_format="%.2f")

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<title>{strategy_name} — Backtest Report</title>
<style>
  body {{ font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
         max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #222; }}
  h1 {{ border-bottom: 2px solid #1f77b4; padding-bottom: 6px; }}
  h2 {{ color: #1f77b4; margin-top: 28px; }}
  table {{ border-collapse: collapse; margin: 8px 0; }}
  table.metrics td {{ padding: 4px 12px; border-bottom: 1px solid #eee; }}
  table.metrics td:first-child {{ color: #666; }}
  table.metrics td:last-child {{ text-align: right; font-weight: 600; }}
  table.trades {{ font-size: 12px; border: 1px solid #ddd; }}
  table.trades th, table.trades td {{ padding: 4px 8px; border: 1px solid #eee; }}
  img {{ display: block; max-width: 100%; margin: 8px 0; }}
  .config td {{ padding: 2px 12px; }}
  .config td:first-child {{ color: #666; }}
</style>
</head><body>
<h1>{strategy_name}</h1>

<h2>Configuration</h2>
<table class="config"><tbody>{config_rows}</tbody></table>

<h2>Performance metrics</h2>
{metrics_html}

<h2>Equity curve</h2>
<img src="data:image/png;base64,{eq_img}"/>

<h2>Drawdown</h2>
<img src="data:image/png;base64,{dd_img}"/>

<h2>Monthly returns</h2>
<img src="data:image/png;base64,{heat_img}"/>

<h2>Trade P&amp;L distribution</h2>
<img src="data:image/png;base64,{hist_img}"/>

<h2>Last 20 trades</h2>
{trades_table or '<p>(no trades)</p>'}

<p style="color:#aaa;font-size:11px;margin-top:32px;">
Generated by twquant backtest report writer
</p>
</body></html>
"""
    out_path.write_text(html, encoding="utf-8")
    return out_path
