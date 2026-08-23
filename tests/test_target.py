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


# --- joint passage --------------------------------------------------------

from pricemodel.target import joint_passage  # noqa: E402

SPOT = {"BTC": 72_944.28, "ETH": 2_425.44, "SOL": 93.45}
SIG = {"BTC": 0.55, "ETH": 0.70, "SOL": 0.90}
BETA = {"ETH": 1.15, "SOL": 1.30}


def _run(barriers, cagr=0.5, mnav_target=1.75, paths=2500, days=1825):
    return joint_passage(
        spot=SPOT, barriers=barriers, sigma=SIG,
        median_cagr={k: cagr for k in SPOT}, beta_to_btc=BETA,
        btc_holdings=HOLDINGS, shares=SHARES, mnav_now=0.709,
        mnav_target=mnav_target, horizon_days=days, n_paths=paths,
    )


def test_joint_is_never_likelier_than_its_easiest_leg():
    """All four on one day cannot beat any single condition ever happening."""
    r = _run({"BTC": 585_000.0, "ETH": 11_000.0, "SOL": 450.0, "MSTR": 1550.0})
    assert r.prob_ever_joint <= min(r.prob_ever.values()) + 1e-9


def test_correlated_joint_beats_the_independent_product():
    """These assets move together, so multiplying marginals understates it.

    This is the reason for simulating them jointly rather than running three
    single-asset models and combining the answers.
    """
    b = {"BTC": 300_000.0, "ETH": 6_000.0, "SOL": 250.0}
    r = _run(b, paths=4000)
    product = r.prob_ever["BTC"] * r.prob_ever["ETH"] * r.prob_ever["SOL"]
    assert r.prob_ever_joint > product


def test_mstr_inherits_the_btc_path_rather_than_drifting_alone():
    """With the multiple pinned near 1x, MSTR must track gross NAV per share."""
    nav_barrier = 1.0 * (HOLDINGS * 585_000.0 / SHARES)
    r = joint_passage(
        spot=SPOT, barriers={"BTC": 585_000.0, "MSTR": nav_barrier}, sigma=SIG,
        median_cagr={k: 0.5 for k in SPOT}, beta_to_btc=BETA,
        btc_holdings=HOLDINGS, shares=SHARES, mnav_now=1.0, mnav_target=1.0,
        mnav_sigma_annual=0.01, mnav_halflife_days=30.0,
        horizon_days=1825, n_paths=3000,
    )
    assert r.prob_ever["MSTR"] == pytest.approx(r.prob_ever["BTC"], abs=0.03)


def test_a_higher_sustained_multiple_pulls_the_mstr_date_forward():
    low = _run({"MSTR": 1550.0}, mnav_target=1.2)
    high = _run({"MSTR": 1550.0}, mnav_target=2.5)
    assert high.prob_ever["MSTR"] > low.prob_ever["MSTR"]


def test_the_multiple_barely_moves_the_joint_date():
    """The binding constraint is BTC, not the rerating.

    Between 1.5x and 2.0x the MSTR leg moves a lot and the joint date hardly
    does - which is the finding that matters when someone proposes to reach a
    joint target by rerating alone.
    """
    b = {"BTC": 585_000.0, "ETH": 11_000.0, "SOL": 450.0, "MSTR": 1550.0}
    lo, hi = _run(b, mnav_target=1.5, paths=4000), _run(b, mnav_target=2.0, paths=4000)
    assert abs(hi.prob_ever_joint - lo.prob_ever_joint) < 0.06
    assert hi.prob_ever["MSTR"] > lo.prob_ever["MSTR"]


def test_btc_585k_is_the_binding_leg_of_this_particular_basket():
    r = _run({"BTC": 585_000.0, "ETH": 11_000.0, "SOL": 450.0, "MSTR": 1550.0},
             paths=4000, days=2555)
    assert r.prob_ever["BTC"] < r.prob_ever["ETH"]
    assert r.prob_ever["BTC"] < r.prob_ever["SOL"]


def test_joint_median_is_none_below_even_odds():
    r = _run({"BTC": 5_000_000.0}, cagr=0.0, paths=2000)
    assert r.prob_ever_joint < 0.5 and r.median_day_joint is None


# --- maturation and adoption jumps ----------------------------------------


def _mature(**over):
    b = {"BTC": 585_000.0, "ETH": 11_000.0, "SOL": 450.0, "MSTR": 1550.0}
    kw = dict(
        spot=SPOT, barriers=b, sigma=SIG, median_cagr={k: 0.5 for k in SPOT},
        beta_to_btc=BETA, btc_holdings=HOLDINGS, shares=SHARES, mnav_now=0.709,
        mnav_target=1.35, horizon_days=2555, n_paths=3000,
    )
    kw.update(over)
    return joint_passage(**kw)


MATURING = dict(
    sigma_terminal={"BTC": 0.32, "ETH": 0.42, "SOL": 0.55},
    vol_halflife_days=550, df_terminal=8.0,
)


def test_maturation_makes_drawdowns_shallower():
    """The thesis's own claim, stated so the model can be checked against it."""
    base = _mature().btc_max_drawdown
    mature = _mature(**MATURING).btc_max_drawdown
    assert mature["median"] > base["median"]      # less negative
    assert mature["p5_worst"] > base["p5_worst"]


def test_maturation_delays_arrival_at_a_distant_barrier():
    """The counterintuitive half, and the reason to model it rather than assume.

    Damping volatility removes the fast paths as well as the deep drawdowns, so
    a thesis that makes holding easier also makes a deadline harder. Anyone
    treating 'adoption reduces volatility' as unambiguously bullish for a
    dated upside target has this backwards.
    """
    base = _mature()
    mature = _mature(**MATURING)
    by = lambda r: min(r.curve_joint, key=lambda kv: abs(kv[0] - 1773))[1]
    assert by(mature) < by(base)


def test_adoption_jumps_buy_speed_back_at_the_cost_of_drawdown():
    """Speed and shallow drawdowns are the same dial, not two.

    Jumps are drift-compensated, so this is a pure path-shape comparison at an
    unchanged expected return: the jumpy path arrives sooner and drops harder.
    """
    smooth = _mature(**MATURING)
    jumpy = _mature(**MATURING, jump_intensity_annual=1.0,
                    jump_mean_log=0.30, jump_sigma_log=0.15)
    by = lambda r: min(r.curve_joint, key=lambda kv: abs(kv[0] - 1773))[1]
    assert by(jumpy) > by(smooth)
    assert jumpy.btc_max_drawdown["median"] < smooth.btc_max_drawdown["median"]


def test_jumps_do_not_smuggle_in_extra_return():
    """Drift compensation keeps total expected growth unchanged.

    Without it, 'modelling adoption' would just be adding return and calling
    the resulting earlier date a structural insight.
    """
    smooth = _mature(**MATURING, barriers={"BTC": 120_000.0})
    jumpy = _mature(**MATURING, barriers={"BTC": 120_000.0},
                    jump_intensity_annual=1.0, jump_mean_log=0.30, jump_sigma_log=0.15)
    assert jumpy.prob_ever["BTC"] == pytest.approx(smooth.prob_ever["BTC"], abs=0.06)
