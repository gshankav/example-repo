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
