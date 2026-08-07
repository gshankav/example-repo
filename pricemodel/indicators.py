"""Technical and statistical indicators, all on numpy arrays of daily closes."""

from __future__ import annotations

import numpy as np


def log_returns(closes: np.ndarray) -> np.ndarray:
    return np.diff(np.log(closes))


def sma(closes: np.ndarray, window: int) -> float | None:
    if len(closes) < window:
        return None
    return float(np.mean(closes[-window:]))


def realized_vol(rets: np.ndarray, window: int, ann_factor: float) -> float | None:
    """Annualized standard deviation of the last ``window`` daily log returns."""
    if len(rets) < window:
        return None
    return float(np.std(rets[-window:], ddof=1) * np.sqrt(ann_factor))


def ewma_vol(rets: np.ndarray, lam: float, ann_factor: float) -> float:
    """RiskMetrics-style exponentially weighted volatility.

    Reacts to a vol regime change far faster than a flat 30-day window, which
    matters when the forecast is built on top of it.
    """
    if len(rets) == 0:
        return 0.0
    weights = lam ** np.arange(len(rets) - 1, -1, -1)
    weights /= weights.sum()
    var = float(np.sum(weights * (rets - rets.mean()) ** 2))
    return float(np.sqrt(var * ann_factor))


def rsi(closes: np.ndarray, period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def momentum(closes: np.ndarray, lookback: int) -> float | None:
    """Simple price return over ``lookback`` observations, as a fraction."""
    if len(closes) < lookback + 1:
        return None
    return float(closes[-1] / closes[-1 - lookback] - 1.0)


def zscore(closes: np.ndarray, window: int) -> float | None:
    """How many standard deviations the last price sits from its own mean."""
    if len(closes) < window:
        return None
    window_slice = closes[-window:]
    sd = float(np.std(window_slice, ddof=1))
    if sd == 0:
        return 0.0
    return float((closes[-1] - float(np.mean(window_slice))) / sd)


def drawdown_from_high(closes: np.ndarray, window: int) -> tuple[float, float] | None:
    """Return ``(drawdown_fraction, running_high)`` over the trailing window."""
    if len(closes) < 2:
        return None
    window_slice = closes[-window:] if len(closes) >= window else closes
    high = float(np.max(window_slice))
    if high <= 0:
        return None
    return float(closes[-1] / high - 1.0), high


def correlation(a: np.ndarray, b: np.ndarray, window: int) -> float | None:
    """Pearson correlation of the last ``window`` paired daily log returns."""
    n = min(len(a), len(b))
    if n < window or window < 3:
        return None
    ra, rb = a[-window:], b[-window:]
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def beta(dependent: np.ndarray, benchmark: np.ndarray, window: int) -> float | None:
    """OLS slope of ``dependent`` returns on ``benchmark`` returns."""
    n = min(len(dependent), len(benchmark))
    if n < window or window < 3:
        return None
    y, x = dependent[-window:], benchmark[-window:]
    var = float(np.var(x, ddof=1))
    if var == 0:
        return None
    return float(np.cov(y, x, ddof=1)[0, 1] / var)


def regime(price: float, sma50: float | None, sma200: float | None) -> str:
    """Coarse trend label from the classic 50/200 moving-average structure.

    Two independent axes decide the label: where price sits relative to the
    200-day mean (the long-term trend), and whether the 50-day sits above the
    200-day (the trend's structure). The mixed cases are the informative ones,
    so they are split rather than lumped together:

    ``bull``        price > 50D > 200D - clean uptrend
    ``pullback``    above the 200D with 50D > 200D, but price under the 50D
    ``recovering``  above the 200D while the 50D is still below it
    ``corrective``  below the 200D but not yet a clean downtrend
    ``bear``        price < 50D < 200D - clean downtrend
    """
    if sma50 is None or sma200 is None:
        return "insufficient history"
    if price > sma50 > sma200:
        return "bull"
    if price < sma50 < sma200:
        return "bear"
    if price > sma200:
        return "pullback" if sma50 > sma200 else "recovering"
    return "corrective"
