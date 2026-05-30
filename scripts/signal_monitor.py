#!/usr/bin/env python3
"""台指期 EMA20/120 即時訊號監控器。

從 Shioaji 拉取近期 1m K 棒，聚合為 30m，以 EmaCrossover 策略判斷訊號，
並透過 Telegram Bot 推播。由 GitHub Actions 每 30 分鐘排程執行。

必要環境變數：
    SHIOAJI_API_KEY      永豐 API Key
    SHIOAJI_SECRET_KEY   永豐 Secret Key
    TELEGRAM_BOT_TOKEN   Telegram Bot Token
    TELEGRAM_CHAT_ID     接收通知的 Chat ID（個人或群組）

選用環境變數（有預設值）：
    FAST_PERIOD   快線週期，預設 20
    SLOW_PERIOD   慢線週期，預設 120
    ATR_PERIOD    ATR 週期，預設 7
    ATR_MULT      ATR 乘數，預設 1.5
    FETCH_DAYS    拉取幾天的 1m 歷史資料，預設 40（足夠 EMA120 暖機）
    STATE_FILE    狀態檔路徑，預設 signal_state.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
import requests

from twquant.data.bar_aggregator import aggregate_bars_to_higher
from twquant.data.session import TAIPEI
from twquant.data.shioaji_loader import ShioajiClient
from twquant.events import BarEvent, Direction
from twquant.strategies.ema_crossover import EmaCrossover

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 策略參數（從環境變數讀取，有預設值）─────────────────────────────────────
FAST       = int(os.getenv("FAST_PERIOD", "20"))
SLOW       = int(os.getenv("SLOW_PERIOD", "120"))
ATR_P      = int(os.getenv("ATR_PERIOD", "7"))
ATR_M      = float(os.getenv("ATR_MULT", "1.5"))
FETCH_DAYS = int(os.getenv("FETCH_DAYS", "40"))
TIMEFRAME  = "30m"
STATE_PATH = Path(os.getenv("STATE_FILE", "signal_state.json"))


# ─────────────────────────────────────────────────────────────────────────────
# 資料抓取
# ─────────────────────────────────────────────────────────────────────────────

def fetch_30m_bars(api_key: str, secret_key: str) -> pd.DataFrame:
    """從 Shioaji 抓取近期 30m bars（TX 近月連續）。"""
    now = datetime.now(TAIPEI)
    start_d = (now - timedelta(days=FETCH_DAYS)).date()
    end_d   = now.date()

    log.info("Fetching 1m bars for TX %s ~ %s ...", start_d, end_d)
    with ShioajiClient(api_key=api_key, secret_key=secret_key) as client:
        bars_1m = client.fetch_1m_kbars("TXFR1", start=start_d, end=end_d)

    if bars_1m.empty:
        log.warning("No 1m bars returned from Shioaji")
        return pd.DataFrame()

    log.info("1m bars: %d rows, aggregating to 30m ...", len(bars_1m))
    bars_30m = aggregate_bars_to_higher(bars_1m, target_bar_min=30)

    # 只保留 TX 近月
    bars_30m = bars_30m[
        (bars_30m["product"] == "TX") & (bars_30m["contract_month"] == "CONT")
    ].copy()

    log.info("30m bars: %d rows", len(bars_30m))
    return bars_30m.sort_values("ts").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 訊號偵測
# ─────────────────────────────────────────────────────────────────────────────

def detect_signal(bars: pd.DataFrame):
    """以 EmaCrossover 策略跑暖機 + 最後一根 K 棒訊號偵測。

    Returns:
        (signal, strategy) — signal 為 SignalEvent 或 None；
        strategy 用於取出最終 EMA 值。
    """
    strat = EmaCrossover(
        fast_period=FAST, slow_period=SLOW,
        timeframe=TIMEFRAME,
        atr_period=ATR_P, atr_mult=ATR_M,
    )

    if len(bars) < SLOW + 5:
        log.warning("Not enough bars to warmup: %d (need %d+)", len(bars), SLOW + 5)
        return None, strat

    # 暖機：跑前 N-1 根，丟棄訊號
    for r in bars.iloc[:-1].itertuples(index=False):
        bar = BarEvent(
            ts=r.ts, timeframe=TIMEFRAME, product="TX", contract_month="CONT",
            open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            volume=int(r.volume),
        )
        strat.on_bar(bar)

    log.info(
        "Warmup done — position: %s  EMA%d=%.0f  EMA%d=%.0f",
        strat.current_target.value,
        FAST, strat._fast.value if strat._fast.ready else 0,
        SLOW, strat._slow.value if strat._slow.ready else 0,
    )

    # 最後一根
    last = bars.iloc[-1]
    bar = BarEvent(
        ts=last.ts, timeframe=TIMEFRAME, product="TX", contract_month="CONT",
        open=float(last.open), high=float(last.high),
        low=float(last.low), close=float(last.close),
        volume=int(last.volume),
    )
    signal = strat.on_bar(bar)
    return signal, strat


# ─────────────────────────────────────────────────────────────────────────────
# 狀態管理（去重）
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"last_signal_ts": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    log.info("State saved: %s", state)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram 通知
# ─────────────────────────────────────────────────────────────────────────────

_REASON_ZH = {
    "golden_cross":   f"黃金交叉（EMA{FAST} 穿越 EMA{SLOW}）",
    "death_cross":    f"死亡交叉（EMA{FAST} 跌破 EMA{SLOW}）",
    "atr_trail_stop": f"ATR 移動停利（週期 {ATR_P}×{ATR_M} 倍）",
}


def build_message(signal, strat: EmaCrossover, last_bar: pd.Series) -> str:
    ts_str = pd.Timestamp(last_bar.ts).strftime("%Y-%m-%d %H:%M TST")
    reason = _REASON_ZH.get(signal.reason, signal.reason)

    if signal.direction == Direction.LONG:
        icon   = "🟢"
        action = "做多進場訊號"
        note   = "⚠️ 訊號於 K 棒收盤確認，請於下一根開盤掛單進場"
    elif signal.reason == "atr_trail_stop":
        icon   = "🟠"
        action = "ATR 停利出場訊號"
        note   = "⚠️ 訊號於 K 棒收盤確認，請於下一根開盤平倉"
    else:
        icon   = "🔴"
        action = "出場訊號"
        note   = "⚠️ 訊號於 K 棒收盤確認，請於下一根開盤平倉"

    fast_val = f"{strat._fast.value:,.0f}" if strat._fast.ready else "N/A"
    slow_val = f"{strat._slow.value:,.0f}" if strat._slow.ready else "N/A"

    lines = [
        f"<b>{icon} {action}</b>",
        f"商品：台指期 TX（近月連續）",
        f"時間：{ts_str}",
        f"收盤：<b>{last_bar.close:,.0f}</b> 點",
        f"原因：{reason}",
        f"EMA{FAST}：{fast_val}　EMA{SLOW}：{slow_val}",
        "",
        note,
    ]

    # ATR 停利：加上停利水位
    if signal.reason == "atr_trail_stop" and strat._atr is not None and strat._atr.ready:
        peak = strat._peak_high or last_bar.close
        trail = peak - ATR_M * strat._atr.value
        lines.insert(6, f"ATR 停利水位：約 {trail:,.0f} 點")

    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Telegram notification sent successfully")
            return True
        log.error("Telegram API error %d: %s", resp.status_code, resp.text)
    except Exception as e:
        log.error("Failed to send Telegram notification: %s", e)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── 讀取環境變數 ────────────────────────────────────────────────────────
    api_key    = os.environ.get("SHIOAJI_API_KEY", "")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY", "")
    tg_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([api_key, secret_key, tg_token, tg_chat]):
        missing = [k for k, v in {
            "SHIOAJI_API_KEY": api_key,
            "SHIOAJI_SECRET_KEY": secret_key,
            "TELEGRAM_BOT_TOKEN": tg_token,
            "TELEGRAM_CHAT_ID": tg_chat,
        }.items() if not v]
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)

    # ── 拉取資料 ────────────────────────────────────────────────────────────
    bars = fetch_30m_bars(api_key, secret_key)
    if bars.empty or len(bars) < SLOW + 5:
        log.info("Insufficient data, exiting.")
        return

    last_bar = bars.iloc[-1]
    last_ts_str = str(last_bar.ts)
    log.info("Latest 30m bar: %s  close=%.0f", last_ts_str, last_bar.close)

    # ── 去重：若本輪最後一根 bar 已通知過，略過 ─────────────────────────────
    state = load_state()
    if state.get("last_signal_ts") == last_ts_str:
        log.info("Signal for this bar already sent, skipping.")
        return

    # ── 偵測訊號 ────────────────────────────────────────────────────────────
    signal, strat = detect_signal(bars)

    if signal is None:
        log.info("No signal on latest bar (%s).", last_ts_str)
        return

    log.info("Signal detected: %s  reason=%s", signal.direction, signal.reason)

    # ── 推播通知 ────────────────────────────────────────────────────────────
    msg = build_message(signal, strat, last_bar)
    sent = send_telegram(tg_token, tg_chat, msg)

    if sent:
        state["last_signal_ts"] = last_ts_str
        state["last_signal_reason"] = signal.reason
        state["last_signal_direction"] = signal.direction.value
        save_state(state)


if __name__ == "__main__":
    main()
