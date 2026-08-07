from datetime import date, timedelta

import numpy as np
import pytest

from pricemodel.config import ModelParams, MstrHoldings
from pricemodel.data import Series

RUN_DATE = date(2026, 8, 7)


def _walk(key: str, start: float, vol: float, n: int, seed: int, skip_weekends: bool):
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(0.0005, vol, n)))
    dates = [RUN_DATE - timedelta(days=n - 1 - i) for i in range(n)]
    pairs = list(zip(dates, closes))
    if skip_weekends:
        pairs = [(d, c) for d, c in pairs if d.weekday() < 5]
    return Series(key, [p[0] for p in pairs], [float(p[1]) for p in pairs], "test")


@pytest.fixture
def series():
    return {
        "BTC": _walk("BTC", 68000.0, 0.028, 600, 11, False),
        "ETH": _walk("ETH", 3300.0, 0.035, 600, 12, False),
        "MSTR": _walk("MSTR", 1350.0, 0.050, 600, 13, True),
    }


@pytest.fixture
def holdings():
    return MstrHoldings(
        btc_holdings=650_000.0,
        diluted_shares=285_000_000.0,
        as_of=RUN_DATE,
        source="test",
        verified=True,
    )


@pytest.fixture
def params():
    # Few paths keeps the suite fast; the seed keeps it deterministic.
    return ModelParams(n_paths=3000)


@pytest.fixture
def run_date():
    return RUN_DATE
