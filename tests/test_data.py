from datetime import date

import pytest

from pricemodel import data as data_mod
from pricemodel.config import ASSETS_BY_KEY
from pricemodel.data import DataError, Series, align, fetch_series


def test_series_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        Series("BTC", [date(2026, 8, 1)], [1.0, 2.0], "test")


def test_clean_sorts_dedupes_and_drops_bad_prices():
    pairs = [
        (date(2026, 8, 3), 30.0),
        (date(2026, 8, 1), 10.0),
        (date(2026, 8, 2), -5.0),  # non-positive, dropped
        (date(2026, 8, 3), 31.0),  # duplicate date, last one wins
        (date(2026, 8, 4), None),  # missing, dropped
    ]
    dates, closes = data_mod._clean(pairs)
    assert dates == [date(2026, 8, 1), date(2026, 8, 3)]
    assert closes == [10.0, 31.0]


def test_align_intersects_on_dates():
    btc = Series(
        "BTC",
        [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)],
        [10.0, 11.0, 12.0],
        "test",
    )
    mstr = Series("MSTR", [date(2026, 8, 1), date(2026, 8, 3)], [100.0, 120.0], "test")
    a, b = align(btc, mstr)
    assert a == [10.0, 12.0]  # 8/2 dropped - MSTR has no close that day
    assert b == [100.0, 120.0]


def test_align_with_no_overlap_is_empty():
    a = Series("BTC", [date(2026, 8, 1)], [10.0], "test")
    b = Series("MSTR", [date(2026, 9, 1)], [100.0], "test")
    assert align(a, b) == ([], [])


def test_fetch_falls_through_to_the_next_source(monkeypatch):
    calls = []

    def broken(symbol, days):
        calls.append("broken")
        raise RuntimeError("429 rate limited")

    def working(symbol, days):
        calls.append("working")
        n = 300
        dates = [date.fromordinal(date(2026, 8, 7).toordinal() - n + 1 + i) for i in range(n)]
        return dates, [100.0 + i for i in range(n)], "working"

    monkeypatch.setitem(data_mod.SOURCES, "BTC", [broken, working])
    series = fetch_series(ASSETS_BY_KEY["BTC"], 300)
    assert calls == ["broken", "working"]
    assert series.source == "working"


def test_fetch_skips_a_source_that_returns_too_little_history(monkeypatch):
    def stubby(symbol, days):
        dates = [date.fromordinal(date(2026, 8, 7).toordinal() - 9 + i) for i in range(10)]
        return dates, [100.0] * 10, "stubby"

    def full(symbol, days):
        n = 400
        dates = [date.fromordinal(date(2026, 8, 7).toordinal() - n + 1 + i) for i in range(n)]
        return dates, [100.0 + i for i in range(n)], "full"

    monkeypatch.setitem(data_mod.SOURCES, "BTC", [stubby, full])
    assert fetch_series(ASSETS_BY_KEY["BTC"], 400).source == "full"


def test_fetch_raises_when_every_source_fails(monkeypatch):
    def broken(symbol, days):
        raise RuntimeError("boom")

    monkeypatch.setitem(data_mod.SOURCES, "BTC", [broken, broken])
    with pytest.raises(DataError) as exc:
        fetch_series(ASSETS_BY_KEY["BTC"], 300)
    assert "all sources failed for BTC" in str(exc.value)


def test_fetch_trims_to_requested_days(monkeypatch):
    def long_history(symbol, days):
        n = 900
        dates = [date.fromordinal(date(2026, 8, 7).toordinal() - n + 1 + i) for i in range(n)]
        return dates, [100.0 + i for i in range(n)], "long"

    monkeypatch.setitem(data_mod.SOURCES, "BTC", [long_history])
    assert len(fetch_series(ASSETS_BY_KEY["BTC"], 400)) == 400
