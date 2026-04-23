"""TAIFEX 盤後逐筆成交 ZIP 下載器。

官方檔案網址格式（推定，依社群紀錄 2013+）：
    https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_YYYY_MM_DD.zip

注意：
- 官方只保留最近 ~30 個交易日資料，更早資料須向 TAIFEX 申請
- 休市日（週末、國定假日）無此檔案，HTTP 404 是正常情況
- 部分新檔可能從 Big5 改為 UTF-8，編碼由 parser 端處理

此模組不做任何全域 retry／rate-limit，交由 CLI 決定排程。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

TAIFEX_DAILY_URL = (
    "https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{y}_{m}_{d}.zip"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (twquant-data-pipeline research; contact: your-email@example.com)"
)


@dataclass
class DownloadResult:
    trade_date: date
    url: str
    path: Path | None       # 成功則為本機路徑，失敗為 None
    status: str             # "ok" | "not_found" | "error" | "cached"
    bytes_downloaded: int = 0
    error: str | None = None


def daily_url(d: date) -> str:
    return TAIFEX_DAILY_URL.format(y=d.year, m=f"{d.month:02d}", d=f"{d.day:02d}")


def _fetch(url: str, timeout: int, user_agent: str) -> bytes:
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_daily(
    trade_date: date,
    out_dir: str | Path,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 30,
    skip_if_exists: bool = True,
    retries: int = 2,
    backoff_sec: float = 2.0,
) -> DownloadResult:
    """下載指定交易日的 Daily ZIP 至 `out_dir/Daily_YYYY_MM_DD.zip`。

    - 已存在且 skip_if_exists=True → 回傳 status='cached'
    - 404 → status='not_found'（休市日正常情況）
    - 其他網路錯誤 → retry 到上限，最終 status='error'
    """
    url = daily_url(trade_date)
    out_path = Path(out_dir) / f"Daily_{trade_date.year}_{trade_date.month:02d}_{trade_date.day:02d}.zip"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if skip_if_exists and out_path.exists() and out_path.stat().st_size > 0:
        return DownloadResult(trade_date, url, out_path, "cached", out_path.stat().st_size)

    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            logger.info("Fetching %s (attempt %d)", url, attempt + 1)
            data = _fetch(url, timeout=timeout, user_agent=user_agent)
            out_path.write_bytes(data)
            return DownloadResult(trade_date, url, out_path, "ok", len(data))
        except HTTPError as e:
            if e.code == 404:
                return DownloadResult(trade_date, url, None, "not_found", 0, f"HTTP 404")
            last_err = f"HTTP {e.code}: {e.reason}"
            logger.warning("HTTPError for %s: %s", url, last_err)
        except URLError as e:
            last_err = f"URLError: {e.reason}"
            logger.warning("URLError for %s: %s", url, last_err)
        except Exception as e:  # noqa: BLE001 — surface any other error clearly
            last_err = f"{type(e).__name__}: {e}"
            logger.warning("Unexpected error for %s: %s", url, last_err)

        if attempt < retries:
            time.sleep(backoff_sec * (2 ** attempt))

    return DownloadResult(trade_date, url, None, "error", 0, last_err)


def download_date_range(
    start: date,
    end: date,
    out_dir: str | Path,
    **kwargs,
) -> list[DownloadResult]:
    """下載區間內每日 ZIP（週末自動跳過，休市日會得到 not_found）。"""
    from datetime import timedelta
    results: list[DownloadResult] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            results.append(download_daily(d, out_dir, **kwargs))
        d += timedelta(days=1)
    return results
