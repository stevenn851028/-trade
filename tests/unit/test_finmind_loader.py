"""finmind_loader.py 單元測試（mock HTTP）。"""

import io
import json
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from twquant.data import finmind_loader as fm
from twquant.data.finmind_loader import (
    FinMindClient,
    FinMindError,
    consolidate_daily_bars,
)


def _fake_payload(rows: list[dict], status: int = 200, msg: str = "success") -> bytes:
    return json.dumps({"status": status, "msg": msg, "data": rows}).encode()


def _mock_urlopen(body: bytes):
    """Return a context manager mimicking urlopen()."""
    class _Resp:
        def __init__(self, b): self._b = b
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *_a): pass
    return _Resp(body)


class TestFinMindClient:
    def test_fetch_returns_dataframe(self):
        rows = [
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17500.0, "high": 17600.0, "low": 17400.0, "close": 17550.0,
             "spread": 0, "spread_per": 0, "volume": 80000,
             "settlement_price": 17550.0, "open_interest": 70000,
             "trading_session": "regular"},
        ]
        with patch.object(fm, "urlopen", return_value=_mock_urlopen(_fake_payload(rows))):
            c = FinMindClient()
            df = c.fetch_taiwan_futures_daily("TX", date(2024, 1, 1), date(2024, 1, 31))
        assert len(df) == 1
        assert df.iloc[0]["futures_id"] == "TX"

    def test_empty_data_returns_empty_df(self):
        with patch.object(fm, "urlopen", return_value=_mock_urlopen(_fake_payload([]))):
            c = FinMindClient()
            df = c.fetch_taiwan_futures_daily("TX", date(2024, 1, 1), date(2024, 1, 2))
        assert df.empty

    def test_non_200_status_raises(self):
        with patch.object(fm, "urlopen",
                          return_value=_mock_urlopen(_fake_payload([], status=402,
                                                                    msg="payment required"))):
            c = FinMindClient()
            with pytest.raises(FinMindError, match="payment"):
                c.fetch_taiwan_futures_daily("TX", date(2024, 1, 1), date(2024, 1, 2))


class TestConsolidate:
    def test_combines_regular_and_after_market(self):
        rows = [
            # 同一天同一合約，日盤 + 夜盤兩筆
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17500.0, "high": 17600.0, "low": 17400.0, "close": 17550.0,
             "volume": 80000, "open_interest": 70000, "trading_session": "regular"},
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17560.0, "high": 17700.0, "low": 17500.0, "close": 17650.0,
             "volume": 20000, "open_interest": 70500, "trading_session": "after_market"},
        ]
        df = pd.DataFrame(rows)
        out = consolidate_daily_bars(df)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["open"] == 17500.0          # 日盤開
        assert row["close"] == 17650.0         # 夜盤收
        assert row["high"] == 17700.0          # 兩段最大
        assert row["low"] == 17400.0           # 兩段最小
        assert row["volume"] == 100000         # 加總
        assert row["timeframe"] == "1d"
        assert row["product"] == "TX"
        assert row["contract_month"] == "202401"

    def test_only_regular_session(self):
        rows = [
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17500.0, "high": 17600.0, "low": 17400.0, "close": 17550.0,
             "volume": 80000, "open_interest": 70000, "trading_session": "regular"},
        ]
        out = consolidate_daily_bars(pd.DataFrame(rows))
        assert out.iloc[0]["close"] == 17550.0
        assert out.iloc[0]["volume"] == 80000

    def test_multiple_contract_months_kept_separate(self):
        rows = [
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17500.0, "high": 17500.0, "low": 17500.0, "close": 17500.0,
             "volume": 80000, "open_interest": 70000, "trading_session": "regular"},
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202402",
             "open": 17600.0, "high": 17600.0, "low": 17600.0, "close": 17600.0,
             "volume": 5000, "open_interest": 10000, "trading_session": "regular"},
        ]
        out = consolidate_daily_bars(pd.DataFrame(rows))
        assert len(out) == 2
        assert set(out["contract_month"]) == {"202401", "202402"}

    def test_ts_set_to_taipei_1345(self):
        rows = [
            {"date": "2024-01-02", "futures_id": "TX", "contract_date": "202401",
             "open": 17500.0, "high": 17500.0, "low": 17500.0, "close": 17500.0,
             "volume": 80000, "open_interest": 70000, "trading_session": "regular"},
        ]
        out = consolidate_daily_bars(pd.DataFrame(rows))
        ts = out.iloc[0]["ts"]
        assert ts.hour == 13 and ts.minute == 45
        assert str(ts.tz) == "Asia/Taipei"
