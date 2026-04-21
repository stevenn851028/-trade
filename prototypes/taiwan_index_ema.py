"""台指期 EMA 量化指標

抓取台指期（或台灣加權指數）歷史資料，計算多條 EMA、偵測黃金／死亡交叉，
並執行簡易回測以評估策略績效。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import yfinance as yf


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity_curve: pd.Series
    total_return: float
    win_rate: float
    max_drawdown: float
    num_trades: int


def fetch_price(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        raise RuntimeError(f"無法取得 {symbol} 的資料，請確認代號或日期區間")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def add_emas(df: pd.DataFrame, periods: tuple[int, ...]) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"EMA{p}"] = out["Close"].ewm(span=p, adjust=False).mean()
    return out


def generate_signals(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = df.copy()
    fast_col, slow_col = f"EMA{fast}", f"EMA{slow}"
    spread = out[fast_col] - out[slow_col]
    prev_spread = spread.shift(1)
    out["signal"] = 0
    out.loc[(prev_spread <= 0) & (spread > 0), "signal"] = 1   # 黃金交叉
    out.loc[(prev_spread >= 0) & (spread < 0), "signal"] = -1  # 死亡交叉
    out["position"] = 0
    position = 0
    positions = []
    for s in out["signal"].tolist():
        if s == 1:
            position = 1
        elif s == -1:
            position = 0
        positions.append(position)
    out["position"] = positions
    return out


def backtest(df: pd.DataFrame, initial_capital: float = 1_000_000) -> BacktestResult:
    ret = df["Close"].pct_change().fillna(0)
    strat_ret = ret * df["position"].shift(1).fillna(0)
    equity = (1 + strat_ret).cumprod() * initial_capital

    entries = df.index[df["signal"] == 1]
    exits = df.index[df["signal"] == -1]
    trades = []
    for entry_date in entries:
        later_exits = exits[exits > entry_date]
        if len(later_exits) == 0:
            continue
        exit_date = later_exits[0]
        entry_px = float(df.loc[entry_date, "Close"])
        exit_px = float(df.loc[exit_date, "Close"])
        trades.append(
            {
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_price": entry_px,
                "exit_price": exit_px,
                "return_pct": (exit_px / entry_px - 1) * 100,
            }
        )
    trades_df = pd.DataFrame(trades)

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

    win_rate = 0.0
    if not trades_df.empty:
        win_rate = float((trades_df["return_pct"] > 0).mean())

    total_return = float(equity.iloc[-1] / initial_capital - 1) if not equity.empty else 0.0

    return BacktestResult(
        trades=trades_df,
        equity_curve=equity,
        total_return=total_return,
        win_rate=win_rate,
        max_drawdown=max_dd,
        num_trades=len(trades_df),
    )


def print_report(symbol: str, df: pd.DataFrame, result: BacktestResult, fast: int, slow: int) -> None:
    last = df.iloc[-1]
    print("=" * 60)
    print(f"標的: {symbol}")
    print(f"區間: {df.index[0].date()} ~ {df.index[-1].date()}  共 {len(df)} 根 K 棒")
    print(f"策略: EMA{fast} / EMA{slow} 交叉（黃金交叉做多、死亡交叉平倉）")
    print("-" * 60)
    print(f"最新收盤: {last['Close']:.2f}")
    print(f"EMA{fast}: {last[f'EMA{fast}']:.2f}   EMA{slow}: {last[f'EMA{slow}']:.2f}")
    trend = "多頭 (EMA快>慢)" if last[f"EMA{fast}"] > last[f"EMA{slow}"] else "空頭 (EMA快<慢)"
    print(f"當前趨勢: {trend}")
    print(f"當前部位: {'持有多單' if last['position'] == 1 else '空手'}")
    print("-" * 60)
    print(f"交易次數: {result.num_trades}")
    print(f"勝率:     {result.win_rate * 100:.2f}%")
    print(f"總報酬:   {result.total_return * 100:.2f}%")
    print(f"最大回撤: {result.max_drawdown * 100:.2f}%")
    print("=" * 60)
    if not result.trades.empty:
        print("最近 5 筆交易:")
        print(result.trades.tail(5).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="台指期 EMA 量化指標")
    parser.add_argument("--symbol", default="^TWII",
                        help="Yahoo Finance 代號，預設 ^TWII（台灣加權指數）。台指期可用 TX=F")
    parser.add_argument("--start", default="2020-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"), help="結束日期 YYYY-MM-DD")
    parser.add_argument("--interval", default="1d", help="K 棒週期，如 1d, 1h, 15m")
    parser.add_argument("--fast", type=int, default=12, help="快線 EMA 週期")
    parser.add_argument("--slow", type=int, default=26, help="慢線 EMA 週期")
    parser.add_argument("--long", type=int, default=60, help="長期 EMA 週期（僅顯示用）")
    parser.add_argument("--capital", type=float, default=1_000_000, help="回測起始資金")
    parser.add_argument("--output", default=None, help="若指定，將帶有指標的資料輸出為 CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    price = fetch_price(args.symbol, args.start, args.end, args.interval)
    periods = tuple(sorted({args.fast, args.slow, args.long}))
    with_ema = add_emas(price, periods)
    with_signal = generate_signals(with_ema, args.fast, args.slow)
    result = backtest(with_signal, args.capital)
    print_report(args.symbol, with_signal, result, args.fast, args.slow)
    if args.output:
        with_signal.to_csv(args.output, encoding="utf-8-sig")
        print(f"\n已輸出指標資料至 {args.output}")


if __name__ == "__main__":
    main()
