"""archive.py 單元測試（以 monkeypatch mock downloader）。"""

from datetime import date
from pathlib import Path

import pytest

from twquant.data import archive as archive_mod
from twquant.data.taifex_downloader import DownloadResult


def make_result(d: date, status: str, size: int = 100) -> DownloadResult:
    return DownloadResult(
        trade_date=d,
        url=f"https://dummy/Daily_{d}.zip",
        path=Path(f"/tmp/fake/Daily_{d}.zip") if status in ("ok", "cached") else None,
        status=status,
        bytes_downloaded=size if status == "ok" else 0,
    )


class TestArchive:
    def test_archive_catches_up_range(self, monkeypatch, tmp_path):
        calls = {}

        def fake_download_range(start, end, out_dir, **kwargs):
            calls["start"] = start
            calls["end"] = end
            calls["out_dir"] = out_dir
            calls["skip_if_exists"] = kwargs.get("skip_if_exists")
            # 回三筆：ok、cached、not_found
            from datetime import timedelta
            return [
                make_result(start + timedelta(days=0), "ok"),
                make_result(start + timedelta(days=1), "cached"),
                make_result(start + timedelta(days=2), "not_found"),
            ]

        monkeypatch.setattr(archive_mod, "download_date_range", fake_download_range)

        summary = archive_mod.run_archive(
            tmp_path, lookback_days=30, today=date(2026, 4, 30)
        )
        assert calls["start"] == date(2026, 3, 31)
        assert calls["end"] == date(2026, 4, 30)
        assert calls["skip_if_exists"] is True
        assert summary.ok == 1
        assert summary.cached == 1
        assert summary.not_found == 1
        assert summary.errors == 0

    def test_archive_exit_nonzero_on_error(self, monkeypatch, tmp_path):
        def fake_download_range(start, end, out_dir, **kwargs):
            return [
                make_result(start, "error"),
            ]

        monkeypatch.setattr(archive_mod, "download_date_range", fake_download_range)
        summary = archive_mod.run_archive(tmp_path, lookback_days=1, today=date(2026, 4, 30))
        assert summary.errors == 1

    def test_summary_string_format(self, monkeypatch, tmp_path):
        def fake_download_range(start, end, out_dir, **kwargs):
            return [make_result(start, "ok")]

        monkeypatch.setattr(archive_mod, "download_date_range", fake_download_range)
        s = archive_mod.run_archive(tmp_path, lookback_days=1, today=date(2026, 4, 30))
        text = s.summary()
        assert "ok=1" in text
        assert "holiday(404)=0" in text
        assert "errors=0" in text

    def test_taipei_today_returns_a_date(self):
        d = archive_mod.taipei_today()
        assert isinstance(d, date)
