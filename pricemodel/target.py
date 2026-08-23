"""What has to be true for MSTR to reach a target price, and how likely is it.

This module exists because "when will MSTR hit $X" is not a question a model can
answer. It has no answer of the form "March 14th". What it does have is three
answers that are real, in increasing order of how much you have to assume:

1. **Required BTC price** - pure algebra, no assumptions at all. MSTR's price is
   ``mNAV x (BTC held x BTC price / shares)``, so a target price and an assumed
   multiple pin the BTC price exactly. Nothing here is forecast.

2. **Required growth rate** - the CAGR bitcoin must compound at to get there by
   a given date. Still assumption-free: it is the first answer divided by time.
   This is usually the number that ends the conversation, because it converts a
   plausible-sounding target into an implied rate of return you can judge.

3. **Probability by date** - the only genuinely modelled step, and the one that
   needs a volatility and a drift you have to supply and defend. Reported as a
   distribution over dates, never as a date, because the distribution is what
   the model actually knows.

The barrier is on the running maximum, not the endpoint: a path that touches the
target and falls back still counts as having touched it, which is the right
question for "when does it get there" and gives a materially higher probability
than asking where the price ends up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np


@dataclass
class PassageResult:
    """Distribution of the first date a barrier is touched."""

    barrier: float
    spot: float
    prob_ever: float
    median_day: int | None
    p10_day: int | None
    p90_day: int | None
    curve: list[tuple[int, float]]  # (day, cumulative probability of touching)

    def date_for(self, start: date, day: int | None) -> str:
        return (start + timedelta(days=day)).isoformat() if day is not None else "never"


def required_btc_price(
    target_mstr: float, mnav: float, btc_holdings: float, shares: float
) -> float:
    """BTC price at which MSTR trades at ``target_mstr``, given a multiple.

    Straight inversion of ``mstr = mnav x btc_holdings x btc_price / shares``.
    No forecast, no assumption beyond the multiple and the share count.
    """
    if min(target_mstr, mnav, btc_holdings, shares) <= 0:
        raise ValueError("target, multiple, holdings and shares must be positive")
    return target_mstr * shares / (mnav * btc_holdings)


def required_cagr(spot: float, target: float, years: float) -> float:
    """Compound annual growth needed to get from spot to target in ``years``."""
    if spot <= 0 or target <= 0 or years <= 0:
        raise ValueError("spot, target and years must be positive")
    return (target / spot) ** (1.0 / years) - 1.0


def first_passage(
    spot: float,
    barrier: float,
    sigma_annual: float,
    median_cagr: float,
    horizon_days: int,
    n_paths: int = 20_000,
    df: float = 4.0,
    seed: int = 20240101,
    ann_factor: float = 365.0,
) -> PassageResult:
    """Monte Carlo distribution of the first day ``spot`` touches ``barrier``.

    ``median_cagr`` is the growth rate of the *median* path, which is the
    interpretable parameter: for a lognormal, the median grows at exp(mu t), so
    mu = ln(1 + cagr). Quoting drift this way avoids the usual confusion where a
    "10% drift" silently means something different from 10% price growth.

    Innovations are Student-t rescaled to unit variance, matching the forecast
    engine in ``model.py`` - the fat tails matter more here than in a 30-day
    forecast, because reaching a distant barrier depends almost entirely on the
    tail of the distribution rather than its middle.
    """
    if spot <= 0 or barrier <= 0:
        raise ValueError("spot and barrier must be positive")
    if barrier <= spot:
        # Already there; the question is not meaningful.
        return PassageResult(barrier, spot, 1.0, 0, 0, 0, [(0, 1.0)])

    rng = np.random.default_rng(seed)
    sigma_daily = sigma_annual / np.sqrt(ann_factor)
    mu_daily = np.log1p(median_cagr) / ann_factor

    t_scale = np.sqrt((df - 2.0) / df)
    shocks = rng.standard_t(df, size=(n_paths, horizon_days)) * t_scale * sigma_daily
    log_paths = np.cumsum(mu_daily + shocks, axis=1)

    # Running maximum: a path counts as having touched the barrier on the first
    # day its high reaches it, even if it closes lower afterwards.
    running_max = np.maximum.accumulate(log_paths, axis=1)
    hit = running_max >= np.log(barrier / spot)

    ever = hit[:, -1]
    prob_ever = float(ever.mean())
    first = np.where(ever, hit.argmax(axis=1) + 1, -1)
    hit_days = first[first > 0]

    def pct(p: float) -> int | None:
        # Only defined when that share of *all* paths got there; otherwise the
        # honest answer is that the quantile lies beyond the horizon.
        if prob_ever < p / 100.0:
            return None
        return int(np.percentile(hit_days, p / prob_ever)) if len(hit_days) else None

    step = max(1, horizon_days // 60)
    curve = [
        (d, float(hit[:, d - 1].mean())) for d in range(step, horizon_days + 1, step)
    ]
    return PassageResult(
        barrier=barrier,
        spot=spot,
        prob_ever=prob_ever,
        median_day=pct(50),
        p10_day=pct(10),
        p90_day=pct(90),
        curve=curve,
    )


def target_table(
    target_mstr: float,
    btc_spot: float,
    btc_holdings: float,
    shares: float,
    multiples: tuple[float, ...],
    horizons_years: tuple[float, ...],
) -> list[dict[str, Any]]:
    """The assumption-free half: required BTC price and CAGR per multiple."""
    rows = []
    for m in multiples:
        req = required_btc_price(target_mstr, m, btc_holdings, shares)
        rows.append(
            {
                "mnav": m,
                "required_btc": req,
                "multiple_of_spot": req / btc_spot,
                "cagr": {y: required_cagr(btc_spot, req, y) for y in horizons_years},
            }
        )
    return rows


@dataclass
class JointResult:
    """First passage for several correlated targets, and for all of them at once."""

    prob_ever: dict[str, float]
    median_day: dict[str, int | None]
    prob_ever_joint: float
    median_day_joint: int | None
    curve_joint: list[tuple[int, float]]
    mnav_at_joint: float | None


def joint_passage(
    spot: dict[str, float],
    barriers: dict[str, float],
    sigma: dict[str, float],
    median_cagr: dict[str, float],
    beta_to_btc: dict[str, float],
    btc_holdings: float,
    shares: float,
    mnav_now: float,
    mnav_target: float,
    mnav_halflife_days: float = 365.0,
    mnav_sigma_annual: float = 0.45,
    horizon_days: int = 2555,
    n_paths: int = 20_000,
    df: float = 4.0,
    seed: int = 20240101,
    ann_factor: float = 365.0,
) -> JointResult:
    """First passage for BTC/ETH/SOL and a derived MSTR, simulated jointly.

    Three things matter here that a per-asset model gets wrong:

    **Correlation.** Running each asset independently and multiplying the
    probabilities badly understates the joint event, because these assets move
    together. ETH and SOL are driven by a beta to the BTC shock plus their own
    idiosyncratic noise, sized so each keeps its total volatility.

    **MSTR is not an independent asset.** It is ``mNAV x BTC NAV per share``, so
    it inherits BTC's path exactly and adds the multiple's own dynamics. Giving
    it a free-standing drift would double-count the bitcoin it already holds.

    **The multiple mean-reverts.** A premium is not a random walk - it is pulled
    toward whatever level the market will sustain, which is the whole content of
    a claim like "1.5-2x is the sustainable range". Modelled as an
    Ornstein-Uhlenbeck process in log space with a half-life, so the multiple
    wanders around the target rather than sitting on it or drifting away forever.
    """
    # BTC is the common factor driving everything else - including MSTR, which
    # is BTC NAV per share times a multiple - so it is always simulated, whether
    # or not the caller set a barrier on it.
    keys = [k for k in ("BTC", "ETH", "SOL") if k in barriers or k == "BTC"]
    barrier_keys = [k for k in keys if k in barriers]
    rng = np.random.default_rng(seed)
    t_scale = np.sqrt((df - 2.0) / df)

    def shocks(vol_annual: float) -> np.ndarray:
        v = vol_annual / np.sqrt(ann_factor)
        return rng.standard_t(df, size=(n_paths, horizon_days)) * t_scale * v

    btc_shock = shocks(sigma["BTC"])
    paths: dict[str, np.ndarray] = {}
    for k in keys:
        mu = np.log1p(median_cagr[k]) / ann_factor
        if k == "BTC":
            inc = mu + btc_shock
        else:
            beta = beta_to_btc.get(k, 1.0)
            # Split total variance into the part BTC explains and the rest, so
            # raising beta changes correlation without inflating the asset's vol.
            resid_var = sigma[k] ** 2 - (beta * sigma["BTC"]) ** 2
            idio = shocks(np.sqrt(resid_var)) if resid_var > 0 else 0.0
            inc = mu + beta * btc_shock + idio
        paths[k] = spot[k] * np.exp(np.cumsum(inc, axis=1))

    # mNAV as an OU process in log space, pulled toward the sustainable level.
    kappa = np.log(2.0) / max(mnav_halflife_days, 1.0)
    m_sigma = mnav_sigma_annual / np.sqrt(ann_factor)
    log_m = np.full(n_paths, np.log(mnav_now))
    log_target = np.log(mnav_target)
    m_shocks = rng.standard_normal((n_paths, horizon_days)) * m_sigma
    mnav_path = np.empty((n_paths, horizon_days))
    for t in range(horizon_days):
        log_m = log_m + kappa * (log_target - log_m) + m_shocks[:, t]
        mnav_path[:, t] = log_m
    mnav_path = np.exp(mnav_path)

    mstr = mnav_path * (btc_holdings * paths["BTC"] / shares)

    hits = {
        k: np.maximum.accumulate(paths[k], axis=1) >= barriers[k] for k in barrier_keys
    }
    if "MSTR" in barriers:
        hits["MSTR"] = np.maximum.accumulate(mstr, axis=1) >= barriers["MSTR"]

    # Joint: every condition true on the SAME day, not each at some point.
    same_day = np.ones((n_paths, horizon_days), dtype=bool)
    for k in barrier_keys:
        same_day &= paths[k] >= barriers[k]
    if "MSTR" in barriers:
        same_day &= mstr >= barriers["MSTR"]
    joint = np.maximum.accumulate(same_day, axis=1)

    def summarize(mask: np.ndarray) -> tuple[float, int | None]:
        ever = mask[:, -1]
        p = float(ever.mean())
        if p < 0.5:
            return p, None
        days = np.where(ever, mask.argmax(axis=1) + 1, -1)
        return p, int(np.percentile(days[days > 0], 50.0 / p))

    prob_ever, median_day = {}, {}
    for k, mask in hits.items():
        prob_ever[k], median_day[k] = summarize(mask)
    p_joint, d_joint = summarize(joint)

    step = max(1, horizon_days // 80)
    curve = [(d, float(joint[:, d - 1].mean())) for d in range(step, horizon_days + 1, step)]

    mnav_at = None
    if d_joint is not None:
        reached = same_day[:, d_joint - 1]
        if reached.any():
            mnav_at = float(np.median(mnav_path[reached, d_joint - 1]))

    return JointResult(prob_ever, median_day, p_joint, d_joint, curve, mnav_at)
