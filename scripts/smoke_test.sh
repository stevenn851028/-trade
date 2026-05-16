#!/usr/bin/env bash
# Phase 1 端對端 smoke test：以實際 TAIFEX 資料跑一輪完整 pipeline。
#
# 用法（從專案根目錄）:
#     ./scripts/smoke_test.sh
#
# 行為:
#     1. 檢查 Python 相依套件
#     2. 找出 data/raw/ 內最早與最新的 Daily_*.zip
#     3. load → 自動 build-continuous → stats
#     4. 跑 15m 與 30m 回測（含 HTML 報告）
#     5. compare 比較
#     6. 把所有輸出彙整到 results/smoke_<時戳>/SUMMARY.txt 供回報
#
# 若 data/raw/ 為空，請先：
#     ./scripts/archive_daily.sh
# 或在 docs/OPERATIONS.md 設定 cron，幾天後再回來跑這支腳本。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# 啟用 venv（若存在）
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
elif [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "venv/bin/activate"
fi

export PYTHONPATH="${PYTHONPATH:-}:${PROJECT_DIR}/src"

DB="data/db/bars.sqlite"
RAW="data/raw"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="results/smoke_${STAMP}"
mkdir -p "$OUT"
SUMMARY="$OUT/SUMMARY.txt"

# Pipe stdout + stderr into log file while still showing on console
exec > >(tee -a "$SUMMARY") 2>&1

echo "================================================================"
echo " TWQuant Phase 1 smoke test  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================================"
echo

echo "[1/8] Checking dependencies..."
python -c "import pandas, matplotlib; print('  pandas', pandas.__version__,
'matplotlib', matplotlib.__version__)" || {
    echo "  Missing deps. Run: pip install -r requirements-dev.txt"
    exit 1
}
echo

echo "[2/8] Scanning raw data..."
nzip="$(find "$RAW" -maxdepth 1 -name 'Daily_*.zip' 2>/dev/null | wc -l | tr -d ' ')"
echo "  Found $nzip Daily_*.zip files in $RAW/"
if [ "$nzip" -eq 0 ]; then
    echo "  No raw data — run ./scripts/archive_daily.sh first."
    exit 1
fi

oldest_zip="$(find "$RAW" -maxdepth 1 -name 'Daily_*.zip' | sort | head -1)"
newest_zip="$(find "$RAW" -maxdepth 1 -name 'Daily_*.zip' | sort | tail -1)"
START="$(basename "$oldest_zip" | sed -E 's/Daily_([0-9]{4})_([0-9]{2})_([0-9]{2})\.zip/\1-\2-\3/')"
END="$(basename "$newest_zip" | sed -E 's/Daily_([0-9]{4})_([0-9]{2})_([0-9]{2})\.zip/\1-\2-\3/')"
echo "  Date range: $START  →  $END"
echo

echo "[3/8] Loading into SQLite (auto build-continuous)..."
python -m twquant.data.cli load --start "$START" --end "$END" \
    --raw-dir "$RAW" --db "$DB" || true
echo

echo "[4/8] DB stats:"
python -m twquant.data.cli stats --db "$DB"
echo

echo "[5/8] Backtest 30m..."
python -m twquant.backtest.cli run --strategy ema_cross_30m \
    --db "$DB" --start "$START" --end "$END" \
    --output "$OUT/run_30m" || true
echo

echo "[6/8] Backtest 15m..."
python -m twquant.backtest.cli run --strategy ema_cross_15m \
    --db "$DB" --start "$START" --end "$END" \
    --output "$OUT/run_15m" || true
echo

echo "[7/8] Compare:"
python -m twquant.backtest.cli compare "$OUT/run_15m" "$OUT/run_30m" || true
echo

echo "[8/8] Files written:"
find "$OUT" -type f | sort
echo
echo "================================================================"
echo " HTML reports (open in browser):"
echo "   $OUT/run_30m/report.html"
echo "   $OUT/run_15m/report.html"
echo "================================================================"
echo " Summary saved to: $SUMMARY"
echo " 把整份 SUMMARY.txt 內容貼回給我，我會診斷實際資料下是否有 bug。"
echo "================================================================"
