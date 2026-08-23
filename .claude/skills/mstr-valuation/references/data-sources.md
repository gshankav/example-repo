# Inputs: where each number comes from and how to refresh it

Every valuation figure in this skill is a function of five inputs. Three come
from live APIs, two do not — and the two that don't are the ones that go stale
and quietly poison everything downstream.

| Input | Source | Refresh | Fails how? |
| --- | --- | --- | --- |
| BTC price | Kraken → Coinbase → CoinGecko → Binance | every run | loudly — `DataError` when all four fail |
| MSTR price | Stooq → Yahoo | every run | loudly |
| Shares outstanding | SEC XBRL company concept API | every run, best-effort | quietly — returns `None`, config value stands |
| **BTC holdings** | **manual, from 8-K/10-Q** | **by hand** | **quietly, until the staleness check fires** |
| **Capital structure** | **manual, from 10-Q** | **by hand** | **quietly** |
| Pinned prices (`--snapshot`) | `config/price_snapshot.json` | by hand | never used unless asked for by name |

**What ships today.** The configs carry real figures current to 22 August 2026 —
840,447 BTC, 364.58M shares, $6.7B converts, $15.5B preferred, $4.8B cash — from
public reporting rather than from filings anyone opened, so `verified` is false
on both. Treat them as good working inputs and bad citations: quote them as
"reported as", never as "per the 10-Q", and confirm before anything consequential
rests on them.

## Prices

`pricemodel/data.py:fetch_series` walks a per-asset source list until one
answers, and `Series.source` records which did. The fallbacks exist because free
endpoints rate-limit, change shape and go down; Binance sits last because it
often returns 451 from US-hosted CI runners. If all sources fail the run raises
rather than substituting anything — the right behaviour for a valuation input.

Cross-asset work must go through `data.align`, which intersects on dates. MSTR
doesn't trade weekends and BTC does, so positional zipping silently compares
Saturday's BTC move against Monday's MSTR move.

## Share count

`pricemodel/data.py:fetch_shares_outstanding` reads
`dei/EntityCommonStockSharesOutstanding` from SEC XBRL for CIK `0001050446` and
takes the latest-ending entry. `cli.py` applies it only when the filing is newer
than the configured `as_of`.

Two things to remember: it is **basic** shares outstanding despite landing in a
field called `diluted_shares`, and it returns `None` on any failure by design —
a SEC outage shouldn't take down the daily report. The consequence is that a
silent SEC failure leaves a stale share count in place with no warning, so check
`as_of` rather than assuming the refinement ran.

MSTR issues continuously through its ATM, so between quarterly filings the true
count drifts above the last reported one. Valuations computed mid-quarter are
mildly optimistic on a per-share basis for that reason.

## BTC holdings

No free, stable API provides this. It lives in `config/holdings.json` and is
refreshed by hand.

Primary sources, in order of preference: the 8-K filed after each purchase
(Strategy discloses buys as they happen, so this is the freshest authoritative
number), then the latest 10-Q/10-K, then the company's investor page. Third-party
trackers are fine for a sanity check and not for the number itself.

To refresh: update `btc_holdings`, `as_of` and `source` in `config/holdings.json`,
and set `"verified": true`. For a one-off run without a commit, the env overrides
`MSTR_BTC_HOLDINGS`, `MSTR_DILUTED_SHARES` and `MSTR_HOLDINGS_AS_OF` take
precedence (`pricemodel/config.py:load_holdings`), and setting the BTC override
marks the run verified.

Staleness: `MstrHoldings.is_stale` fires past `stale_after_days` (45), and
`run_model` turns both unverified and stale into user-visible warnings. Keep
that pattern for any input you add — degrade visibly, never silently.

## Capital structure

Convertible notes and preferreds are not in `config/holdings.json` at all, which
is why the daily report's mNAV is gross-only. They live in
`config/capital_structure.json`, read by `pricemodel.valuation.load_capital_structure`
(the CLI's `--capital-structure PATH` overrides it).

The file ships with **placeholders** — structurally realistic so the app runs out
of the box, sourced from no filing — and every surface says UNVERIFIED until you
fix that. Fill it from the most recent 10-Q: the debt footnote gives face value,
conversion price and maturity per note series; the equity footnote and cover page
give preferred liquidation preference and dividend rate. Set `"verified": true`
only once the numbers came from a filing you actually opened.

If the file is missing entirely, valuation degrades to gross-only (net NAV equals
BTC NAV) and says so, rather than failing.

Convert terms change: issuers repurchase notes, holders convert early, and new
series get issued several times a year. A capital structure more than one
quarter old should be treated the way a stale BTC count is treated — flagged in
the output, not quietly used.

## Pinned prices

`config/price_snapshot.json` holds spot prices and nothing else. It exists for
two cases: reproducing a valuation at a known moment, and working when no source
is reachable at all.

It never loads on its own. The fetchers fail loudly by design — a valuation
quietly computed on last week's price is worse than no valuation — so the
snapshot is the explicit opt-out, behind `--snapshot`, and every surface that
uses it says so in a warning.

Spot only, deliberately. Moving averages, volatility, correlations, the Monte
Carlo forecast and the mNAV percentile all need a real series. Under a snapshot
they report unavailable rather than being reconstructed from one point: a
percentile computed from a single observation is a fabrication with a number
attached, which is worse than a blank.

## Sanity checks before quoting

Cheap, and they catch most input errors:

- Gross mNAV outside roughly 0.5x–4x → suspect an input, not a market event.
  (It has been *below* 1.0x since mid-2026, so a sub-par reading is no longer
  itself a red flag — 0.71x gross against 0.99x net is the current shape.)
- BTC NAV wildly different from market cap when mNAV is near 1 → share count is
  probably on the wrong basis.
- BTC per share moving sharply without a disclosed raise or purchase → the share
  count or holdings updated but not both.
- Implied BTC price at par more than ~3x spot → plausible at a euphoric premium,
  but check `btc_holdings` first.
