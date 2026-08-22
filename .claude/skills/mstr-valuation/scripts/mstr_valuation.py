#!/usr/bin/env python3
"""Full MSTR valuation: gross mNAV, net-of-claims NAV, BTC per share, sensitivity.

The repo's daily report computes gross mNAV only - market cap against the market
value of the bitcoin, with convertible debt and preferred stock ignored. This
script reuses that machinery (same fetchers, same ``compute_mnav``) and adds the
layers the report leaves out, so the valuation and the daily report can never
disagree about the part they share.

    python scripts/mstr_valuation.py                     # live data
    python scripts/mstr_valuation.py --offline           # synthetic, no network
    python scripts/mstr_valuation.py --btc-price 150000  # scenario override
    python scripts/mstr_valuation.py --json              # machine-readable

Every input can be overridden, which is the point: the interesting questions
here are conditional ("what if BTC doubles and the multiple halves"), and a tool
that only reports today's number cannot answer them.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _find_repo_root(start: Path) -> Path:
    """Walk up until we find the pricemodel package this script builds on."""
    for candidate in [start, *start.parents]:
        if (candidate / "pricemodel" / "config.py").exists():
            return candidate
    raise SystemExit(
        "could not locate the pricemodel package - run this from inside the repo"
    )


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pricemodel.config import ASSETS, DEFAULT_PARAMS, load_holdings  # noqa: E402
from pricemodel.data import (  # noqa: E402
    DataError,
    Series,
    fetch_all,
    fetch_shares_outstanding,
)
from pricemodel.model import build_cross_asset, compute_mnav  # noqa: E402

# Scenario axes for the sensitivity grid. The BTC moves are deliberately
# asymmetric to the upside because that is the shape of the asset's own
# distribution; the multiples span roughly the observed historical range.
BTC_SCENARIOS = (-0.50, -0.25, 0.0, 0.25, 0.50, 1.00)
MNAV_SCENARIOS = (0.8, 1.0, 1.5, 2.0, 2.5)


# --------------------------------------------------------------------------
# Capital structure
# --------------------------------------------------------------------------


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
    notes: list[ConvertibleNote] = field(default_factory=list)
    preferred: list[Preferred] = field(default_factory=list)
    cash_usd: float = 0.0
    other_assets_usd: float = 0.0
    as_of: date = date(1970, 1, 1)
    verified: bool = False
    source: str = "unknown"
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


def load_capital_structure(explicit: Path | None) -> CapitalStructure:
    """Explicit path, then the repo's config, then this skill's placeholders.

    The fallback is what makes the script runnable out of the box, so it has to
    be loud about being placeholder data - see the warnings assembled in main().
    """
    for path in (
        explicit,
        REPO_ROOT / "config" / "capital_structure.json",
        SKILL_DIR / "assets" / "capital_structure.example.json",
    ):
        if path and path.exists():
            break
    else:  # pragma: no cover - the bundled example always exists
        raise SystemExit("no capital structure file found")

    raw = json.loads(path.read_text())
    notes = []
    for n in raw.get("convertible_notes", []):
        face = float(n["face_usd"])
        k = float(n["conversion_price"])
        notes.append(
            ConvertibleNote(
                name=n.get("name", "convertible note"),
                face_usd=face,
                conversion_price=k,
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
        path=str(path),
    )


# --------------------------------------------------------------------------
# Valuation
# --------------------------------------------------------------------------


def value(
    btc_price: float,
    mstr_price: float,
    btc_holdings: float,
    basic_shares: float,
    cap: CapitalStructure,
) -> dict[str, Any]:
    """Every figure the report needs, from five inputs and a capital structure."""
    equity_like = [n for n in cap.notes if n.is_equity_like(mstr_price)]
    debt_like = [n for n in cap.notes if not n.is_equity_like(mstr_price)]

    convert_shares = sum(n.shares_if_converted for n in equity_like)
    diluted_shares = basic_shares + convert_shares
    debt_claim = sum(n.face_usd for n in debt_like)

    btc_nav = btc_holdings * btc_price
    non_btc_assets = cap.cash_usd + cap.other_assets_usd
    net_nav = btc_nav + non_btc_assets - debt_claim - cap.preferred_claim

    market_cap_basic = mstr_price * basic_shares
    market_cap_diluted = mstr_price * diluted_shares

    gross_nav_ps_basic = btc_nav / basic_shares
    gross_nav_ps_diluted = btc_nav / diluted_shares
    net_nav_ps = net_nav / diluted_shares

    # EV adds the claims that were treated as debt; the equity-like converts are
    # already inside the diluted market cap, so adding their face too would be
    # the same double-count the branch above exists to avoid.
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
        "market_cap_basic": market_cap_basic,
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
        # What BTC price makes market cap equal the bitcoin backing - i.e. the
        # BTC price the equity is already pricing in.
        "btc_price_at_par": mstr_price * diluted_shares / btc_holdings,
        # Where senior claims would consume the whole treasury. A structural
        # reference point, not a margin call: these are unsecured obligations.
        "btc_price_at_zero_net_nav": max(
            0.0, (debt_claim + cap.preferred_claim - cap.cash_usd) / btc_holdings
        ),
        # Equity's mechanical sensitivity to BTC with the multiple held fixed.
        "structural_leverage": (btc_nav / net_nav) if net_nav > 0 else None,
    }


def sensitivity(v: dict[str, Any]) -> list[dict[str, Any]]:
    """Implied MSTR price across BTC moves and multiples.

    The capital structure branch is held at today's state; a scenario far enough
    from spot would flip notes between debt and equity, so treat the extremes as
    indicative.
    """
    rows = []
    for move in BTC_SCENARIOS:
        px = v["btc_price"] * (1 + move)
        nav_ps = v["btc_holdings"] * px / v["diluted_shares"]
        rows.append(
            {
                "btc_move": move,
                "btc_price": px,
                "gross_nav_per_share": nav_ps,
                "implied": {m: m * nav_ps for m in MNAV_SCENARIOS},
            }
        )
    return rows


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _big(x: float) -> str:
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(x) >= scale:
            return f"${x / scale:,.2f}{unit}"
    return f"${x:,.0f}"


def _px(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 1000 else f"${x:,.0f}"


def render(
    v: dict[str, Any],
    cap: CapitalStructure,
    percentile: float | None,
    realized_beta: float | None,
    warnings: list[str],
    holdings_as_of: date,
) -> str:
    out: list[str] = []
    add = out.append
    rule = "-" * 74

    add("=" * 74)
    add("MSTR VALUATION")
    add("=" * 74)

    if warnings:
        add("")
        for w in warnings:
            add(f"  !! {w}")

    add("")
    add("INPUTS")
    add(rule)
    add(f"  MSTR price              {_px(v['mstr_price'])}")
    add(f"  BTC price               {_px(v['btc_price'])}")
    add(f"  BTC held                {v['btc_holdings']:,.0f} BTC")
    add(f"  Shares (basic)          {v['basic_shares']:,.0f}   as of {holdings_as_of}")
    add(f"  + in-the-money converts {v['convert_shares']:,.0f}")
    add(f"  Shares (diluted)        {v['diluted_shares']:,.0f}")
    add(f"  Capital structure       {cap.path}")
    add(f"                          as of {cap.as_of}, "
        f"{'verified' if cap.verified else 'UNVERIFIED'}")

    add("")
    add("GROSS: market cap vs the bitcoin")
    add(rule)
    add(f"  BTC NAV                 {_big(v['btc_nav'])}")
    add(f"  Market cap (diluted)    {_big(v['market_cap_diluted'])}")
    add(f"  Gross NAV / share       {_px(v['gross_nav_per_share_diluted'])} diluted"
        f"   ({_px(v['gross_nav_per_share_basic'])} basic)")
    add(f"  Gross mNAV              {v['gross_mnav_diluted']:.3f}x diluted"
        f"   ({v['gross_mnav_basic']:.3f}x basic)")
    premium = (v["gross_mnav_diluted"] - 1) * 100
    word = "premium to" if premium >= 0 else "discount to"
    add(f"  Premium / discount      {premium:+.1f}%  ({word} bitcoin backing)")
    if percentile is not None:
        add(f"  Percentile vs own range {percentile:.0f}th  "
            f"(premium at today's BTC and share count, not true history)")
    add(f"  EV / BTC NAV            {v['ev_to_btc_nav']:.3f}x  "
        f"(the convention sell-side notes usually quote)")

    add("")
    add("NET: after claims ranking ahead of common")
    add(rule)
    add(f"  BTC NAV                 {_big(v['btc_nav'])}")
    add(f"  + cash & other assets   {_big(v['non_btc_assets'])}")
    add(f"  - debt-like converts    {_big(v['debt_claim'])}")
    add(f"  - preferred preference  {_big(v['preferred_claim'])}")
    add(f"  = Net NAV to common     {_big(v['net_nav'])}")
    add(f"  Net NAV / share         {_px(v['net_nav_per_share'])}")
    if v["net_mnav"] is not None:
        add(f"  Net mNAV                {v['net_mnav']:.3f}x")
    else:
        add("  Net mNAV                n/a - net NAV to common is negative")
    if cap.preferred_dividends:
        add(f"  Preferred dividends     {_big(v['preferred_dividends'])}/yr  "
            f"(a recurring drain, not a claim on assets)")
    for n in v["equity_like_notes"]:
        add(f"  [equity] {n}")
    for n in v["debt_like_notes"]:
        add(f"  [debt]   {n}")

    add("")
    add("PER SHARE")
    add(rule)
    add(f"  BTC per share           {v['btc_per_share']:.6f} BTC "
        f"({v['sats_per_share']:,.0f} sats)")
    add(f"  BTC price at par        {_px(v['btc_price_at_par'])}  "
        f"(vs {_px(v['btc_price'])} spot - what the equity prices in)")
    add(f"  BTC price at zero net   {_px(v['btc_price_at_zero_net_nav'])}  "
        f"(senior claims consume the treasury; not a margin call)")
    add("  Issuing equity above 1.0x gross mNAV raises BTC per share; "
        "below 1.0x it lowers it.")

    add("")
    add("LEVERAGE")
    add(rule)
    if v["structural_leverage"] is not None:
        add(f"  Structural (gross/net)  {v['structural_leverage']:.2f}x  "
            f"per 1% BTC move, multiple held constant")
    if realized_beta is not None:
        add(f"  Realized 90d beta       {realized_beta:.2f}x  (observed MSTR vs BTC)")
        if v["structural_leverage"]:
            gap = realized_beta - v["structural_leverage"]
            add(f"  Gap                     {gap:+.2f}x  "
                f"- premium expansion/compression, not balance sheet")

    add("")
    add("SENSITIVITY: implied MSTR price")
    add(rule)
    header = "  BTC price   " + "".join(f"{m:>10.1f}x" for m in MNAV_SCENARIOS)
    add(header)
    for row in sensitivity(v):
        cells = "".join(f"{_px(row['implied'][m]):>11}" for m in MNAV_SCENARIOS)
        add(f"  {_px(row['btc_price']):>10}{cells}   ({row['btc_move']:+.0%})")
    add("  Columns are assumed multiples, not forecasts. BTC and the multiple")
    add("  contribute comparably: BTC doubling at half the multiple leaves you flat.")

    add("")
    add("For information only. Not investment advice.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MSTR bitcoin-treasury valuation")
    p.add_argument("--offline", action="store_true", help="synthetic data, no network")
    p.add_argument("--capital-structure", type=Path, default=None)
    p.add_argument("--btc-price", type=float, help="override BTC spot")
    p.add_argument("--mstr-price", type=float, help="override MSTR price")
    p.add_argument("--btc-holdings", type=float, help="override coins held")
    p.add_argument("--shares", type=float, help="override basic share count")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = p.parse_args(argv)

    run_date = date.today()
    warnings: list[str] = []

    if args.offline:
        from pricemodel.cli import _synthetic_series  # deterministic fake series

        series: dict[str, Series] = _synthetic_series()
        warnings.append("OFFLINE: synthetic prices, meaningless as a valuation.")
    else:
        try:
            series = fetch_all(ASSETS, DEFAULT_PARAMS.history_days)
        except DataError as exc:
            # Same posture as pricemodel/cli.py: a valuation with no prices is
            # not a degraded valuation, it is no valuation, so fail visibly
            # rather than substituting anything.
            print(f"data fetch failed: {exc}", file=sys.stderr)
            print(
                "\nNo price source answered. Check network access, or use "
                "--offline to exercise the report with synthetic data.",
                file=sys.stderr,
            )
            return 2

    holdings = load_holdings()
    if not args.offline:
        sec = fetch_shares_outstanding()
        if sec and sec[1] >= holdings.as_of:
            holdings.diluted_shares, holdings.as_of = sec[0], sec[1]

    if not holdings.verified:
        warnings.append(
            "MSTR treasury figures are UNVERIFIED placeholders - update "
            "config/holdings.json from the latest 8-K/10-Q before quoting these."
        )
    elif holdings.is_stale(run_date):
        warnings.append(
            f"MSTR treasury figures are {holdings.days_old(run_date)} days old "
            f"(as of {holdings.as_of})."
        )

    cap = load_capital_structure(args.capital_structure)
    if not cap.verified:
        warnings.append(
            f"Capital structure is UNVERIFIED ({cap.source}) - every net-NAV "
            "figure below is illustrative until it comes from a 10-Q."
        )
    elif cap.is_stale(run_date):
        warnings.append(
            f"Capital structure is {cap.days_old(run_date)} days old "
            f"(as of {cap.as_of}); convert terms change every quarter."
        )

    # Reuse the repo's own mNAV so the valuation and the daily report can never
    # disagree about the part they share - the percentile needs its history.
    mnav = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
    beta = build_cross_asset(series, DEFAULT_PARAMS).betas.get("MSTR/BTC")

    v = value(
        btc_price=args.btc_price or mnav.btc_price,
        mstr_price=args.mstr_price or mnav.mstr_price,
        btc_holdings=args.btc_holdings or holdings.btc_holdings,
        basic_shares=args.shares or holdings.diluted_shares,
        cap=cap,
    )
    if any((args.btc_price, args.mstr_price, args.btc_holdings, args.shares)):
        warnings.append("SCENARIO: one or more inputs overridden on the command line.")

    if args.json:
        print(
            json.dumps(
                {
                    "run_date": run_date.isoformat(),
                    "valuation": v,
                    "mnav_percentile": mnav.percentile_rank,
                    "realized_beta_90d": beta,
                    "sensitivity": sensitivity(v),
                    "warnings": warnings,
                    "holdings_as_of": holdings.as_of.isoformat(),
                    "holdings_verified": holdings.verified,
                    "capital_structure_verified": cap.verified,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    print(render(v, cap, mnav.percentile_rank, beta, warnings, holdings.as_of))
    return 0


if __name__ == "__main__":
    sys.exit(main())
