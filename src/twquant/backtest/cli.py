"""回測 CLI。

範例:
    # 跑 30m 候選；資料由 data/db/bars.sqlite 的 CONT 序列載入
    python -m twquant.backtest.cli run \\
        --strategy ema_cross_30m \\
        --db data/db/bars.sqlite \\
        --start 2026-04-01 --end 2026-04-30 \\
        --output results/run_30m

    # 跑 15m 候選
    python -m twquant.backtest.cli run --strategy ema_cross_15m ...

    # 比較兩個跑出來的結果
    python -m twquant.backtest.cli compare results/run_15m results/run_30m
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, fields as dc_fields, is_dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from twquant.backtest.report import write_html_report
from twquant.backtest.runner import run_backtest
from twquant.backtest.walk_forward import run_walk_forward
from twquant.data.session import TAIPEI
from twquant.data.sqlite_store import BarStore
from twquant.execution import CostModel
from twquant.strategies.ema_crossover import EmaCrossover
from twquant.strategies.kd_ema import KdEmaStrategy

log = logging.getLogger(__name__)


STRATEGIES = {
    "ema_cross_15m": lambda: EmaCrossover(fast_period=10, slow_period=60, timeframe="15m"),
    "ema_cross_30m": lambda: EmaCrossover(fast_period=10, slow_period=60, timeframe="30m"),
    "ema_cross_1d": lambda: EmaCrossover(fast_period=10, slow_period=60, timeframe="1d"),
    # 加日線 EMA200 趨勢濾網：只在 close > EMA200 時允許黃金交叉進場
    "ema_cross_1d_t200": lambda: EmaCrossover(
        fast_period=10, slow_period=60, timeframe="1d", trend_period=200),
    "ema_cross_1d_t100": lambda: EmaCrossover(
        fast_period=10, slow_period=60, timeframe="1d", trend_period=100),
    # KD + EMA 組合（方式 1：EMA 定方向 + KD 低檔黃金交叉抓時機）
    "kd_ema_15m": lambda: KdEmaStrategy(timeframe="15m"),
    "kd_ema_30m": lambda: KdEmaStrategy(timeframe="30m"),
    "kd_ema_1d": lambda: KdEmaStrategy(timeframe="1d"),
}

COST_MODELS = {
    "TX": CostModel.for_tx,
    "MTX": CostModel.for_mtx,
}


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _date_to_taipei_dt(d: date, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TAIPEI)
    return datetime(d.year, d.month, d.day, tzinfo=TAIPEI)


def cmd_run(args: argparse.Namespace) -> int:
    if args.strategy not in STRATEGIES:
        print(f"unknown strategy: {args.strategy}; choose from {list(STRATEGIES)}",
              file=sys.stderr)
        return 2

    strategy = STRATEGIES[args.strategy]()
    cost_model = COST_MODELS[args.symbol]()

    start = _date_to_taipei_dt(args.start)
    end = _date_to_taipei_dt(args.end, end_of_day=True)

    print(f"Loading bars: {args.symbol} {strategy.timeframe} CONT {args.start}..{args.end}")
    with BarStore(args.db) as store:
        bars = store.query_bars(
            symbol=args.symbol,
            timeframe=strategy.timeframe,
            start=start,
            end=end,
            month_code="CONT",
        )
    if bars.empty:
        print("No CONT bars found; have you run `data.cli build-continuous`?",
              file=sys.stderr)
        return 1
    print(f"Loaded {len(bars):,} bars")

    result = run_backtest(bars, strategy,
                          initial_cash=args.capital,
                          cost_model=cost_model)

    _print_metrics(result.metrics, strategy.name, args.start, args.end, len(bars))

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        result.equity_curve.to_csv(out / "equity_curve.csv", index=False)
        result.trades.to_csv(out / "trades.csv", index=False)
        if not result.signals.empty:
            result.signals.to_csv(out / "signals.csv", index=False)
        if not result.fills.empty:
            result.fills.to_csv(out / "fills.csv", index=False)
        (out / "metrics.json").write_text(json.dumps(asdict(result.metrics),
                                                     indent=2, default=str))
        config = {
            "strategy": args.strategy,
            "symbol": args.symbol,
            "start": str(args.start),
            "end": str(args.end),
            "capital": args.capital,
            "timeframe": strategy.timeframe,
        }
        # 通用擷取策略宣告參數（不同策略類別參數不同）
        if is_dataclass(strategy):
            for f in dc_fields(strategy):
                config[f.name] = getattr(strategy, f.name)
        (out / "config.json").write_text(json.dumps(config, indent=2, default=str))

        if not args.no_html:
            html_path = write_html_report(
                out / "report.html",
                strategy_name=strategy.name,
                config=config,
                metrics=result.metrics,
                equity_df=result.equity_curve,
                trades_df=result.trades,
            )
            print(f"Wrote HTML report: {html_path}")
        print(f"Wrote results to {out}/")
    return 0


def _print_metrics(m, name, start, end, n_bars):
    print("=" * 60)
    print(f"{name}: {start} → {end}  ({n_bars:,} bars)")
    print("-" * 60)
    print(f"  Total return     {m.total_return_pct:>+8.2f}%")
    print(f"  CAGR             {m.cagr_pct:>+8.2f}%")
    print(f"  Sharpe (ann)     {m.sharpe:>+8.2f}")
    print(f"  Sortino (ann)    {m.sortino:>+8.2f}")
    print(f"  Calmar           {m.calmar:>+8.2f}")
    print(f"  Max drawdown     {m.max_drawdown_pct:>+8.2f}%")
    print(f"  Volatility (ann) {m.volatility_annualized_pct:>+8.2f}%")
    print("-" * 60)
    print(f"  Trades           {m.num_trades:>8d}")
    print(f"  Win rate         {m.win_rate_pct:>+8.2f}%")
    print(f"  Avg win          {m.avg_win:>+11.0f}")
    print(f"  Avg loss         {m.avg_loss:>+11.0f}")
    print(f"  Profit factor    {m.profit_factor:>+8.2f}")
    print(f"  Avg trade P&L    {m.avg_trade_pnl:>+11.0f}")
    print("=" * 60)


def cmd_walk_forward(args: argparse.Namespace) -> int:
    if args.strategy not in STRATEGIES:
        print(f"unknown strategy: {args.strategy}", file=sys.stderr)
        return 2

    strategy_factory = STRATEGIES[args.strategy]
    cost_model = COST_MODELS[args.symbol]()
    start = _date_to_taipei_dt(args.start)
    end = _date_to_taipei_dt(args.end, end_of_day=True)

    timeframe = strategy_factory().timeframe
    with BarStore(args.db) as store:
        bars = store.query_bars(args.symbol, timeframe,
                                start=start, end=end,
                                month_code="CONT")
    if bars.empty:
        print("No CONT bars in range", file=sys.stderr)
        return 1
    print(f"Loaded {len(bars):,} bars; running walk-forward "
          f"train={args.train_months}m / test={args.test_months}m")

    result = run_walk_forward(
        bars,
        strategy_factory,
        train_months=args.train_months,
        test_months=args.test_months,
        initial_cash=args.capital,
        cost_model=cost_model,
    )

    print("=" * 60)
    print(f"Walk-forward: {result.strategy_name}")
    print(f"  windows: {len(result.windows)}")
    for w, m in zip(result.windows, result.per_window_metrics):
        print(f"   [{w.test_start} → {w.test_end}]  "
              f"return={m.total_return_pct:+.2f}%  Sharpe={m.sharpe:+.2f}  "
              f"trades={m.num_trades}  win={m.win_rate_pct:.1f}%")
    print("-" * 60)
    cm = result.combined_metrics
    print(f"Combined (test-only quasi-OOS):")
    print(f"  Total return  {cm.total_return_pct:+.2f}%")
    print(f"  Sharpe        {cm.sharpe:+.2f}")
    print(f"  Calmar        {cm.calmar:+.2f}")
    print(f"  Max drawdown  {cm.max_drawdown_pct:+.2f}%")
    print(f"  Trades        {cm.num_trades}")
    print(f"  Win rate      {cm.win_rate_pct:.2f}%")
    print("=" * 60)

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        result.combined_equity.to_csv(out / "wf_equity.csv", index=False)
        result.combined_trades.to_csv(out / "wf_trades.csv", index=False)
        rows = [{
            "test_start": str(w.test_start),
            "test_end": str(w.test_end),
            **asdict(m),
        } for w, m in zip(result.windows, result.per_window_metrics)]
        pd.DataFrame(rows).to_csv(out / "wf_windows.csv", index=False)
        (out / "wf_combined_metrics.json").write_text(
            json.dumps(asdict(cm), indent=2, default=str))
        print(f"Wrote walk-forward results to {out}/")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """並列顯示多個 results 目錄的關鍵指標。"""
    rows = []
    for run_dir in args.runs:
        p = Path(run_dir)
        metrics_file = p / "metrics.json"
        config_file = p / "config.json"
        if not metrics_file.exists():
            print(f"  WARN {p}: no metrics.json", file=sys.stderr)
            continue
        m = json.loads(metrics_file.read_text())
        cfg = json.loads(config_file.read_text()) if config_file.exists() else {}
        rows.append({
            "run": p.name,
            "strategy": cfg.get("strategy"),
            "total_return%": m["total_return_pct"],
            "cagr%": m["cagr_pct"],
            "sharpe": m["sharpe"],
            "calmar": m["calmar"],
            "mdd%": m["max_drawdown_pct"],
            "trades": m["num_trades"],
            "win_rate%": m["win_rate_pct"],
        })
    if not rows:
        return 1
    df = pd.DataFrame(rows)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(df.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="twquant.backtest.cli",
                                description="Backtest runner CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="跑單次回測")
    pr.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    pr.add_argument("--db", default="data/db/bars.sqlite")
    pr.add_argument("--symbol", default="TX", choices=list(COST_MODELS.keys()))
    pr.add_argument("--start", type=_parse_date, required=True)
    pr.add_argument("--end", type=_parse_date, required=True)
    pr.add_argument("--capital", type=float, default=1_000_000)
    pr.add_argument("--output", default=None,
                    help="若指定則寫出 equity / trades / metrics 等 CSV/JSON 與 HTML 報告")
    pr.add_argument("--no-html", action="store_true",
                    help="跳過 HTML 報告產生")
    pr.set_defaults(func=cmd_run)

    pw = sub.add_parser("walk-forward", help="跑 walk-forward 分析")
    pw.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    pw.add_argument("--db", default="data/db/bars.sqlite")
    pw.add_argument("--symbol", default="TX", choices=list(COST_MODELS.keys()))
    pw.add_argument("--start", type=_parse_date, required=True)
    pw.add_argument("--end", type=_parse_date, required=True)
    pw.add_argument("--train-months", type=int, default=24)
    pw.add_argument("--test-months", type=int, default=6)
    pw.add_argument("--capital", type=float, default=1_000_000)
    pw.add_argument("--output", default=None)
    pw.set_defaults(func=cmd_walk_forward)

    pc = sub.add_parser("compare", help="並列比較多次回測的結果")
    pc.add_argument("runs", nargs="+", help="results/run_* 目錄")
    pc.set_defaults(func=cmd_compare)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
