"""shioaji_loader.py 單元測試（不依賴實際 Shioaji 套件）。"""

import os
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from twquant.data import shioaji_loader as sl
from twquant.data.shioaji_loader import (
    ShioajiClient,
    ShioajiNotInstalled,
    _kbars_to_dataframe,
    credentials_from_env,
    normalize_kbars,
)


class TestCredentialsFromEnv:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("SHIOAJI_API_KEY", "k")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "s")
        assert credentials_from_env() == ("k", "s")

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("SHIOAJI_API_KEY", raising=False)
        monkeypatch.delenv("SHIOAJI_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SHIOAJI_API_KEY"):
            credentials_from_env()


class TestClientConstruction:
    def test_requires_keys(self):
        with pytest.raises(ValueError):
            ShioajiClient(api_key="", secret_key="")

    def test_lazy_import_when_not_connected(self):
        # 即使沒裝 shioaji 也應該能建構物件
        c = ShioajiClient(api_key="k", secret_key="s")
        assert c._api is None


class TestKbarsToDataFrame:
    def test_passes_through_dataframe(self):
        df = pd.DataFrame({"ts": [1], "Open": [1.0]})
        assert _kbars_to_dataframe(df) is df

    def test_converts_attr_object(self):
        kbars = MagicMock()
        kbars.ts = [datetime(2024, 1, 2, 9, 0)]
        kbars.Open = [17000.0]
        kbars.High = [17010.0]
        kbars.Low = [16990.0]
        kbars.Close = [17005.0]
        kbars.Volume = [50]
        out = _kbars_to_dataframe(kbars)
        assert len(out) == 1
        assert out["Open"].iloc[0] == 17000.0

    def test_handles_none(self):
        assert _kbars_to_dataframe(None).empty


class TestNormalize:
    def test_empty_returns_schema(self):
        out = normalize_kbars(pd.DataFrame(), "TXFR1")
        assert list(out.columns) == ["ts", "timeframe", "product",
                                     "contract_month", "open", "high",
                                     "low", "close", "volume"]
        assert out.empty

    def test_basic_normalization(self):
        raw = pd.DataFrame({
            "ts": [datetime(2024, 1, 2, 9, 0), datetime(2024, 1, 2, 9, 1)],
            "Open": [17000.0, 17005.0],
            "High": [17010.0, 17015.0],
            "Low": [16990.0, 17000.0],
            "Close": [17005.0, 17012.0],
            "Volume": [50, 60],
        })
        out = normalize_kbars(raw, "TXFR1")
        assert len(out) == 2
        assert out["timeframe"].iloc[0] == "1m"
        assert out["product"].iloc[0] == "TX"
        assert out["contract_month"].iloc[0] == "CONT"
        assert out["open"].iloc[0] == 17000.0
        assert str(out["ts"].iloc[0].tz) == "Asia/Taipei"

    def test_mxf_symbol_maps_to_mtx(self):
        raw = pd.DataFrame({
            "ts": [datetime(2024, 1, 2, 9, 0)],
            "Open": [17000.0], "High": [17010.0], "Low": [16990.0],
            "Close": [17005.0], "Volume": [10],
        })
        out = normalize_kbars(raw, "MXFR1")
        assert out["product"].iloc[0] == "MTX"

    def test_drops_duplicates(self):
        raw = pd.DataFrame({
            "ts": [datetime(2024, 1, 2, 9, 0)] * 2,
            "Open": [17000.0, 17001.0], "High": [17010.0, 17011.0],
            "Low": [16990.0, 16991.0], "Close": [17005.0, 17006.0],
            "Volume": [10, 20],
        })
        out = normalize_kbars(raw, "TXFR1")
        assert len(out) == 1


class TestClientFetchMocked:
    def test_fetch_1m_chunks_and_concatenates(self, monkeypatch):
        """模擬 Shioaji API：兩個 chunk 各回傳 5 根 bar。"""
        mock_sj = MagicMock()
        mock_api = MagicMock()
        mock_sj.Shioaji.return_value = mock_api

        # 模擬 contract object
        mock_contract = MagicMock()
        mock_api.Contracts.Futures.TXFR1 = mock_contract

        # 模擬 kbars 回傳
        def fake_kbars(contract, start, end):
            kb = MagicMock()
            kb.ts = [datetime(2024, 1, 1, 9, i) for i in range(5)]
            kb.Open = [17000.0 + i for i in range(5)]
            kb.High = [17010.0 + i for i in range(5)]
            kb.Low = [16990.0 + i for i in range(5)]
            kb.Close = [17005.0 + i for i in range(5)]
            kb.Volume = [10 + i for i in range(5)]
            return kb
        mock_api.kbars.side_effect = fake_kbars
        mock_api.login.return_value = ["mock-account"]

        # patch import shioaji
        monkeypatch.setitem(__import__("sys").modules, "shioaji", mock_sj)

        c = ShioajiClient(api_key="k", secret_key="s")
        df = c.fetch_1m_kbars("TXFR1", date(2024, 1, 1), date(2024, 3, 1))
        # 兩個月 (~60 日) / 25 日 chunk → 3 chunks，每個回傳 5 列；去重後仍應有資料
        # 因為 fake_kbars 每次回傳相同 ts，drop_duplicates 後只剩 5 列
        assert len(df) == 5
        assert df["product"].iloc[0] == "TX"
        assert df["timeframe"].iloc[0] == "1m"

    def test_shioaji_not_installed_raises(self, monkeypatch):
        # 模擬 import shioaji 失敗
        import sys
        monkeypatch.setitem(sys.modules, "shioaji", None)
        c = ShioajiClient(api_key="k", secret_key="s")
        with pytest.raises(ShioajiNotInstalled):
            c.connect()
