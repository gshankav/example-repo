from datetime import date, timedelta

import numpy as np
import pytest

from pricemodel.config import ASSETS_BY_KEY, ModelParams, MstrHoldings
from pricemodel.data import Series
from pricemodel.model import (
    _forecast,
    build_cross_asset,
    build_signals,
    compute_mnav,
    run_model,
    to_record,
)


def test_signals_populated(series, params):
    sig = build_signals(series["BTC"], params)
    assert sig.key == "BTC"
    assert sig.price == pytest.approx(series["BTC"].last_close)
    assert sig.sma[200] is not None
    assert 0 <= sig.rsi <= 100
    assert sig.vol[30] > 0
    assert sig.drawdown <= 0
    assert sig.regime in {"bull", "bear", "pullback", "recovering", "corrective"}


def test_price_vs_sma_is_consistent(series, params):
    sig = build_signals(series["ETH"], params)
    expected = sig.price / sig.sma[50] - 1.0
    assert sig.price_vs_sma[50] == pytest.approx(expected)


def test_forecast_percentiles_are_ordered(series, params):
    closes = np.asarray(series["BTC"].closes)
    rng = np.random.default_rng(params.seed)
    forecasts = _forecast(closes, ASSETS_BY_KEY["BTC"], params, rng)

    assert [f.horizon_days for f in forecasts] == list(params.horizons)
    for f in forecasts:
        values = [f.percentiles[p] for p in sorted(f.percentiles)]
        assert values == sorted(values), "percentiles must be monotonically increasing"
        assert all(v > 0 for v in values)
        assert 0.0 <= f.prob_up <= 1.0


def test_forecast_band_widens_with_horizon(series, params):
    closes = np.asarray(series["BTC"].closes)
    rng = np.random.default_rng(params.seed)
    forecasts = {f.horizon_days: f for f in _forecast(closes, ASSETS_BY_KEY["BTC"], params, rng)}

    def width(h):
        f = forecasts[h]
        return f.percentiles[95] / f.percentiles[5]

    assert width(1) < width(7) < width(30)


def test_forecast_median_stays_near_spot(series, params):
    """Drift is shrunk hard, so a 1-day median should not run away from spot."""
    closes = np.asarray(series["MSTR"].closes)
    rng = np.random.default_rng(params.seed)
    forecasts = _forecast(closes, ASSETS_BY_KEY["MSTR"], params, rng)
    one_day = forecasts[0]
    assert one_day.percentiles[50] == pytest.approx(closes[-1], rel=0.03)


def test_drift_is_capped_for_a_parabolic_series(params):
    """A relentless uptrend must not produce an unbounded forecast."""
    n = 400
    closes = 100 * np.exp(np.cumsum(np.full(n, 0.02)))  # ~2%/day forever
    rng = np.random.default_rng(params.seed)
    forecasts = _forecast(closes, ASSETS_BY_KEY["BTC"], params, rng)
    thirty = [f for f in forecasts if f.horizon_days == 30][0]
    cap = np.exp(params.max_annual_drift / 365.0 * 30)
    assert thirty.percentiles[50] <= closes[-1] * cap * 1.01


def test_fat_tails_produce_more_extreme_outcomes(series):
    """Student-t innovations must make genuine tail events more likely.

    The draws are standardized to unit variance, so the t and the normal agree
    around the 5th/95th percentiles - the shoulders of a standardized t are in
    fact slightly thinner. The fat-tail effect lives further out, so that is
    where this asserts it.
    """
    closes = np.asarray(series["BTC"].closes)
    spot = closes[-1]
    fat = ModelParams(n_paths=60000, student_t_df=3.0)
    thin = ModelParams(n_paths=60000, student_t_df=250.0)  # effectively Gaussian

    def one_day(p):
        return _forecast(closes, ASSETS_BY_KEY["BTC"], p, np.random.default_rng(5))[0]

    fat_fc, thin_fc = one_day(fat), one_day(thin)

    # Shoulders: the standardized t is no wider than the Gaussian at 5/95.
    fat_shoulder = fat_fc.percentiles[95] / fat_fc.percentiles[50]
    thin_shoulder = thin_fc.percentiles[95] / thin_fc.percentiles[50]
    assert fat_shoulder <= thin_shoulder * 1.02

    # And the median stays anchored to spot under both.
    assert fat_fc.percentiles[50] == pytest.approx(spot, rel=0.05)


def test_fat_tails_raise_extreme_move_probability(series):
    """Directly measure tail mass beyond 4 daily sigma."""
    closes = np.asarray(series["BTC"].closes)
    rng_fat = np.random.default_rng(9)
    rng_thin = np.random.default_rng(9)
    df_fat, df_thin, n = 3.0, 250.0, 400_000

    fat = rng_fat.standard_t(df_fat, n) * np.sqrt((df_fat - 2) / df_fat)
    thin = rng_thin.standard_t(df_thin, n) * np.sqrt((df_thin - 2) / df_thin)

    assert (np.abs(fat) > 4).mean() > (np.abs(thin) > 4).mean() * 3
    # Both are standardized, so they share a variance of ~1.
    assert np.var(fat) == pytest.approx(1.0, rel=0.1)
    assert np.var(thin) == pytest.approx(1.0, rel=0.1)


def test_cross_asset_betas_and_correlations(series, params):
    cross = build_cross_asset(series, params)
    assert "ETH/BTC" in cross.correlations
    assert "MSTR/BTC" in cross.correlations
    for windows in cross.correlations.values():
        for value in windows.values():
            if value is not None:
                assert -1.0 <= value <= 1.0


def test_cross_asset_aligns_dates_not_positions(params):
    """MSTR has no weekend closes, so a positional zip would misalign the pairs."""
    n = 300
    dates = [date(2026, 8, 7) - timedelta(days=n - 1 - i) for i in range(n)]
    values = list(100 * np.exp(np.cumsum(np.full(n, 0.001))))

    btc = Series("BTC", dates, [float(v) for v in values], "test")
    weekdays = [(d, v) for d, v in zip(dates, values) if d.weekday() < 5]
    mstr = Series("MSTR", [d for d, _ in weekdays], [float(v) for _, v in weekdays], "test")

    cross = build_cross_asset({"BTC": btc, "MSTR": mstr}, params)
    # Identical prices on shared dates -> beta of exactly 1 once aligned.
    assert cross.betas["MSTR/BTC"] == pytest.approx(1.0, rel=1e-6)


def test_mnav_math(series, holdings, run_date):
    m = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
    expected_nav = holdings.btc_holdings * series["BTC"].last_close
    assert m.btc_nav == pytest.approx(expected_nav)
    assert m.nav_per_share == pytest.approx(expected_nav / holdings.diluted_shares)
    assert m.mnav == pytest.approx(m.mstr_price / m.nav_per_share)
    assert m.premium_pct == pytest.approx((m.mnav - 1) * 100)
    assert m.market_cap == pytest.approx(m.mstr_price * holdings.diluted_shares)


def test_mnav_premium_sign(series, holdings, run_date):
    m = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
    assert (m.mnav > 1) == (m.premium_pct > 0)


def test_mnav_percentile_rank_within_bounds(series, holdings, run_date):
    m = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
    assert m.history, "expected an mNAV history on overlapping dates"
    assert 0.0 <= m.percentile_rank <= 100.0


def test_stale_holdings_flagged(series, run_date):
    old = MstrHoldings(
        btc_holdings=650_000.0,
        diluted_shares=285_000_000.0,
        as_of=run_date - timedelta(days=120),
        verified=True,
        stale_after_days=45,
    )
    output = run_model(series, old, ModelParams(n_paths=500), run_date=run_date)
    assert output.mnav.stale
    assert any("days old" in w for w in output.warnings)


def test_unverified_holdings_flagged(series, run_date):
    unverified = MstrHoldings(
        btc_holdings=650_000.0,
        diluted_shares=285_000_000.0,
        as_of=run_date,
        verified=False,
    )
    output = run_model(series, unverified, ModelParams(n_paths=500), run_date=run_date)
    assert any("UNVERIFIED" in w for w in output.warnings)


def test_stale_price_data_warns(series, run_date):
    stale = {k: v for k, v in series.items()}
    btc = stale["BTC"]
    shifted = [d - timedelta(days=10) for d in btc.dates]
    stale["BTC"] = Series("BTC", shifted, btc.closes, "test")

    output = run_model(stale, None, ModelParams(n_paths=500), run_date=run_date)
    assert any("BTC data is" in w for w in output.warnings)


def test_weekend_lag_does_not_warn_for_equities(series, run_date):
    """MSTR closing on Friday is normal on a Monday run, and must not warn."""
    monday = run_date + timedelta(days=(7 - run_date.weekday()) % 7)
    output = run_model(series, None, ModelParams(n_paths=500), run_date=monday)
    assert not any(w.startswith("MSTR data is") for w in output.warnings)


def test_run_model_is_deterministic(series, holdings, run_date):
    p = ModelParams(n_paths=2000)
    a = run_model(series, holdings, p, run_date=run_date)
    b = run_model(series, holdings, p, run_date=run_date)
    assert (
        a.signals["BTC"].forecasts[0].percentiles
        == b.signals["BTC"].forecasts[0].percentiles
    )


def test_to_record_is_json_serializable(series, holdings, params, run_date):
    import json

    output = run_model(series, holdings, params, run_date=run_date)
    record = to_record(output)
    round_tripped = json.loads(json.dumps(record))
    assert round_tripped["run_date"] == run_date.isoformat()
    assert set(round_tripped["assets"]) == {"BTC", "ETH", "MSTR"}
    assert "1" in round_tripped["assets"]["BTC"]["forecasts"]
