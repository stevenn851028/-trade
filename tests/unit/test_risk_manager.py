"""風控 RiskManager 單元測試。"""

from datetime import datetime

import pytest

from twquant.data.session import TAIPEI
from twquant.events import Direction, Side, SignalEvent
from twquant.risk.manager import PortfolioSnapshot, RiskConfig, RiskManager


def _signal(target: Direction, reason: str = "test") -> SignalEvent:
    return SignalEvent(datetime(2026, 4, 15, 9, 0, tzinfo=TAIPEI), target, reason)


def _snap(cash=1_000_000.0, equity=1_000_000.0, peak=1_000_000.0, lots=0):
    return PortfolioSnapshot(cash=cash, equity=equity, peak_equity=peak,
                             position_lots=lots)


class TestEntryExit:
    def test_long_signal_no_position_creates_buy(self):
        rm = RiskManager()
        order = rm.on_signal(_signal(Direction.LONG), _snap())
        assert order is not None
        assert order.side == Side.BUY
        assert order.quantity == 1

    def test_flat_signal_with_position_creates_sell(self):
        rm = RiskManager()
        order = rm.on_signal(_signal(Direction.FLAT), _snap(lots=1))
        assert order is not None
        assert order.side == Side.SELL

    def test_long_signal_already_long_does_nothing(self):
        rm = RiskManager()
        assert rm.on_signal(_signal(Direction.LONG), _snap(lots=1)) is None

    def test_flat_signal_no_position_does_nothing(self):
        rm = RiskManager()
        assert rm.on_signal(_signal(Direction.FLAT), _snap()) is None


class TestMarginCheck:
    def test_entry_blocked_when_margin_utilization_exceeds_cap(self):
        # 1 lot needs NT$184,000; cap 50% → need cash ≥ NT$368,000
        rm = RiskManager(RiskConfig(fixed_lots=1, margin_per_lot=184_000,
                                    max_margin_utilization=0.5))
        # cash too small
        assert rm.on_signal(_signal(Direction.LONG), _snap(cash=200_000)) is None
        # enough cash
        assert rm.on_signal(_signal(Direction.LONG), _snap(cash=400_000)) is not None

    def test_exit_not_blocked_by_margin(self):
        rm = RiskManager(RiskConfig(margin_per_lot=184_000,
                                    max_margin_utilization=0.5))
        # even with bad cash, allow closing
        order = rm.on_signal(_signal(Direction.FLAT), _snap(cash=1.0, lots=1))
        assert order is not None and order.side == Side.SELL


class TestKillSwitch:
    def test_kill_switch_trips_on_drawdown(self):
        rm = RiskManager(RiskConfig(max_drawdown_pct=20.0))
        # peak=1M, equity=700k → drawdown 30%
        assert rm.on_signal(_signal(Direction.LONG),
                            _snap(equity=700_000, peak=1_000_000)) is None
        assert rm.kill_switch_tripped is True

    def test_kill_switch_blocks_entries_but_allows_exits(self):
        rm = RiskManager(RiskConfig(max_drawdown_pct=20.0))
        rm.kill_switch_tripped = True
        assert rm.on_signal(_signal(Direction.LONG), _snap()) is None
        order = rm.on_signal(_signal(Direction.FLAT), _snap(lots=1))
        assert order is not None
        assert order.side == Side.SELL

    def test_kill_switch_does_not_trip_when_within_threshold(self):
        rm = RiskManager(RiskConfig(max_drawdown_pct=20.0))
        # drawdown 10% < 20%
        order = rm.on_signal(_signal(Direction.LONG),
                             _snap(equity=900_000, peak=1_000_000))
        assert rm.kill_switch_tripped is False
        assert order is not None
