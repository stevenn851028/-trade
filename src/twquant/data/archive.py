"""日常存檔（catch-up）。

用途：TAIFEX 公開下載只保留最近 ~30 交易日；此模組每日定時跑一次，
補齊本機 `data/raw/` 缺漏的所有 Daily_*.zip。

設計原則：
1. **Idempotent**：已存在檔案預設跳過，安全重跑多次
2. **週末自動跳過**：Mon–Fri 才嘗試下載
3. **404 視為正常**：國定假日、颱風假；不重試、不告警
4. **錯誤留痕**：network 錯誤等下次執行時會再試
5. **時區固定 Asia/Taipei**：不受 VPS 系統 timezone 影響

typical schedule：每日 07:00 Asia/Taipei（夜盤 05:00 收盤 + 2 小時 buffer）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from twquant.data.session import TAIPEI
from twquant.data.taifex_downloader import DownloadResult, download_date_range

log = logging.getLogger(__name__)


@dataclass
class ArchiveSummary:
    start: date
    end: date
    results: list[DownloadResult] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def cached(self) -> int:
        return sum(1 for r in self.results if r.status == "cached")

    @property
    def not_found(self) -> int:
        return sum(1 for r in self.results if r.status == "not_found")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    def summary(self) -> str:
        return (
            f"[archive {self.start}..{self.end}] "
            f"ok={self.ok}, cached={self.cached}, "
            f"holiday(404)={self.not_found}, errors={self.errors}"
        )


def taipei_today() -> date:
    return datetime.now(TAIPEI).date()


def run_archive(
    out_dir: str | Path,
    *,
    lookback_days: int = 30,
    today: date | None = None,
    timeout: int = 30,
    retries: int = 2,
) -> ArchiveSummary:
    """補齊 `today - lookback_days` 至 `today` 之間所有遺漏的 Daily ZIP。

    Args:
        out_dir: 存檔目錄（通常 `data/raw/`）
        lookback_days: 回溯天數，預設 30（= TAIFEX 公開窗口）
        today: 今日日期（Asia/Taipei），預設取當下
        timeout: 單次下載逾時（秒）
        retries: 網路錯誤重試次數
    """
    today = today or taipei_today()
    start = today - timedelta(days=lookback_days)

    log.info("Archive run: %s → %s (lookback=%d days) → %s",
             start, today, lookback_days, out_dir)

    results = download_date_range(
        start=start,
        end=today,
        out_dir=out_dir,
        timeout=timeout,
        retries=retries,
        skip_if_exists=True,
    )

    summary = ArchiveSummary(start=start, end=today, results=results)
    log.info("%s", summary.summary())
    return summary
