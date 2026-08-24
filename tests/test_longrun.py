"""Five-year projection: adoption curve, halving cycle, share dynamics."""

from datetime import date

import numpy as np
import pytest

from pricemodel.longrun import Assumptions, adoption_trend, cycle_factor, project

START = date(2026, 8, 22)
SPOT = {"BTC": 72_944.28, "ETH": 2_425.44, "SOL": 93.45}
HOLDINGS, SHARES = 840_447.0, 364_580_000.0


def _y(d):
    return np.array([(d.toordinal() - START.toordinal()) / 365.25])


# --- adoption ------------------------------------------------------------


def test_growth_decays_as_it_approaches_saturation():
    """The whole point of a logistic curve: it is not an exponential."""
    a = Assumptions(saturation_mcap=25e12)
    spot_mcap = SPOT["BTC"] * a.btc_supply
    years = np.array([0.0, 2.5, 5.0, 7.5, 10.0])
    mcap = adoption_trend(a, spot_mcap, years)
    growth = np.diff(np.log(mcap))
    assert np.all(np.diff(growth) < 0), "each period must grow slower than the last"


def test_trend_never_exceeds_saturation():
    a = Assumptions(saturation_mcap=25e12)
    far = adoption_trend(a, SPOT["BTC"] * a.btc_supply, np.array([50.0]))[0]
    assert far <= a.saturation_mcap


def test_a_bigger_end_state_lifts_the_whole_path():
    spot_mcap = SPOT["BTC"] * Assumptions().btc_supply
    y = np.array([5.36])
    small = adoption_trend(Assumptions(saturation_mcap=15e12), spot_mcap, y)[0]
    big = adoption_trend(Assumptions(saturation_mcap=40e12), spot_mcap, y)[0]
    assert big > small


# --- cycle ---------------------------------------------------------------


def test_cycle_peaks_after_halvings_and_troughs_a_year_later():
    a = Assumptions()
    peak = cycle_factor(a, START, _y(date(2029, 10, 20)))[0]
    trough = cycle_factor(a, START, _y(date(2030, 10, 20)))[0]
    assert peak > 1.15 and trough < 0.85
    assert peak > trough


def test_cycle_places_today_near_a_low():
    """A sanity check against observed reality rather than against itself.

    August 2026 is ~28 months past the 2024 halving, which history puts near the
    cycle low - and bitcoin is in fact well off its high with MSTR at a discount
    to NAV. If the phasing did not reproduce that, it would be wrong.
    """
    assert cycle_factor(Assumptions(), START, _y(date(2026, 8, 22)))[0] < 0.85


def test_late_2031_is_mid_recovery_not_a_peak():
    """The finding that matters for a date chosen without reference to cycles."""
    f = cycle_factor(Assumptions(), START, _y(date(2031, 12, 31)))[0]
    assert 0.85 < f < 1.05


# --- share dynamics ------------------------------------------------------


def _run(**over):
    a = Assumptions(**over)
    return project(a, SPOT, HOLDINGS, SHARES, 0.709, START, 1957, n_paths=1500)


def test_preferred_dividends_dilute_bitcoin_per_share():
    """Funding a $1.55B dividend with equity buys no bitcoin, so it dilutes."""
    heavy = _run(preferred_dividend=6e9, accretive_issuance=0.0)
    light = _run(preferred_dividend=0.0, accretive_issuance=0.0)
    assert (
        heavy["quantiles"]["BTC_per_share"]["p50"]
        < light["quantiles"]["BTC_per_share"]["p50"]
    )


def test_issuing_above_one_times_nav_adds_bitcoin_per_share():
    none = _run(accretive_issuance=0.0, preferred_dividend=0.0)
    some = _run(accretive_issuance=0.05, preferred_dividend=0.0)
    assert (
        some["quantiles"]["BTC_per_share"]["p50"]
        > none["quantiles"]["BTC_per_share"]["p50"]
    )


def test_bitcoin_per_share_is_roughly_flat_on_the_shipped_assumptions():
    """Dividend drag and accretive issuance very nearly cancel.

    Worth pinning, because it means MSTR here is a levered bitcoin position plus
    a multiple - not a per-share compounder. Both halves have to be modelled to
    see that; holding the count fixed assumes the answer.
    """
    r = _run()
    start_bps = HOLDINGS / SHARES
    assert r["quantiles"]["BTC_per_share"]["p50"] == pytest.approx(start_bps, rel=0.05)


# --- alts ----------------------------------------------------------------


def test_alt_medians_track_btc_rather_than_compounding_a_beta():
    """Beta widens the distribution; it must not levitate the median.

    A long-horizon log-beta well above 1 would imply structural outperformance
    that alt/BTC ratios do not show, so the relative view lives in ratio drift.
    """
    r = _run()
    q = r["quantiles"]
    btc_x = q["BTC"]["p50"] / SPOT["BTC"]
    eth_x = q["ETH"]["p50"] / SPOT["ETH"]
    assert eth_x < btc_x * 1.25, "ETH median should not run far ahead of BTC's"


def test_alts_are_more_dispersed_than_btc():
    q = _run()["quantiles"]
    spread = lambda k: q[k]["p75"] / q[k]["p25"]
    assert spread("SOL") > spread("ETH") > spread("BTC")


# --- shape ---------------------------------------------------------------


def test_reversion_to_trend_tightens_the_five_year_fan():
    """Without a trend anchor a 5y fan is too wide to be worth reading."""
    loose = _run(reversion_halflife_days=100_000.0)["quantiles"]["BTC"]
    tight = _run(reversion_halflife_days=540.0)["quantiles"]["BTC"]
    assert tight["p75"] / tight["p25"] < loose["p75"] / loose["p25"]


def test_projection_still_carries_a_deep_drawdown():
    """Shallower is not shallow - the median path still halves at some point."""
    assert _run()["max_drawdown_median"] < -0.35


# --- crossing-date distribution -------------------------------------------

from datetime import timedelta  # noqa: E402

from pricemodel.longrun import crossing_dates  # noqa: E402


def _cross(target=523_000.0, **over):
    a = Assumptions(**over)
    return crossing_dates(a, SPOT, target, START, horizon_days=3600, n_paths=3000)


def test_confidence_windows_widen_with_confidence():
    """More confidence buys a wider window, never a sharper date."""
    w = _cross()["windows"]
    spans = {}
    for label in ("50%", "80%", "90%", "95%"):
        lo, hi = w[label]
        spans[label] = date.fromisoformat(hi) - date.fromisoformat(lo)
    assert spans["50%"] < spans["80%"] < spans["90%"] <= spans["95%"]


def test_no_single_day_carries_meaningful_probability():
    """The ceiling on single-date precision, stated numerically.

    A crossing spread over years cannot put more than a fraction of a percent
    on any one day. Any claim of high confidence in a specific date is
    therefore a claim about a window, whether or not it says so.
    """
    assert _cross()["best_single_day_prob"] < 0.01


def test_ninety_five_percent_window_spans_years():
    lo, hi = _cross()["windows"]["95%"]
    assert (date.fromisoformat(hi) - date.fromisoformat(lo)).days > 730


def test_a_higher_starting_price_pulls_the_crossing_forward():
    low = crossing_dates(Assumptions(), {"BTC": 60_000.0}, 523_000.0, START,
                         horizon_days=3600, n_paths=3000)
    high = crossing_dates(Assumptions(), {"BTC": 90_000.0}, 523_000.0, START,
                          horizon_days=3600, n_paths=3000)
    assert date.fromisoformat(high["median"]) < date.fromisoformat(low["median"])


def test_first_touch_precedes_the_central_path_crossing():
    """Two different questions, and the touch answer is always the earlier one.

    Volatility carries paths above trend, so half of them touch a level before
    the trend itself reaches it. Quoting one and labelling it the other
    overstates or understates the date by months.
    """
    a = Assumptions()
    years = np.arange(1, 3600 + 1) / 365.25
    central = (adoption_trend(a, SPOT["BTC"] * a.btc_supply, years) / a.btc_supply
               ) * cycle_factor(a, START, years)
    central_day = int(np.argmax(central >= 523_000.0)) + 1
    touch = date.fromisoformat(_cross()["median"])
    assert touch < START + timedelta(days=central_day)


def test_unreached_paths_are_counted_not_dropped():
    """Excluding failures would condition the answer on success."""
    r = _cross(target=50_000_000.0)
    assert r["prob_reached"] < 0.5
    assert r["median"] is None
    assert r["windows"]["95%"] is None


# --- portfolio crossing ---------------------------------------------------

from pricemodel.longrun import portfolio_crossing  # noqa: E402

POS = {"BTC": 13.37621385, "ETH": 17.73253442, "SOL": 155.8637964, "MSTR": 6512.003114}
PSPOT = {"BTC": 79_106.77, "ETH": 2_507.22, "SOL": 96.24, "MSTR": 125.0}


def _pf(target, **over):
    a = Assumptions(**over)
    return portfolio_crossing(a, PSPOT, POS, target, HOLDINGS, 382_840_000.0,
                              0.687, START, horizon_days=3300, n_paths=2500)


def test_start_value_is_the_marked_basket():
    r = _pf(20e6)
    expected = sum(POS[k] * PSPOT[k] for k in POS)
    assert r["start_value"] == pytest.approx(expected)


def test_bigger_targets_take_longer():
    dates = [_pf(t)["median"] for t in (5e6, 10e6, 20e6)]
    assert all(d is not None for d in dates)
    assert dates == sorted(dates)


def test_a_correlated_basket_beats_its_weakest_leg():
    """The basket is not the slowest asset waiting on the others.

    Every leg keys off bitcoin, so they rise together - which is also why the
    diversification here buys much less smoothing than four tickers suggest.
    """
    basket = _pf(20e6)
    sol_only = portfolio_crossing(
        Assumptions(), PSPOT, {"SOL": POS["SOL"]}, 20e6 * POS["SOL"] * PSPOT["SOL"]
        / sum(POS[k] * PSPOT[k] for k in POS), HOLDINGS, 382_840_000.0, 0.687,
        START, horizon_days=3300, n_paths=2500)
    assert basket["prob_reached"] >= sol_only["prob_reached"] - 0.15


def test_windows_widen_with_confidence():
    w = _pf(20e6)["windows"]
    span = lambda k: (date.fromisoformat(w[k][1]) - date.fromisoformat(w[k][0])).days
    assert span("50%") < span("80%") < span("95%")


def test_an_unreachable_target_reports_no_median():
    r = _pf(50e9)
    assert r["prob_reached"] < 0.5 and r["median"] is None


# --- equity sleeve --------------------------------------------------------


def _pf_eq(target, eq=1_210_567.40, **over):
    return portfolio_crossing(
        Assumptions(), PSPOT, POS, target, HOLDINGS, 382_840_000.0, 0.687,
        START, horizon_days=3300, n_paths=2500, equity_value=eq, **over)


def test_equity_sleeve_raises_start_value_and_pulls_the_date_forward():
    with_eq = _pf_eq(20e6)
    without = _pf(20e6)
    assert with_eq["start_value"] == pytest.approx(without["start_value"] + 1_210_567.40)
    assert date.fromisoformat(with_eq["median"]) < date.fromisoformat(without["median"])


def test_equity_sleeve_borrows_btc_wiggle_not_btc_drift():
    """The sleeve's own return must come from its own parameter alone.

    If the BTC coupling leaked drift, raising bitcoin's growth would raise the
    equity sleeve's median return too, and the sleeve would quietly become a
    second crypto position.
    """
    slow = _pf_eq(20e6, equity_median_cagr=0.0, equity_beta_to_btc=0.30)
    fast = _pf_eq(20e6, equity_median_cagr=0.15, equity_beta_to_btc=0.30)
    assert date.fromisoformat(fast["median"]) < date.fromisoformat(slow["median"])


def test_a_zero_equity_sleeve_changes_nothing():
    assert _pf_eq(20e6, eq=0.0)["median"] == _pf(20e6)["median"]
