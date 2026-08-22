"""MSTR valuation beyond gross mNAV: capital structure, net NAV, sensitivity.

``model.compute_mnav`` answers the headline question - what multiple of its
bitcoin is the equity trading at - and deliberately stops there. This module
adds the claims that rank ahead of common stock, which is what turns a multiple
into a valuation:

    gross NAV/share -> gross mNAV -> net NAV/share -> net mNAV

The interesting questions here are conditional ("what if BTC doubles and the
multiple halves"), so every function takes its inputs explicitly rather than
reaching for live data. Callers - the CLI report, the web app, the valuation
skill - supply spot or a scenario and get the same arithmetic either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR

# Scenario axes for the sensitivity grid. The BTC moves lean to the upside
# because that is the shape of the asset's own distribution; the multiples span
# roughly the range MSTR's premium has actually traded in.
BTC_SCENARIOS: tuple[float, ...] = (-0.50, -0.25, 0.0, 0.25, 0.50, 1.00)
MNAV_SCENARIOS: tuple[float, ...] = (0.8, 1.0, 1.5, 2.0, 2.5)


@dataclass
class ConvertibleNote:
    name: str
    face_usd: float
    conversion_price: float
    shares_if_converted: float
    maturity: str = ""

    def is_equity_like(self, mstr_price: float) -> bool:
        """In the money means the note behaves as equity, not as debt.

        This branch is the whole point of the class. A note counted as debt
        *and* as shares is charged for twice, which is the most common way an
        MSTR net-NAV calculation goes wrong.
        """
        return self.conversion_price > 0 and mstr_price >= self.conversion_price


@dataclass
class Preferred:
    name: str
    liquidation_preference_usd: float
    annual_dividend_usd: float = 0.0


@dataclass
class CapitalStructure:
    """Claims ranking ahead of common, plus the non-BTC assets behind them."""

    notes: list[ConvertibleNote] = field(default_factory=list)
    preferred: list[Preferred] = field(default_factory=list)
    cash_usd: float = 0.0
    other_assets_usd: float = 0.0
    as_of: date = date(1970, 1, 1)
    verified: bool = False
    source: str = "none configured"
    stale_after_days: int = 100
    path: str = ""

    def days_old(self, today: date) -> int:
        return (today - self.as_of).days

    def is_stale(self, today: date) -> bool:
        return self.days_old(today) > self.stale_after_days

    @property
    def preferred_claim(self) -> float:
        return sum(p.liquidation_preference_usd for p in self.preferred)

    @property
    def preferred_dividends(self) -> float:
        return sum(p.annual_dividend_usd for p in self.preferred)


def parse_capital_structure(raw: dict[str, Any], path: str = "") -> CapitalStructure:
    notes = []
    for n in raw.get("convertible_notes", []):
        face = float(n["face_usd"])
        k = float(n["conversion_price"])
        notes.append(
            ConvertibleNote(
                name=n.get("name", "convertible note"),
                face_usd=face,
                conversion_price=k,
                # Derive the share count from face/strike unless the filing
                # states it, which it does once anti-dilution adjustments bite.
                shares_if_converted=float(
                    n.get("shares_if_converted") or (face / k if k else 0.0)
                ),
                maturity=n.get("maturity", ""),
            )
        )
    return CapitalStructure(
        notes=notes,
        preferred=[
            Preferred(
                name=p.get("name", "preferred"),
                liquidation_preference_usd=float(p["liquidation_preference_usd"]),
                annual_dividend_usd=float(p.get("annual_dividend_usd", 0.0)),
            )
            for p in raw.get("preferred", [])
        ],
        cash_usd=float(raw.get("cash_usd", 0.0)),
        other_assets_usd=float(raw.get("other_assets_usd", 0.0)),
        as_of=datetime.strptime(raw["as_of"], "%Y-%m-%d").date(),
        verified=bool(raw.get("verified", False)),
        source=raw.get("source", "unknown"),
        stale_after_days=int(raw.get("stale_after_days", 100)),
        path=path,
    )


def load_capital_structure(path: Path | None = None) -> CapitalStructure:
    """Load convert and preferred terms, or an empty structure if absent.

    An absent file degrades to gross-only valuation (net NAV equals BTC NAV)
    rather than failing, but it is never silent: the returned structure is
    unverified, and callers surface that the same way stale treasury data is
    surfaced.
    """
    path = path or CONFIG_DIR / "capital_structure.json"
    if not path.exists():
        return CapitalStructure(source="no capital_structure.json found", path=str(path))
    return parse_capital_structure(json.loads(path.read_text()), str(path))


def value(
    btc_price: float,
    mstr_price: float,
    btc_holdings: float,
    basic_shares: float,
    cap: CapitalStructure,
) -> dict[str, Any]:
    """Every valuation figure, from five inputs and a capital structure."""
    if min(btc_price, mstr_price, btc_holdings, basic_shares) <= 0:
        raise ValueError("prices, holdings and share count must all be positive")

    equity_like = [n for n in cap.notes if n.is_equity_like(mstr_price)]
    debt_like = [n for n in cap.notes if not n.is_equity_like(mstr_price)]

    convert_shares = sum(n.shares_if_converted for n in equity_like)
    diluted_shares = basic_shares + convert_shares
    debt_claim = sum(n.face_usd for n in debt_like)

    btc_nav = btc_holdings * btc_price
    non_btc_assets = cap.cash_usd + cap.other_assets_usd
    net_nav = btc_nav + non_btc_assets - debt_claim - cap.preferred_claim

    gross_nav_ps_basic = btc_nav / basic_shares
    gross_nav_ps_diluted = btc_nav / diluted_shares
    net_nav_ps = net_nav / diluted_shares
    market_cap_diluted = mstr_price * diluted_shares

    # EV adds the claims treated as debt; the equity-like converts are already
    # inside the diluted market cap, so adding their face too would be the same
    # double-count the branch above exists to avoid.
    enterprise_value = (
        market_cap_diluted + debt_claim + cap.preferred_claim - cap.cash_usd
    )

    return {
        "btc_price": btc_price,
        "mstr_price": mstr_price,
        "btc_holdings": btc_holdings,
        "basic_shares": basic_shares,
        "convert_shares": convert_shares,
        "diluted_shares": diluted_shares,
        "equity_like_notes": [n.name for n in equity_like],
        "debt_like_notes": [n.name for n in debt_like],
        "btc_nav": btc_nav,
        "non_btc_assets": non_btc_assets,
        "debt_claim": debt_claim,
        "preferred_claim": cap.preferred_claim,
        "preferred_dividends": cap.preferred_dividends,
        "net_nav": net_nav,
        "market_cap_basic": mstr_price * basic_shares,
        "market_cap_diluted": market_cap_diluted,
        "enterprise_value": enterprise_value,
        "gross_nav_per_share_basic": gross_nav_ps_basic,
        "gross_nav_per_share_diluted": gross_nav_ps_diluted,
        "net_nav_per_share": net_nav_ps,
        "gross_mnav_basic": mstr_price / gross_nav_ps_basic,
        "gross_mnav_diluted": mstr_price / gross_nav_ps_diluted,
        "net_mnav": (mstr_price / net_nav_ps) if net_nav_ps > 0 else None,
        "ev_to_btc_nav": enterprise_value / btc_nav,
        "btc_per_share": btc_holdings / diluted_shares,
        "sats_per_share": btc_holdings / diluted_shares * 1e8,
        # The BTC price at which market cap equals the bitcoin backing - what
        # the equity is already pricing in.
        "btc_price_at_par": mstr_price * diluted_shares / btc_holdings,
        # Where senior claims would consume the whole treasury. A structural
        # reference point, not a margin call: these are unsecured obligations
        # with distant maturities, not a collateralized position.
        "btc_price_at_zero_net_nav": max(
            0.0, (debt_claim + cap.preferred_claim - cap.cash_usd) / btc_holdings
        ),
        # Equity's mechanical sensitivity to BTC with the multiple held fixed.
        # The realized beta usually runs above this; the gap is the premium
        # expanding and compressing, not the balance sheet.
        "structural_leverage": (btc_nav / net_nav) if net_nav > 0 else None,
    }


def sensitivity(
    v: dict[str, Any],
    btc_moves: tuple[float, ...] = BTC_SCENARIOS,
    multiples: tuple[float, ...] = MNAV_SCENARIOS,
) -> list[dict[str, Any]]:
    """Implied MSTR price across BTC moves and assumed multiples.

    The capital structure branch is held at today's state, so a scenario far
    enough from spot would in reality flip notes between debt and equity - treat
    the extremes as indicative rather than exact.
    """
    rows = []
    for move in btc_moves:
        px = v["btc_price"] * (1 + move)
        nav_ps = v["btc_holdings"] * px / v["diluted_shares"]
        rows.append(
            {
                "btc_move": move,
                "btc_price": px,
                "gross_nav_per_share": nav_ps,
                "implied": {str(m): m * nav_ps for m in multiples},
                # Return against today's actual price, which is what makes the
                # grid readable as risk rather than as a table of levels.
                "returns": {
                    str(m): (m * nav_ps) / v["mstr_price"] - 1.0 for m in multiples
                },
            }
        )
    return rows


def warnings_for(cap: CapitalStructure, today: date) -> list[str]:
    """User-visible caveats. Treasury inputs degrade visibly or not at all."""
    if not cap.notes and not cap.preferred:
        return [
            "No capital structure configured - net NAV equals gross NAV. Add "
            "config/capital_structure.json to account for converts and preferreds."
        ]
    if not cap.verified:
        return [
            f"Capital structure is UNVERIFIED ({cap.source}) - every net-NAV "
            "figure is illustrative until it comes from a 10-Q."
        ]
    if cap.is_stale(today):
        return [
            f"Capital structure is {cap.days_old(today)} days old (as of "
            f"{cap.as_of}); convert terms change every quarter."
        ]
    return []
