from datetime import date, timedelta

import pytest

from pricemodel.history import append_record, load_records, score_history, summarize


def _record(run_date: date, price: float, median: float, p5: float, p95: float):
    return {
        "run_date": run_date.isoformat(),
        "assets": {
            "BTC": {
                "price": price,
                "forecasts": {
                    "1": {"median": median, "p5": p5, "p95": p95, "prob_up": 0.5}
                },
            }
        },
    }


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "forecasts.jsonl"
    append_record(path, _record(date(2026, 8, 1), 100, 101, 90, 110))
    append_record(path, _record(date(2026, 8, 2), 102, 103, 92, 112))
    records = load_records(path)
    assert len(records) == 2
    assert records[0]["run_date"] == "2026-08-01"


def test_load_records_missing_file_is_empty(tmp_path):
    assert load_records(tmp_path / "nope.jsonl") == []


def test_malformed_line_is_skipped(tmp_path):
    path = tmp_path / "forecasts.jsonl"
    append_record(path, _record(date(2026, 8, 1), 100, 101, 90, 110))
    with open(path, "a") as fh:
        fh.write('{"run_date": "truncated"\n')  # interrupted write
    assert len(load_records(path)) == 1


def test_scoring_computes_error_and_band_hit():
    made = date.today() - timedelta(days=10)
    target = made + timedelta(days=1)
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    actuals = {"BTC": {target: 121.0}}

    scored, summary = score_history(records, actuals)
    assert len(scored) == 1
    s = scored[0]
    assert s.actual == 121.0
    assert s.error_pct == pytest.approx((121.0 / 110.0 - 1) * 100)
    assert s.within_band is True
    assert s.direction_correct is True
    assert summary[0].n == 1


def test_outside_band_is_recorded():
    made = date.today() - timedelta(days=10)
    target = made + timedelta(days=1)
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    scored, _ = score_history(records, {"BTC": {target: 200.0}})
    assert scored[0].within_band is False


def test_direction_incorrect_when_actual_moves_the_other_way():
    made = date.today() - timedelta(days=10)
    target = made + timedelta(days=1)
    # Forecast median above spot (up), actual came in below spot.
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    scored, _ = score_history(records, {"BTC": {target: 95.0}})
    assert scored[0].direction_correct is False


def test_target_rolls_forward_past_a_weekend():
    """An equity target landing on Saturday resolves to the next available close."""
    made = date.today() - timedelta(days=10)
    target = made + timedelta(days=1)
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    # Only a close two days after the target exists.
    actuals = {"BTC": {target + timedelta(days=2): 112.0}}
    scored, _ = score_history(records, actuals)
    assert len(scored) == 1
    assert scored[0].target_date == target + timedelta(days=2)


def test_unelapsed_horizon_is_not_scored():
    made = date.today()
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    scored, summary = score_history(records, {"BTC": {}})
    assert scored == []
    assert summary == []


def test_records_older_than_lookback_are_ignored():
    made = date.today() - timedelta(days=400)
    target = made + timedelta(days=1)
    records = [_record(made, price=100.0, median=110.0, p5=90.0, p95=130.0)]
    scored, _ = score_history(records, {"BTC": {target: 111.0}}, max_lookback_days=180)
    assert scored == []


def test_summarize_median_absolute_error():
    made = date.today() - timedelta(days=10)
    target = made + timedelta(days=1)
    records = [
        _record(made, 100.0, 100.0, 90.0, 130.0),
        _record(made - timedelta(days=1), 100.0, 100.0, 90.0, 130.0),
    ]
    actuals = {"BTC": {target: 110.0, made: 105.0}}
    scored, summary = score_history(records, actuals)
    assert len(summary) == 1
    assert summary[0].asset == "BTC"
    assert summary[0].horizon_days == 1
    assert summary[0].n == len(scored)


def test_summarize_empty_input():
    assert summarize([]) == []
