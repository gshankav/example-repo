"""The model layer: per-asset signals, cross-asset stats, forecasts, MSTR mNAV."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from . import indicators as ind
from .config import ASSETS_BY_KEY, Asset, ModelParams, MstrHoldings
from .data import Series, align


@dataclass
class Forecast:
    horizon_days: int
    percentiles: dict[int, float]
    prob_up: float
    expected_move_pct: float


@dataclass
class AssetSignals:
    key: str
    name: str
    price: float
    as_of: date
    source: str
    observations: int

    change_1d: float | None = None
    change_7d: float | None = None
    change_30d: float | None = None
    change_90d: float | None = None
    change_365d: float | None = None

    sma: dict[int, float | None] = field(default_factory=dict)
    price_vs_sma: dict[int, float | None] = field(default_factory=dict)
    vol: dict[int, float | None] = field(default_factory=dict)
    ewma_vol: float = 0.0
    rsi: float | None = None
    zscore_200: float | None = None
    drawdown: float | None = None
    trailing_high: float | None = None
    regime: str = "unknown"
    forecasts: list[Forecast] = field(default_factory=list)

    @property
    def rsi_label(self) -> str:
        if self.rsi is None:
            return "n/a"
        if self.rsi >= 70:
            return "overbought"
        if self.rsi <= 30:
            return "oversold"
        return "neutral"


@dataclass
class MnavResult:
    btc_price: float
    mstr_price: float
    btc_holdings: float
    diluted_shares: float
    btc_nav: float
    nav_per_share: float
    mnav: float
    premium_pct: float
    market_cap: float
    as_of: date
    verified: bool
    stale: bool
    days_old: int
    source: str
    history: list[tuple[date, float]] = field(default_factory=list)

    @property
    def percentile_rank(self) -> float | None:
        """Where today's mNAV sits within its own trailing history, 0-100."""
        if len(self.history) < 30:
            return None
        values = np.array([v for _, v in self.history])
        return float((values < self.mnav).mean() * 100.0)


@dataclass
class CrossAsset:
    correlations: dict[str, dict[int, float | None]]
    betas: dict[str, float | None]


@dataclass
class ModelOutput:
    run_date: date
    signals: dict[str, AssetSignals]
    cross: CrossAsset
    mnav: MnavResult | None
    warnings: list[str] = field(default_factory=list)


def _forecast(
    closes: np.ndarray,
    asset: Asset,
    params: ModelParams,
    rng: np.random.Generator,
) -> list[Forecast]:
    """Monte Carlo forward price distribution.

    Innovations are Student-t rather than normal because crypto daily returns
    have far fatter tails than a Gaussian. The draws are rescaled to unit
    variance so ``sigma_daily`` keeps its meaning, which means the t and the
    normal agree closely around the 5th/95th percentiles - a standardized t
    actually has slightly *thinner* shoulders. The difference shows up further
    out: the probability of a genuine 5-plus-sigma day, which a Gaussian model
    treats as essentially impossible and which crypto delivers regularly. So
    this choice is about not understating crash and melt-up risk, not about
    widening the headline band.
    """
    rets = ind.log_returns(closes)
    spot = float(closes[-1])

    sigma_annual = ind.ewma_vol(rets, params.ewma_lambda, asset.ann_factor)
    realized = ind.realized_vol(rets, params.vol_windows[0], asset.ann_factor)
    if realized is not None:
        # Blend the fast EWMA estimate with a slower window so a single violent
        # day does not dominate the whole forward distribution.
        sigma_annual = 0.6 * sigma_annual + 0.4 * realized
    sigma_daily = sigma_annual / np.sqrt(asset.ann_factor)

    # Drift: trailing mean return, shrunk hard toward zero and capped. Trailing
    # drift has very little predictive power, so it nudges the distribution
    # rather than steering it.
    window = min(params.drift_window, len(rets))
    raw_daily_drift = float(np.mean(rets[-window:])) if window > 0 else 0.0
    daily_drift = raw_daily_drift * params.drift_shrinkage
    cap = params.max_annual_drift / asset.ann_factor
    daily_drift = float(np.clip(daily_drift, -cap, cap))

    df = params.student_t_df
    # Scale the t draws to unit variance so sigma_daily keeps its meaning.
    t_scale = np.sqrt((df - 2.0) / df)

    out: list[Forecast] = []
    max_h = max(params.horizons)
    shocks = rng.standard_t(df, size=(params.n_paths, max_h)) * t_scale * sigma_daily
    # Subtract half the variance so the simulated median tracks the drift
    # instead of drifting up from Jensen's inequality on the exponential.
    increments = (daily_drift - 0.5 * sigma_daily**2) + shocks
    log_paths = np.cumsum(increments, axis=1)

    for h in params.horizons:
        if h > max_h:
            continue
        terminal = spot * np.exp(log_paths[:, h - 1])
        pct = {p: float(np.percentile(terminal, p)) for p in params.percentiles}
        out.append(
            Forecast(
                horizon_days=h,
                percentiles=pct,
                prob_up=float((terminal > spot).mean()),
                expected_move_pct=float(sigma_daily * np.sqrt(h) * 100.0),
            )
        )
    return out


def build_signals(series: Series, params: ModelParams) -> AssetSignals:
    asset = ASSETS_BY_KEY[series.key]
    closes = np.asarray(series.closes, dtype=float)
    rets = ind.log_returns(closes)

    sig = AssetSignals(
        key=asset.key,
        name=asset.name,
        price=float(closes[-1]),
        as_of=series.last_date,
        source=series.source,
        observations=len(closes),
    )

    for label, lookback in (
        ("change_1d", 1),
        ("change_7d", 7),
        ("change_30d", 30),
        ("change_90d", 90),
        ("change_365d", 365),
    ):
        setattr(sig, label, ind.momentum(closes, lookback))

    for w in params.sma_windows:
        value = ind.sma(closes, w)
        sig.sma[w] = value
        sig.price_vs_sma[w] = (sig.price / value - 1.0) if value else None

    for w in params.vol_windows:
        sig.vol[w] = ind.realized_vol(rets, w, asset.ann_factor)

    sig.ewma_vol = ind.ewma_vol(rets, params.ewma_lambda, asset.ann_factor)
    sig.rsi = ind.rsi(closes, params.rsi_period)
    sig.zscore_200 = ind.zscore(closes, 200)

    dd = ind.drawdown_from_high(closes, 365)
    if dd:
        sig.drawdown, sig.trailing_high = dd

    sig.regime = ind.regime(sig.price, sig.sma.get(50), sig.sma.get(200))
    return sig


def build_cross_asset(series: dict[str, Series], params: ModelParams) -> CrossAsset:
    """Correlations and BTC-betas, computed on date-aligned returns."""
    correlations: dict[str, dict[int, float | None]] = {}
    betas: dict[str, float | None] = {}

    btc = series.get("BTC")
    for key in ("ETH", "MSTR"):
        other = series.get(key)
        if btc is None or other is None:
            continue
        a_closes, b_closes = align(btc, other)
        if len(a_closes) < 5:
            continue
        btc_rets = ind.log_returns(np.asarray(a_closes, dtype=float))
        other_rets = ind.log_returns(np.asarray(b_closes, dtype=float))
        label = f"{key}/BTC"
        correlations[label] = {
            w: ind.correlation(other_rets, btc_rets, w) for w in params.corr_windows
        }
        betas[label] = ind.beta(other_rets, btc_rets, params.beta_window)

    if "ETH" in series and "MSTR" in series:
        a_closes, b_closes = align(series["ETH"], series["MSTR"])
        if len(a_closes) >= 5:
            eth_rets = ind.log_returns(np.asarray(a_closes, dtype=float))
            mstr_rets = ind.log_returns(np.asarray(b_closes, dtype=float))
            correlations["MSTR/ETH"] = {
                w: ind.correlation(mstr_rets, eth_rets, w) for w in params.corr_windows
            }

    return CrossAsset(correlations=correlations, betas=betas)


def compute_mnav(
    btc: Series,
    mstr: Series,
    holdings: MstrHoldings,
    run_date: date,
    history_days: int = 180,
) -> MnavResult:
    """MSTR's market cap relative to the market value of its bitcoin.

    mNAV above 1 means the equity trades at a premium to the bitcoin backing it.
    The historical series holds today's BTC count and share count constant, so
    it shows how the *premium* moved with BTC's price rather than reconstructing
    the true historical treasury; it is a comparison aid, not a restatement.
    """
    btc_price = btc.last_close
    mstr_price = mstr.last_close

    btc_nav = holdings.btc_holdings * btc_price
    nav_per_share = btc_nav / holdings.diluted_shares
    mnav = mstr_price / nav_per_share
    market_cap = mstr_price * holdings.diluted_shares

    btc_map = dict(zip(btc.dates, btc.closes))
    history: list[tuple[date, float]] = []
    for d, px in zip(mstr.dates[-history_days:], mstr.closes[-history_days:]):
        btc_px = btc_map.get(d)
        if btc_px is None:
            continue
        nav_ps = (holdings.btc_holdings * btc_px) / holdings.diluted_shares
        if nav_ps > 0:
            history.append((d, px / nav_ps))

    return MnavResult(
        btc_price=btc_price,
        mstr_price=mstr_price,
        btc_holdings=holdings.btc_holdings,
        diluted_shares=holdings.diluted_shares,
        btc_nav=btc_nav,
        nav_per_share=nav_per_share,
        mnav=mnav,
        premium_pct=(mnav - 1.0) * 100.0,
        market_cap=market_cap,
        as_of=holdings.as_of,
        verified=holdings.verified,
        stale=holdings.is_stale(run_date),
        days_old=holdings.days_old(run_date),
        source=holdings.source,
        history=history,
    )


def run_model(
    series: dict[str, Series],
    holdings: MstrHoldings | None,
    params: ModelParams,
    run_date: date | None = None,
) -> ModelOutput:
    run_date = run_date or date.today()
    rng = np.random.default_rng(params.seed)
    warnings: list[str] = []

    signals: dict[str, AssetSignals] = {}
    for key, s in series.items():
        sig = build_signals(s, params)
        sig.forecasts = _forecast(
            np.asarray(s.closes, dtype=float), ASSETS_BY_KEY[key], params, rng
        )
        signals[key] = sig

        staleness = (run_date - s.last_date).days
        # Equities legitimately lag over weekends and holidays; crypto does not.
        limit = 5 if ASSETS_BY_KEY[key].kind == "equity" else 2
        if staleness > limit:
            warnings.append(
                f"{key} data is {staleness} days old (last close {s.last_date}, "
                f"source {s.source})."
            )

    cross = build_cross_asset(series, params)

    mnav = None
    if holdings and "BTC" in series and "MSTR" in series:
        mnav = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
        if not mnav.verified:
            warnings.append(
                f"MSTR treasury figures are UNVERIFIED ({mnav.source}, as of "
                f"{mnav.as_of}) - confirm against the latest 8-K/10-Q before "
                "relying on mNAV."
            )
        elif mnav.stale:
            warnings.append(
                f"MSTR treasury figures are {mnav.days_old} days old "
                f"(as of {mnav.as_of}); mNAV may be inaccurate."
            )

    return ModelOutput(
        run_date=run_date, signals=signals, cross=cross, mnav=mnav, warnings=warnings
    )


def to_record(output: ModelOutput) -> dict[str, Any]:
    """Flatten a run into a JSON-serializable row for the forecast history log."""
    return {
        "run_date": output.run_date.isoformat(),
        "assets": {
            key: {
                "price": s.price,
                "as_of": s.as_of.isoformat(),
                "source": s.source,
                "regime": s.regime,
                "rsi": s.rsi,
                "ewma_vol": s.ewma_vol,
                "change_1d": s.change_1d,
                "forecasts": {
                    str(f.horizon_days): {
                        "median": f.percentiles.get(50),
                        "p5": f.percentiles.get(5),
                        "p95": f.percentiles.get(95),
                        "prob_up": f.prob_up,
                    }
                    for f in s.forecasts
                },
            }
            for key, s in output.signals.items()
        },
        "mnav": (
            {
                "mnav": output.mnav.mnav,
                "premium_pct": output.mnav.premium_pct,
                "verified": output.mnav.verified,
            }
            if output.mnav
            else None
        ),
    }
