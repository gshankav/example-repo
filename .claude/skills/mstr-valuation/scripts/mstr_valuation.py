#!/usr/bin/env python3
"""Full MSTR valuation: gross mNAV, net-of-claims NAV, BTC per share, sensitivity.

A command-line front end over ``pricemodel.valuation``. The arithmetic lives in
the package, not here, so this script, the web app and the daily report can
never disagree about the same number - this file only fetches inputs and renders
the result.

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
from datetime import date
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
sys.path.insert(0, str(REPO_ROOT))

from pricemodel.config import (  # noqa: E402
    ASSETS,
    DEFAULT_PARAMS,
    load_holdings,
    load_price_snapshot,
)
from pricemodel.data import (  # noqa: E402
    DataError,
    Series,
    fetch_all,
    fetch_shares_outstanding,
)
from pricemodel.model import build_cross_asset, compute_mnav  # noqa: E402
from pricemodel.valuation import (  # noqa: E402
    MNAV_SCENARIOS,
    CapitalStructure,
    load_capital_structure,
    sensitivity,
    value,
)


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
        cells = "".join(f"{_px(row['implied'][str(m)]):>11}" for m in MNAV_SCENARIOS)
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
    p.add_argument(
        "--snapshot",
        nargs="?",
        const="",
        metavar="PATH",
        help="value against pinned spot prices from config/price_snapshot.json "
        "(or PATH) instead of fetching. The mNAV percentile and realized beta "
        "need a history, so they report as n/a.",
    )
    p.add_argument("--capital-structure", type=Path, default=None)
    p.add_argument("--btc-price", type=float, help="override BTC spot")
    p.add_argument("--mstr-price", type=float, help="override MSTR price")
    p.add_argument("--btc-holdings", type=float, help="override coins held")
    p.add_argument("--shares", type=float, help="override basic share count")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = p.parse_args(argv)

    run_date = date.today()
    warnings: list[str] = []

    snapshot = None
    series: dict[str, Series] = {}
    if args.snapshot is not None:
        snapshot = load_price_snapshot(Path(args.snapshot) if args.snapshot else None)
        stamps = ", ".join(f"{k} {snapshot.quoted_at[k]}" for k in sorted(snapshot.quoted_at))
        warnings.append(
            f"PINNED PRICES from the snapshot ({snapshot.source}). "
            f"Quoted at: {stamps}. Not live."
        )
    elif args.offline:
        from pricemodel.cli import _synthetic_series  # deterministic fake series

        series = _synthetic_series()
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
    if not args.offline and snapshot is None:
        sec = fetch_shares_outstanding()
        if sec and sec[1] >= holdings.as_of:
            holdings.diluted_shares, holdings.as_of = sec[0], sec[1]

    if not holdings.verified:
        warnings.append(
            f"MSTR treasury figures are UNVERIFIED ({holdings.source}, as of "
            f"{holdings.as_of}) - confirm against the latest 8-K/10-Q."
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
    # disagree about the part they share - the percentile needs its history, so
    # both it and the beta go unavailable on a snapshot rather than being
    # estimated from a single price point.
    if snapshot is not None:
        spot_btc, spot_mstr = snapshot.price("BTC"), snapshot.price("MSTR")
        percentile = beta = None
    else:
        mnav = compute_mnav(series["BTC"], series["MSTR"], holdings, run_date)
        beta = build_cross_asset(series, DEFAULT_PARAMS).betas.get("MSTR/BTC")
        spot_btc, spot_mstr = mnav.btc_price, mnav.mstr_price
        percentile = mnav.percentile_rank

    v = value(
        btc_price=args.btc_price or spot_btc,
        mstr_price=args.mstr_price or spot_mstr,
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
                    "mnav_percentile": percentile,
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

    print(render(v, cap, percentile, beta, warnings, holdings.as_of))
    return 0


if __name__ == "__main__":
    sys.exit(main())
