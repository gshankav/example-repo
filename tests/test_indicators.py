import numpy as np
import pytest

from pricemodel import indicators as ind


def test_sma_returns_none_without_enough_history():
    assert ind.sma(np.array([1.0, 2.0]), 5) is None


def test_sma_matches_manual_mean():
    closes = np.arange(1.0, 11.0)
    assert ind.sma(closes, 5) == pytest.approx(np.mean([6, 7, 8, 9, 10]))


def test_log_returns_length_and_value():
    closes = np.array([100.0, 110.0, 121.0])
    rets = ind.log_returns(closes)
    assert len(rets) == 2
    assert rets[0] == pytest.approx(np.log(1.1))
    assert rets[1] == pytest.approx(np.log(1.1))


def test_realized_vol_scales_with_annualization():
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.02, 400)
    crypto = ind.realized_vol(rets, 90, 365.0)
    equity = ind.realized_vol(rets, 90, 252.0)
    assert crypto > equity  # same returns, more periods per year
    assert crypto == pytest.approx(equity * np.sqrt(365 / 252), rel=1e-9)


def test_ewma_vol_reacts_faster_than_flat_window():
    calm = np.full(200, 0.001)
    shocked = np.concatenate([calm, np.full(10, 0.09)])
    ewma = ind.ewma_vol(shocked, 0.94, 365.0)
    flat = ind.realized_vol(shocked, 90, 365.0)
    assert ewma > flat


def test_rsi_all_gains_is_100():
    closes = np.arange(1.0, 40.0)
    assert ind.rsi(closes, 14) == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    closes = np.arange(40.0, 1.0, -1.0)
    assert ind.rsi(closes, 14) == pytest.approx(0.0)


def test_rsi_bounded_on_random_walk():
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    value = ind.rsi(closes, 14)
    assert 0.0 <= value <= 100.0


def test_momentum_and_zscore():
    closes = np.array([100.0] * 199 + [130.0])
    assert ind.momentum(closes, 1) == pytest.approx(0.30)
    assert ind.zscore(closes, 200) > 10  # a single large jump is far from the mean


def test_zscore_flat_series_is_zero():
    assert ind.zscore(np.full(200, 42.0), 200) == 0.0


def test_drawdown_from_high():
    closes = np.array([100.0, 200.0, 150.0])
    dd, high = ind.drawdown_from_high(closes, 365)
    assert high == 200.0
    assert dd == pytest.approx(-0.25)


def test_correlation_of_identical_series_is_one():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 0.02, 120)
    assert ind.correlation(a, a, 90) == pytest.approx(1.0)


def test_correlation_needs_enough_observations():
    a = np.array([0.01, 0.02, -0.01])
    assert ind.correlation(a, a, 90) is None


def test_beta_recovers_known_slope():
    rng = np.random.default_rng(2)
    benchmark = rng.normal(0, 0.02, 400)
    dependent = 1.8 * benchmark  # noiseless, so the slope is exact
    assert ind.beta(dependent, benchmark, 90) == pytest.approx(1.8)


def test_beta_none_when_benchmark_is_constant():
    assert ind.beta(np.ones(100), np.zeros(100), 90) is None


@pytest.mark.parametrize(
    "price,sma50,sma200,expected",
    [
        (110, 105, 100, "bull"),
        (90, 95, 100, "bear"),
        # Above the 200D while the 50D is still below it: recovering.
        (110, 95, 100, "recovering"),
        # Uptrend structure intact, price dipped under the 50D: pullback.
        (95, 105, 90, "pullback"),
        # Uptrend structure but price has broken below the 200D: corrective.
        (85, 110, 100, "corrective"),
        (100, None, None, "insufficient history"),
    ],
)
def test_regime_classification(price, sma50, sma200, expected):
    assert ind.regime(price, sma50, sma200) == expected
