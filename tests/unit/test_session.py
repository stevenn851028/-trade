"""session.py 單元測試。"""

from datetime import date, datetime, timedelta

import pytest

from twquant.data.session import (
    SessionKind,
    TAIPEI,
    bar_end,
    classify,
    day_session,
    expected_bar_ends,
    night_session,
)

D = date(2026, 4, 15)  # 週三


def taipei(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=TAIPEI)


class TestSessionBoundaries:
    def test_day_session_runs_08_45_to_13_45(self):
        s = day_session(D)
        assert s.kind == SessionKind.DAY
        assert s.start == taipei(2026, 4, 15, 8, 45)
        assert s.end == taipei(2026, 4, 15, 13, 45)

    def test_night_session_crosses_midnight_to_05_00(self):
        s = night_session(D)
        assert s.kind == SessionKind.NIGHT
        assert s.start == taipei(2026, 4, 15, 15, 0)
        assert s.end == taipei(2026, 4, 16, 5, 0)

    def test_expected_15m_bars_per_day_session_is_20(self):
        assert day_session(D).expected_bar_count(15) == 20

    def test_expected_15m_bars_per_night_session_is_56(self):
        assert night_session(D).expected_bar_count(15) == 56

    def test_expected_30m_bars_per_day_session_is_10(self):
        assert day_session(D).expected_bar_count(30) == 10

    def test_expected_30m_bars_per_night_session_is_28(self):
        assert night_session(D).expected_bar_count(30) == 28


class TestClassify:
    def test_opening_tick_classified_as_day(self):
        assert classify(taipei(2026, 4, 15, 8, 45)).kind == SessionKind.DAY

    def test_closing_tick_classified_as_day(self):
        assert classify(taipei(2026, 4, 15, 13, 45)).kind == SessionKind.DAY

    def test_midday_tick_classified_as_day(self):
        assert classify(taipei(2026, 4, 15, 11, 30)).kind == SessionKind.DAY

    def test_night_opening_classified_as_night(self):
        assert classify(taipei(2026, 4, 15, 15, 0)).kind == SessionKind.NIGHT

    def test_after_midnight_classified_as_previous_nights_session(self):
        # 04:30 算屬於前一日的夜盤
        sess = classify(taipei(2026, 4, 16, 4, 30))
        assert sess.kind == SessionKind.NIGHT
        assert sess.start.date() == date(2026, 4, 15)

    def test_between_sessions_is_none(self):
        assert classify(taipei(2026, 4, 15, 14, 0)) is None  # 14:00 在日盤與夜盤之間

    def test_requires_timezone_aware(self):
        naive = datetime(2026, 4, 15, 10, 0)
        with pytest.raises(ValueError):
            classify(naive)


class TestBarEnd:
    @pytest.mark.parametrize(
        "h,m,expected_end",
        [
            (8, 45, (9, 0)),     # 開盤 tick → 第一根 bar (08:45, 09:00]
            (8, 46, (9, 0)),
            (8, 59, (9, 0)),
            (9, 0, (9, 0)),      # 09:00:00 算在 (08:45, 09:00] 內
            (9, 1, (9, 15)),
            (13, 44, (13, 45)),
            (13, 45, (13, 45)),
        ],
    )
    def test_day_session_15m_bar_end(self, h, m, expected_end):
        end = bar_end(taipei(2026, 4, 15, h, m), 15)
        assert end == taipei(2026, 4, 15, *expected_end)

    def test_day_session_30m_first_bar_is_08_45_to_09_15(self):
        assert bar_end(taipei(2026, 4, 15, 8, 45), 30) == taipei(2026, 4, 15, 9, 15)
        assert bar_end(taipei(2026, 4, 15, 9, 14), 30) == taipei(2026, 4, 15, 9, 15)
        assert bar_end(taipei(2026, 4, 15, 9, 15), 30) == taipei(2026, 4, 15, 9, 15)
        assert bar_end(taipei(2026, 4, 15, 9, 16), 30) == taipei(2026, 4, 15, 9, 45)

    def test_night_first_bar_is_15_00_to_15_15(self):
        assert bar_end(taipei(2026, 4, 15, 15, 0), 15) == taipei(2026, 4, 15, 15, 15)

    def test_night_last_bar_ending_at_05_00_next_day(self):
        assert bar_end(taipei(2026, 4, 16, 4, 59), 15) == taipei(2026, 4, 16, 5, 0)
        assert bar_end(taipei(2026, 4, 16, 5, 0), 15) == taipei(2026, 4, 16, 5, 0)

    def test_outside_session_returns_none(self):
        assert bar_end(taipei(2026, 4, 15, 14, 30), 15) is None


class TestExpectedBarEnds:
    def test_day_only_30m_produces_10_bars(self):
        ends = expected_bar_ends(D, 30, include_night=False)
        assert len(ends) == 10
        assert ends[0] == taipei(2026, 4, 15, 9, 15)
        assert ends[-1] == taipei(2026, 4, 15, 13, 45)

    def test_day_and_night_15m_produces_76_bars(self):
        ends = expected_bar_ends(D, 15, include_night=True)
        assert len(ends) == 20 + 56

    def test_day_and_night_30m_produces_38_bars(self):
        ends = expected_bar_ends(D, 30, include_night=True)
        assert len(ends) == 10 + 28

    def test_no_duplicate_or_out_of_order(self):
        ends = expected_bar_ends(D, 15, include_night=True)
        assert len(set(ends)) == len(ends)
        assert ends == sorted(ends)
