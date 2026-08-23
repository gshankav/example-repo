"""Five-year projection built on adoption, cycles and share dynamics.

``target.py`` answers "how likely, by when" with geometric Brownian motion. Over
five years GBM has three flaws that dominate the answer, and this module fixes
each one:

**Growth decays; it does not compound forever.** A constant 45%/yr applied to a
$1.5T asset produces a $12T asset and never notices. Adoption is logistic, not
exponential: growth slows as the asset approaches whatever share of global
wealth it ends up holding. Parameterized by a saturation market cap you choose
and can defend, rather than a growth rate pulled from the air.

**Bitcoin has a four-year cycle.** Halvings in April 2028 and April 2032, with
history putting the peak 12-18 months after each and the trough roughly a year
after that. A projection to a fixed date is worth a lot more once you know where
in that cycle the date falls - and the answer for late 2031 is uncomfortable.

**MSTR's bitcoin-per-share is not constant.** Above 1x mNAV, issuing shares to
buy bitcoin raises it. Every year, $1.55B of preferred dividends has to be
funded, which lowers it. Holding both fixed - as the simpler model does - quietly
assumes the two cancel, and they do not.

Everything here is an assumption you can change. The point is that they are
visible and separable, not that they are right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

# Halving dates: April 2024 observed, then every ~4 years. History puts the cycle
# high 12-18 months after a halving and the low roughly 12 months after that.
HALVINGS = (date(2024, 4, 20), date(2028, 4, 20), date(2032, 4, 20))
PEAK_LAG_YEARS = 1.5
TROUGH_LAG_YEARS = 2.5


@dataclass
class Assumptions:
    """Every judgment call in the projection, in one place."""

    # Adoption
    saturation_mcap: float = 25e12   # where BTC stops compounding; gold is ~$20-25T
    intrinsic_growth: float = 0.50   # logistic r, the early-life growth rate
    btc_supply: float = 19_950_000.0

    # Cycle
    cycle_amplitude: float = 0.30    # peak/trough swing around the adoption trend

    # Risk
    sigma_start: float = 0.55
    sigma_end: float = 0.32
    vol_halflife_days: float = 550.0
    reversion_halflife_days: float = 540.0  # pull back toward the adoption trend
    df: float = 5.0

    # Alts, expressed against BTC rather than in isolation
    # Beta near 1 on the LEVEL, with the relative view carried by ratio drift.
    # A sustained log-beta of 1.3 would mean SOL structurally outperforms BTC by
    # ~2.2x over five years purely from amplification, which alt/BTC ratios do
    # not show over long horizons: high beta widens the distribution, it does
    # not lift the median. Amplification is a short-horizon effect; over a full
    # cycle the up-capture and down-capture largely cancel.
    eth_ratio_drift: float = -0.03   # ETH/BTC has trended down for years
    sol_ratio_drift: float = 0.01
    eth_beta: float = 1.05
    sol_beta: float = 1.10
    eth_idio_vol: float = 0.38
    sol_idio_vol: float = 0.55

    # MSTR
    mnav_target: float = 1.35
    mnav_halflife_days: float = 365.0
    mnav_sigma: float = 0.30
    preferred_dividend: float = 1.55e9
    accretive_issuance: float = 0.03  # of shares/yr, only while mNAV is above 1

    seed: int = 20240101


def _years_from(start: date, days: np.ndarray) -> np.ndarray:
    return days / 365.25


def adoption_trend(a: Assumptions, spot_mcap: float, years: np.ndarray) -> np.ndarray:
    """Logistic market-cap path: fast early, flattening as it saturates."""
    k = (a.saturation_mcap - spot_mcap) / spot_mcap
    return a.saturation_mcap / (1.0 + k * np.exp(-a.intrinsic_growth * years))


def cycle_factor(a: Assumptions, start: date, years: np.ndarray) -> np.ndarray:
    """Multiplicative cycle around the trend, phased to the halvings.

    Asymmetric on purpose: cycles spend longer grinding up than falling, so a
    symmetric sine would misplace the recovery leg - which is exactly where a
    late-2031 date lands.
    """
    out = np.zeros_like(years)
    for i, y in enumerate(years):
        d = start.toordinal() + y * 365.25
        # Phase relative to the most recent halving.
        prev = max([h for h in HALVINGS if h.toordinal() <= d], default=HALVINGS[0])
        phase = (d - prev.toordinal()) / 365.25
        if phase < PEAK_LAG_YEARS:            # run-up into the peak
            f = np.sin(np.pi * phase / (2 * PEAK_LAG_YEARS))
        elif phase < TROUGH_LAG_YEARS:        # sharp drop to the low
            frac = (phase - PEAK_LAG_YEARS) / (TROUGH_LAG_YEARS - PEAK_LAG_YEARS)
            f = np.cos(np.pi * frac)
        else:                                  # long recovery to the next halving
            frac = (phase - TROUGH_LAG_YEARS) / max(4.0 - TROUGH_LAG_YEARS, 0.1)
            f = -1.0 + frac
        out[i] = f
    return 1.0 + a.cycle_amplitude * out


def project(
    a: Assumptions,
    spot: dict[str, float],
    btc_holdings: float,
    shares: float,
    mnav_now: float,
    start: date,
    horizon_days: int,
    n_paths: int = 20_000,
) -> dict[str, Any]:
    """Simulate BTC, ETH, SOL and MSTR to the horizon and return the endpoints."""
    rng = np.random.default_rng(a.seed)
    days = np.arange(1, horizon_days + 1)
    years = _years_from(start, days)

    spot_mcap = spot["BTC"] * a.btc_supply
    trend_px = adoption_trend(a, spot_mcap, years) / a.btc_supply
    trend_px = trend_px * cycle_factor(a, start, years)
    log_trend = np.log(trend_px)

    decay = np.exp(-days * np.log(2.0) / a.vol_halflife_days)
    sigma = a.sigma_end + (a.sigma_start - a.sigma_end) * decay
    sigma_d = sigma / np.sqrt(365.25)
    t_scale = np.sqrt((a.df - 2.0) / a.df)

    # Price wanders around the adoption trend and is pulled back toward it. The
    # pull is what makes a five-year projection informative rather than a fan so
    # wide it says nothing - and it is the economic claim that adoption, not
    # momentum, sets the level in the long run.
    kappa = np.log(2.0) / a.reversion_halflife_days
    dev = np.zeros(n_paths)
    log_btc = np.empty((n_paths, horizon_days))
    shocks = rng.standard_t(a.df, size=(n_paths, horizon_days)) * t_scale
    for t in range(horizon_days):
        dev = dev * (1.0 - kappa) + shocks[:, t] * sigma_d[t]
        log_btc[:, t] = log_trend[t] + dev
    btc = np.exp(log_btc)

    # Alts as a ratio to BTC: the honest way to say "SOL outperforms" is a drift
    # on SOL/BTC, not a second free-standing forecast that silently implies one.
    btc_logret = np.diff(np.concatenate([np.full((n_paths, 1), np.log(spot["BTC"])),
                                         log_btc], axis=1), axis=1)
    alts = {}
    for key, beta, drift, idio in (
        ("ETH", a.eth_beta, a.eth_ratio_drift, a.eth_idio_vol),
        ("SOL", a.sol_beta, a.sol_ratio_drift, a.sol_idio_vol),
    ):
        noise = rng.standard_t(a.df, size=(n_paths, horizon_days)) * t_scale
        inc = (
            np.log1p(drift) / 365.25
            + beta * btc_logret
            + noise * (idio / np.sqrt(365.25))
        )
        alts[key] = spot[key] * np.exp(np.cumsum(inc, axis=1))

    # mNAV mean-reverts toward the sustainable level.
    km = np.log(2.0) / a.mnav_halflife_days
    ms = a.mnav_sigma / np.sqrt(365.25)
    log_m = np.full(n_paths, np.log(mnav_now))
    log_target = np.log(a.mnav_target)
    m_noise = rng.standard_normal((n_paths, horizon_days)) * ms
    mnav = np.empty((n_paths, horizon_days))
    for t in range(horizon_days):
        log_m = log_m + km * (log_target - log_m) + m_noise[:, t]
        mnav[:, t] = log_m
    mnav = np.exp(mnav)

    # Bitcoin per share evolves: preferred dividends dilute it every year, and
    # issuing above 1x mNAV to buy bitcoin adds to it. Stepped annually, which
    # is roughly how capital raising actually happens.
    bps = np.full(n_paths, btc_holdings / shares)
    sh = np.full(n_paths, shares)
    bps_path = np.empty((n_paths, horizon_days))
    for t in range(horizon_days):
        if t % 365 == 0 and t > 0:
            m = mnav[:, t]
            px = m * (bps * btc[:, t])
            mcap = px * sh
            # Fund the dividend with equity; it buys no bitcoin, so it dilutes.
            n_div = np.where(px > 0, a.preferred_dividend / np.maximum(px, 1e-9), 0.0)
            # Accretive issuance only makes sense above 1x.
            n_acc = np.where(m > 1.0, sh * a.accretive_issuance, 0.0)
            new_btc = np.where(m > 1.0, (px * n_acc) / btc[:, t], 0.0)
            bps = (bps * sh + new_btc) / (sh + n_div + n_acc)
            sh = sh + n_div + n_acc
        bps_path[:, t] = bps

    mstr = mnav * bps_path * btc
    end = {
        "BTC": btc[:, -1], "ETH": alts["ETH"][:, -1],
        "SOL": alts["SOL"][:, -1], "MSTR": mstr[:, -1],
        "mNAV": mnav[:, -1], "BTC_per_share": bps_path[:, -1],
    }
    peak = np.maximum.accumulate(btc, axis=1)
    return {
        "quantiles": {
            k: {f"p{p}": float(np.percentile(v, p)) for p in (5, 25, 50, 75, 95)}
            for k, v in end.items()
        },
        "prob_at_least": {},
        "end": end,
        "trend_end_price": float(trend_px[-1]),
        "cycle_at_end": float(cycle_factor(a, start, years[-1:])[0]),
        "max_drawdown_median": float(np.median((btc / peak - 1.0).min(axis=1))),
    }
