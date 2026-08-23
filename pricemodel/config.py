"""Configuration: assets, model parameters and MSTR balance-sheet inputs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
REPORTS_DIR = REPO_ROOT / "reports"
HISTORY_DIR = REPO_ROOT / "history"

# Trading days per year for equities, calendar days for crypto (24/7 market).
ANNUALIZATION = {"crypto": 365.0, "equity": 252.0}


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    kind: str  # "crypto" | "equity"
    symbol: str  # source-agnostic ticker used by the fetchers

    @property
    def ann_factor(self) -> float:
        return ANNUALIZATION[self.kind]


ASSETS: tuple[Asset, ...] = (
    Asset("BTC", "Bitcoin", "crypto", "BTC"),
    Asset("ETH", "Ethereum", "crypto", "ETH"),
    Asset("MSTR", "Strategy (MicroStrategy)", "equity", "MSTR"),
)

ASSETS_BY_KEY = {a.key: a for a in ASSETS}


@dataclass(frozen=True)
class ModelParams:
    """Knobs for the signal and forecast layers."""

    history_days: int = 750
    rsi_period: int = 14
    sma_windows: tuple[int, ...] = (20, 50, 200)
    vol_windows: tuple[int, ...] = (30, 90)
    ewma_lambda: float = 0.94
    corr_windows: tuple[int, ...] = (30, 90)
    beta_window: int = 90

    # Forecast
    horizons: tuple[int, ...] = (1, 7, 30)
    n_paths: int = 20_000
    student_t_df: float = 4.0
    # Trailing drift is a weak predictor; shrink it hard toward zero and cap it
    # so the forecast never extrapolates a parabolic run.
    drift_shrinkage: float = 0.15
    max_annual_drift: float = 0.60
    drift_window: int = 90
    seed: int = 20240101

    percentiles: tuple[int, ...] = (5, 25, 50, 75, 95)


DEFAULT_PARAMS = ModelParams()


@dataclass
class MstrHoldings:
    """MSTR's bitcoin treasury and share count, used for the mNAV calculation.

    These are not available from a free, stable API, so they are held in
    ``config/holdings.json`` and refreshed from company filings. The report
    surfaces the ``as_of`` date and flags the figures once they go stale, so a
    forgotten update degrades visibly rather than silently.
    """

    btc_holdings: float
    diluted_shares: float
    as_of: date
    source: str = "config"
    stale_after_days: int = 45
    verified: bool = False

    def days_old(self, today: date | None = None) -> int:
        return ((today or date.today()) - self.as_of).days

    def is_stale(self, today: date | None = None) -> bool:
        return self.days_old(today) > self.stale_after_days


def load_holdings(path: Path | None = None) -> MstrHoldings:
    """Load MSTR treasury data, allowing environment overrides.

    ``MSTR_BTC_HOLDINGS`` / ``MSTR_DILUTED_SHARES`` / ``MSTR_HOLDINGS_AS_OF``
    override the file, which lets the workflow inject fresher numbers without
    a commit.
    """
    path = path or CONFIG_DIR / "holdings.json"
    with open(path) as fh:
        raw = json.load(fh)

    btc = float(os.environ.get("MSTR_BTC_HOLDINGS") or raw["btc_holdings"])
    shares = float(os.environ.get("MSTR_DILUTED_SHARES") or raw["diluted_shares"])
    as_of_raw = os.environ.get("MSTR_HOLDINGS_AS_OF") or raw["as_of"]
    as_of = datetime.strptime(as_of_raw, "%Y-%m-%d").date()

    overridden = bool(os.environ.get("MSTR_BTC_HOLDINGS"))
    source = "env override" if overridden else raw.get("source", "config")
    return MstrHoldings(
        btc_holdings=btc,
        diluted_shares=shares,
        as_of=as_of,
        source=source,
        stale_after_days=int(raw.get("stale_after_days", 45)),
        verified=bool(raw.get("verified", False)) or overridden,
    )


@dataclass(frozen=True)
class PriceSnapshot:
    """Spot prices pinned at a moment, with no history behind them.

    The fetchers deliberately fail loudly when no source answers, because a
    valuation quietly computed on yesterday's price is worse than no valuation.
    A snapshot is the explicit opt-out from that rule: it loads only when asked
    for by name, and every surface that uses it is expected to say so.

    Spot only. Moving averages, volatility, correlations and the Monte Carlo
    forecast all need a real series, so those stay unavailable rather than being
    reconstructed from one point.
    """

    prices: dict[str, float]
    quoted_at: dict[str, str]
    notes: dict[str, str]
    as_of: date
    source: str
    requested_for: str = ""

    def price(self, key: str) -> float:
        try:
            return self.prices[key]
        except KeyError:
            raise KeyError(f"snapshot has no price for {key!r}") from None


def load_price_snapshot(path: Path | None = None) -> PriceSnapshot:
    path = path or CONFIG_DIR / "price_snapshot.json"
    raw = json.loads(path.read_text())
    entries = raw["prices"]
    prices, quoted_at, notes = {}, {}, {}
    for key, entry in entries.items():
        # Accept either a bare number or an annotated object, so a hand-written
        # snapshot stays cheap to produce.
        if isinstance(entry, (int, float)):
            prices[key] = float(entry)
            quoted_at[key], notes[key] = raw.get("as_of", ""), ""
        else:
            prices[key] = float(entry["price"])
            quoted_at[key] = entry.get("quoted_at", raw.get("as_of", ""))
            notes[key] = entry.get("note", "")
        if prices[key] <= 0:
            raise ValueError(f"snapshot price for {key} must be positive")
    return PriceSnapshot(
        prices=prices,
        quoted_at=quoted_at,
        notes=notes,
        as_of=datetime.strptime(raw["as_of"], "%Y-%m-%d").date(),
        source=raw.get("source", "price snapshot"),
        requested_for=raw.get("requested_for", ""),
    )


@dataclass
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str] = field(default_factory=list)
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        missing = [
            k
            for k in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "REPORT_RECIPIENTS")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(
                "Missing required email environment variables: " + ", ".join(missing)
            )
        recipients = [
            r.strip() for r in os.environ["REPORT_RECIPIENTS"].split(",") if r.strip()
        ]
        return cls(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            sender=os.environ.get("SMTP_SENDER") or os.environ["SMTP_USERNAME"],
            recipients=recipients,
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() != "false",
        )
