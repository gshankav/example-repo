"""Target-price inversion and first-passage distributions."""

from datetime import date

import pytest

from pricemodel.target import (
    first_passage,
    required_btc_price,
    required_cagr,
    target_table,
)

HOLDINGS, SHARES = 840_447.0, 364_580_000.0


# --- the assumption-free half ---------------------------------------------


def test_required_btc_price_inverts_the_valuation_identity():
    """Feeding the answer back through the valuation must return the target."""
    target, mnav = 1550.0, 2.0
    req = required_btc_price(target, mnav, HOLDINGS, SHARES)
    implied_mstr = mnav * (HOLDINGS * req / SHARES)
    assert implied_mstr == pytest.approx(target)


def test_required_btc_price_scales_inversely_with_the_multiple():
    a = required_btc_price(1550.0, 1.0, HOLDINGS, SHARES)
    b = required_btc_price(1550.0, 2.0, HOLDINGS, SHARES)
    assert b == pytest.approx(a / 2.0)


def test_more_shares_means_a_higher_required_btc_price():
    """Dilution raises the bar - the target is a per-share price."""
    tight = required_btc_price(1550.0, 1.0, HOLDINGS, SHARES)
    diluted = required_btc_price(1550.0, 1.0, HOLDINGS, SHARES * 1.5)
    assert diluted > tight


@pytest.mark.parametrize("bad", [(0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, 0)])
def test_required_btc_price_rejects_non_positive_inputs(bad):
    with pytest.raises(ValueError):
        required_btc_price(*bad)


def test_required_cagr_round_trips():
    g = required_cagr(100.0, 200.0, 3.0)
    assert 100.0 * (1 + g) ** 3 == pytest.approx(200.0)


def test_required_cagr_falls_as_the_horizon_lengthens():
    rates = [required_cagr(72_944.0, 672_379.0, y) for y in (1, 2, 3, 5, 10)]
    assert rates == sorted(rates, reverse=True)
    assert rates[0] > 5.0  # a 9x in one year is >500%/yr, by construction


def test_target_table_covers_every_multiple():
    rows = target_table(1550.0, 72_944.28, HOLDINGS, SHARES, (1.0, 2.0), (1, 3))
    assert [r["mnav"] for r in rows] == [1.0, 2.0]
    assert set(rows[0]["cagr"]) == {1, 3}
    assert rows[0]["multiple_of_spot"] > rows[1]["multiple_of_spot"]


# --- the modelled half ----------------------------------------------------


def test_barrier_already_reached_is_certain():
    r = first_passage(100.0, 50.0, 0.5, 0.0, 365)
    assert r.prob_ever == 1.0 and r.median_day == 0


def test_probability_is_monotonic_in_time():
    r = first_passage(100.0, 200.0, 0.6, 0.0, 730, n_paths=4000)
    probs = [p for _, p in r.curve]
    assert probs == sorted(probs)
    assert 0.0 <= probs[-1] <= 1.0


def test_a_nearer_barrier_is_likelier():
    near = first_passage(100.0, 150.0, 0.6, 0.0, 730, n_paths=4000)
    far = first_passage(100.0, 500.0, 0.6, 0.0, 730, n_paths=4000)
    assert near.prob_ever > far.prob_ever


def test_more_drift_and_more_volatility_both_help_reach_an_upside_barrier():
    base = first_passage(100.0, 300.0, 0.5, 0.0, 1095, n_paths=4000)
    drifty = first_passage(100.0, 300.0, 0.5, 0.5, 1095, n_paths=4000)
    wild = first_passage(100.0, 300.0, 0.9, 0.0, 1095, n_paths=4000)
    assert drifty.prob_ever > base.prob_ever
    assert wild.prob_ever > base.prob_ever


def test_median_date_is_none_when_most_paths_never_arrive():
    """A median hit date only exists if over half the paths actually hit.

    Reporting one anyway - the median of the subset that made it - is how a
    model gets quoted as predicting a date it assigns under 50% odds to.
    """
    r = first_passage(100.0, 10_000.0, 0.4, 0.0, 365, n_paths=4000)
    assert r.prob_ever < 0.5
    assert r.median_day is None
    assert r.date_for(date(2026, 8, 22), r.median_day) == "never"


def test_running_max_beats_endpoint_only():
    """Touching a barrier is strictly likelier than closing above it."""
    spot, barrier = 100.0, 200.0
    r = first_passage(spot, barrier, 0.6, 0.0, 730, n_paths=6000)
    import numpy as np

    rng = np.random.default_rng(20240101)
    sigma_d = 0.6 / np.sqrt(365.0)
    shocks = rng.standard_t(4.0, size=(6000, 730)) * np.sqrt(0.5) * sigma_d
    ends = spot * np.exp(np.cumsum(shocks, axis=1)[:, -1])
    assert r.prob_ever > float((ends >= barrier).mean())


def test_results_are_deterministic_for_a_seed():
    a = first_passage(100.0, 300.0, 0.55, 0.25, 730, n_paths=3000)
    b = first_passage(100.0, 300.0, 0.55, 0.25, 730, n_paths=3000)
    assert a.prob_ever == b.prob_ever and a.median_day == b.median_day


def test_mstr_1550_is_a_tail_outcome_on_neutral_assumptions():
    """The headline finding, pinned so it cannot rot.

    At a 2x multiple - already a large rerating from today's 0.71x - and BTC
    compounding at 25% a year, touching $1,550 inside three years is well under
    even odds. Any confident near-term date contradicts this.
    """
    req = required_btc_price(1550.0, 2.0, HOLDINGS, SHARES)
    r = first_passage(72_944.28, req, 0.55, 0.25, 1095, n_paths=8000)
    assert r.prob_ever < 0.5
