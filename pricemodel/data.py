"""Market data fetching.

Every asset has several independent free sources tried in order. Free endpoints
rate-limit, change shape and go down; a single-source fetcher would take the
daily report with it, so each asset falls through a list until one works and the
report records which source actually answered.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

import requests

from .config import Asset

log = logging.getLogger(__name__)

USER_AGENT = "pricemodel/1.0 (daily report; +https://github.com/gshankav/example-repo)"
TIMEOUT = 30


class DataError(RuntimeError):
    """Raised when every source for an asset has failed."""


@dataclass
class Series:
    """A daily close series, ascending by date."""

    key: str
    dates: list[date]
    closes: list[float]
    source: str

    def __post_init__(self) -> None:
        if len(self.dates) != len(self.closes):
            raise ValueError("dates and closes must be the same length")

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last_date(self) -> date:
        return self.dates[-1]

    @property
    def last_close(self) -> float:
        return self.closes[-1]

    def tail(self, n: int) -> "Series":
        return Series(self.key, self.dates[-n:], self.closes[-n:], self.source)


def _get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    headers.update(kwargs.pop("headers", {}))
    resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def _clean(pairs: Iterable[tuple[date, float]]) -> tuple[list[date], list[float]]:
    """Sort ascending, drop duplicate dates and non-positive prices."""
    by_date: dict[date, float] = {}
    for d, px in pairs:
        if px is None:
            continue
        px = float(px)
        if px <= 0 or px != px:  # non-positive or NaN
            continue
        by_date[d] = px
    ordered = sorted(by_date.items())
    return [d for d, _ in ordered], [p for _, p in ordered]


# --------------------------------------------------------------------------
# Crypto sources
# --------------------------------------------------------------------------

_KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD"}


def _kraken(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    pair = _KRAKEN_PAIRS[symbol]
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"
    payload = _get(url).json()
    if payload.get("error"):
        raise DataError(f"kraken error: {payload['error']}")
    result = payload["result"]
    # The pair key Kraken echoes back is not always the one we asked for
    # (XBTUSD comes back as XXBTZUSD), so take whichever key holds the candles.
    candles = next(v for k, v in result.items() if k != "last")
    pairs = [
        (datetime.fromtimestamp(int(c[0]), tz=timezone.utc).date(), float(c[4]))
        for c in candles
    ]
    d, c = _clean(pairs)
    return d, c, "kraken"


_COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def _coinbase(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    # Coinbase caps a request at 300 candles, so page backwards in chunks.
    product = _COINBASE_PRODUCTS[symbol]
    collected: list[tuple[date, float]] = []
    end = datetime.now(tz=timezone.utc)
    for _ in range(max(1, (days // 290) + 1)):
        start = end.timestamp() - 290 * 86400
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity=86400"
            f"&start={datetime.fromtimestamp(start, tz=timezone.utc).isoformat()}"
            f"&end={end.isoformat()}"
        )
        rows = _get(url).json()
        if not rows:
            break
        for r in rows:
            # [time, low, high, open, close, volume]
            collected.append(
                (datetime.fromtimestamp(int(r[0]), tz=timezone.utc).date(), float(r[4]))
            )
        end = datetime.fromtimestamp(start, tz=timezone.utc)
        if len(collected) >= days:
            break
        time.sleep(0.35)  # stay under the public rate limit
    d, c = _clean(collected)
    return d, c, "coinbase"


_COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum"}


def _coingecko(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    coin = _COINGECKO_IDS[symbol]
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
        f"?vs_currency=usd&days={min(days, 365)}&interval=daily"
    )
    payload = _get(url).json()
    pairs = [
        (datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date(), float(px))
        for ts, px in payload["prices"]
    ]
    d, c = _clean(pairs)
    return d, c, "coingecko"


_BINANCE_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def _binance(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    # Often 451s from US-hosted CI runners, so this sits last in the chain.
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={_BINANCE_SYMBOLS[symbol]}&interval=1d&limit={min(days, 1000)}"
    )
    rows = _get(url).json()
    pairs = [
        (datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).date(), float(r[4]))
        for r in rows
    ]
    d, c = _clean(pairs)
    return d, c, "binance"


# --------------------------------------------------------------------------
# Equity sources
# --------------------------------------------------------------------------


def _stooq(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    text = _get(url).text
    if "Date" not in text.splitlines()[0]:
        raise DataError(f"stooq returned no data for {symbol}: {text[:120]!r}")
    pairs = []
    for row in csv.DictReader(io.StringIO(text)):
        close = row.get("Close")
        if not close or close == "N/D":
            continue
        pairs.append((datetime.strptime(row["Date"], "%Y-%m-%d").date(), float(close)))
    d, c = _clean(pairs)
    return d, c, "stooq"


def _yahoo(symbol: str, days: int) -> tuple[list[date], list[float], str]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range=3y&interval=1d"
    )
    payload = _get(url).json()
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    pairs = [
        (datetime.fromtimestamp(ts, tz=timezone.utc).date(), px)
        for ts, px in zip(stamps, closes)
        if px is not None
    ]
    d, c = _clean(pairs)
    return d, c, "yahoo"


SOURCES: dict[str, list[Callable[[str, int], tuple[list[date], list[float], str]]]] = {
    "BTC": [_kraken, _coinbase, _coingecko, _binance],
    "ETH": [_kraken, _coinbase, _coingecko, _binance],
    "MSTR": [_stooq, _yahoo],
}

MIN_OBSERVATIONS = 220  # enough for a 200-day moving average plus a little slack


def fetch_series(asset: Asset, days: int) -> Series:
    """Fetch a daily close series, trying each source until one succeeds."""
    errors: list[str] = []
    for source in SOURCES[asset.key]:
        name = source.__name__.lstrip("_")
        try:
            dates, closes, resolved = source(asset.symbol, days)
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next source"
            log.warning("%s: source %s failed: %s", asset.key, name, exc)
            errors.append(f"{name}: {exc}")
            continue

        if len(closes) < MIN_OBSERVATIONS:
            msg = f"only {len(closes)} observations (need {MIN_OBSERVATIONS})"
            log.warning("%s: source %s returned %s", asset.key, name, msg)
            errors.append(f"{name}: {msg}")
            continue

        log.info("%s: %d observations from %s", asset.key, len(closes), resolved)
        return Series(asset.key, dates, closes, resolved).tail(days)

    raise DataError(f"all sources failed for {asset.key}:\n  " + "\n  ".join(errors))


def fetch_all(assets: Iterable[Asset], days: int) -> dict[str, Series]:
    return {asset.key: fetch_series(asset, days) for asset in assets}


# --------------------------------------------------------------------------
# SEC EDGAR: MSTR shares outstanding
# --------------------------------------------------------------------------

MSTR_CIK = "0001050446"


def fetch_shares_outstanding(cik: str = MSTR_CIK) -> tuple[float, date] | None:
    """Latest reported common shares outstanding from SEC XBRL company facts.

    Returns ``None`` rather than raising: this is a refinement on the configured
    share count, and a SEC outage should not take down the whole report.
    """
    url = (
        f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
        f"/dei/EntityCommonStockSharesOutstanding.json"
    )
    try:
        # SEC requires a descriptive User-Agent with contact information.
        payload = _get(url, headers={"User-Agent": USER_AGENT}).json()
        entries = [u for u in payload["units"]["shares"] if u.get("end")]
        if not entries:
            return None
        latest = max(entries, key=lambda u: u["end"])
        return float(latest["val"]), datetime.strptime(latest["end"], "%Y-%m-%d").date()
    except Exception as exc:  # noqa: BLE001
        log.warning("SEC shares-outstanding lookup failed: %s", exc)
        return None


def align(a: Series, b: Series) -> tuple[list[float], list[float]]:
    """Return closes for the dates the two series have in common.

    Crypto trades every day and MSTR does not, so any cross-asset statistic has
    to be computed on the intersection rather than on raw positional slices.
    """
    b_map = dict(zip(b.dates, b.closes))
    pairs = [(px, b_map[d]) for d, px in zip(a.dates, a.closes) if d in b_map]
    return [p[0] for p in pairs], [p[1] for p in pairs]
