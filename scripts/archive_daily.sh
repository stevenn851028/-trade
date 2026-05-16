#!/usr/bin/env bash
# TAIFEX 日常存檔 wrapper
#
# 用途：給 cron / systemd timer / launchd 呼叫的單一進入點。
# 行為：執行 `python -m twquant.data.cli archive`，補齊最近 30 天 Daily ZIP。
#
# 設定方式（cron 範例，每日 Asia/Taipei 07:00）:
#     0 7 * * * /home/you/-trade/scripts/archive_daily.sh
#
# 詳細排程教學見 docs/OPERATIONS.md。

set -euo pipefail

# 專案根目錄：本腳本所在的上一層
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

# 確保 PYTHONPATH 指向 src layout
export PYTHONPATH="${PYTHONPATH:-}:${PROJECT_DIR}/src"

# Log 目錄
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/archive_$(date +%Y%m).log"

# 執行；stdout + stderr 同步寫入月份 log
{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') archive run ====="
    python -m twquant.data.cli archive \
        --out-dir "${PROJECT_DIR}/data/raw" \
        --lookback "${ARCHIVE_LOOKBACK:-30}" \
        --timeout "${ARCHIVE_TIMEOUT:-30}"
    echo "===== exit=$? ====="
    echo
} >> "$LOG_FILE" 2>&1
