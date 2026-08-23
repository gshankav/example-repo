---
name: mstr-valuation
description: Value Strategy / MicroStrategy (MSTR) as a bitcoin treasury company - mNAV, premium or discount to NAV, BTC per share, net asset value after convertible debt and preferred stock, accretion from ATM issuance, and BTC-price sensitivity. Use this whenever the user asks about MSTR or Strategy valuation, mNAV, NAV per share, the premium or discount to bitcoin backing, BTC per share or "BTC yield", whether a raise was accretive, how MSTR should move for a given BTC move, or wants to change, extend or debug the mNAV code in pricemodel/. Also use it for the same questions about other bitcoin/crypto treasury companies (MetaPlanet, Semler, etc.), since the framework is identical. Reach for this even when the user only says "how expensive is MSTR right now" or "what BTC price is MSTR pricing in" - those are valuation questions and the naive market-cap answer is wrong here.
---

# MSTR valuation model

MSTR is not valued like an operating company. Almost all of its asset value is a
bitcoin pile, and its equity trades at a multiple of that pile that swings from
roughly 0.8x to well over 3x. So the entire question is: **what is the bitcoin
backing per share, and what multiple of it is the market paying?** Everything
else is detail.

This skill covers how to compute that correctly in this repo, and — more
importantly — the handful of places where the obvious computation is wrong.

## Where the numbers stand today

The shipped configs carry **real figures current to 22 August 2026**, and they
tell an unusual story worth knowing before you start: MSTR trades at roughly
**0.71x gross mNAV** — a ~29% discount to its bitcoin — while sitting at about
**0.99x net mNAV**, essentially at parity once $6.7B of converts and $15.5B of
preferred are netted off. Gross and net disagreeing that sharply *is* the story:
the discount to gross NAV is close to exactly the weight of the senior claims,
so the market is pricing the equity at its residual rather than at its bitcoin.

Two consequences follow directly. Issuing equity here is **dilutive** to BTC per
share (below 1.0x gross mNAV — see the accretion inequality below), so the
ATM flywheel runs in reverse. And every convert strikes far out of the money, so
they are all debt and none of them dilute.

None of this is filing-verified — see provenance below.

## Before any number is quoted: check provenance

`config/holdings.json` ships with **unverified placeholder** BTC and share
counts, and `verified` is `false` until someone updates it from a filing. Every
valuation number below is a direct function of those two inputs, so a stale
`btc_holdings` doesn't produce a slightly-off mNAV, it produces a confidently
wrong one.

The configs now hold sourced figures rather than invented placeholders, but
`verified` is still false: they came from public reporting, not from a filing
anyone opened. That distinction matters when you quote them — "reported as" is
honest, "per the 10-Q" is not.

Read the config and check `verified` and `as_of` before reporting anything. If
the figures are unverified or older than `stale_after_days` (45), say so in the
same breath as the number — the repo's own report does this, and a valuation
handed over without that caveat is worse than no valuation. `references/data-sources.md`
covers where fresh figures come from and how to refresh them.

## Quick start

```bash
python .claude/skills/mstr-valuation/scripts/mstr_valuation.py             # live data
python .claude/skills/mstr-valuation/scripts/mstr_valuation.py --snapshot  # pinned prices, no network
python .claude/skills/mstr-valuation/scripts/mstr_valuation.py --offline   # synthetic, no network
python .claude/skills/mstr-valuation/scripts/mstr_valuation.py --json      # machine-readable
```

`--snapshot` values against `config/price_snapshot.json` rather than fetching.
It holds spot prices and no history, so the mNAV percentile and the realized
beta come back n/a — both need a series, and estimating them from one point
would be inventing the answer rather than measuring it.

The arithmetic lives in `pricemodel/valuation.py`; the script only fetches
inputs and renders. The web app (`python -m pricemodel.web`) is the same module
behind an interactive page — use it when the question is exploratory ("what if
BTC doubles") and the CLI when you want a number to paste. Override any input to
test a scenario:

```bash
... --btc-price 150000 --mstr-price 600 --btc-holdings 700000
```

Run it before answering rather than doing the arithmetic by hand — the traps
below are exactly the ones that hand-arithmetic falls into.

## The valuation stack

Work outward in this order. Each layer answers a different question, and quoting
one when the user meant another is the most common way to be confidently wrong.

| Layer | Formula | Question it answers |
| --- | --- | --- |
| **Gross NAV/share** | `btc_holdings × btc_price ÷ shares` | What bitcoin backs one share? |
| **Gross mNAV** | `mstr_price ÷ gross NAV/share` | What multiple of that bitcoin is the market paying? (the headline number, what this repo computes) |
| **Net NAV/share** | `(BTC NAV + cash + other − debt − preferred) ÷ shares` | What backs a share *after* the claims that rank ahead of common? |
| **Net mNAV** | `mstr_price ÷ net NAV/share` | The same multiple, on the value common actually owns |

Net mNAV is the higher multiple whenever senior claims exceed non-BTC assets,
which is MSTR's normal state. The gap between gross and net mNAV *is* the
leverage in the structure, and it widens as BTC falls. When someone asks "is MSTR expensive", gross mNAV is the number
they've seen quoted; net mNAV is the number that answers the question.

Then the two per-share derivatives that matter more than either multiple:

- **BTC per share** = `btc_holdings ÷ shares`. This is the number a long-term
  holder is actually compounding. A raise that increases it was accretive; one
  that decreases it transferred value away from existing holders, whatever it
  did to the share price.
- **BTC price at par** = `mstr_price × shares ÷ btc_holdings`. The BTC price at
  which today's market cap equals the bitcoin backing — i.e. what BTC price the
  equity is already pricing in. Often the most intuitive way to express a rich
  premium to someone who finds "2.1x mNAV" abstract.

`references/methodology.md` has the full derivations, the accretion proof, the
leverage decomposition, and the sensitivity-grid construction.

## Four traps that produce wrong numbers

These are not hypothetical; each one is easy to hit and none of them announce
themselves in the output.

**1. Double-counting the convertibles.** A convert is either debt or equity, never
both. If MSTR trades above the conversion price, treat it as converted: add its
shares to the count and do *not* subtract its face value. If below, treat it as
debt: subtract the face value and leave the shares out. Doing both — subtracting
face *and* counting the shares — is the single most common MSTR valuation error,
and it understates net NAV twice over. The script picks the branch per-note from
the current price and prints which branch it took.

**2. "Diluted shares" in this repo is not diluted.** `MstrHoldings.diluted_shares`
is refined at runtime from the SEC XBRL field
`EntityCommonStockSharesOutstanding` (`pricemodel/data.py:fetch_shares_outstanding`),
which is *common shares outstanding* — basic, not diluted. Convert dilution has
to be added on top. Don't assume the field name is telling the truth.

**3. The historical mNAV series is not history.** `compute_mnav` builds its
history by holding *today's* BTC count and share count constant across past
dates. That is deliberate — it isolates how the premium moved with BTC's price —
but it is not what mNAV actually was back then, because MSTR was continuously
issuing shares and buying BTC. Never describe the percentile rank as "mNAV was
lower/higher on X% of days historically". It is a comparison aid; say so.

**4. Market cap vs enterprise value.** Both conventions are called "mNAV" in the
wild. This repo uses `market cap ÷ BTC NAV`. Sell-side notes often use
`EV ÷ BTC NAV`, which is a materially different number once there are billions
in converts and preferreds. When comparing against an external figure, establish
which convention it used first — the script prints both.

One more, less a trap than a limit: **structural leverage is not the observed
beta.** Gross NAV ÷ net NAV gives the mechanical leverage — how much a 1% BTC
move should move equity value with the multiple held constant. The realized
90-day beta (`pricemodel/model.py:build_cross_asset`) is usually well above it,
because the premium itself expands on the way up and compresses on the way down.
That gap is sentiment, not balance sheet, and it's the part that mean-reverts.

## Working on the mNAV code in this repo

| Concern | Location |
| --- | --- |
| Gross mNAV math and history | `pricemodel/model.py:compute_mnav` |
| Net NAV, capital structure, sensitivity | `pricemodel/valuation.py` |
| Convert and preferred terms | `config/capital_structure.json` |
| Treasury inputs, staleness, env overrides | `pricemodel/config.py:MstrHoldings`, `load_holdings` |
| SEC share-count refinement | `pricemodel/data.py:fetch_shares_outstanding` |
| Email report rendering | `pricemodel/report.py:_mnav_block` |
| Web app (`/` valuation, `/report` dashboard) | `pricemodel/web.py`, `pricemodel/static/` |
| Tests | `tests/test_valuation.py`, `tests/test_web.py`, `tests/test_model.py` (`test_mnav_*`) |

The valuation arithmetic has exactly one home — `pricemodel/valuation.py`. The
CLI script, the web app and the daily report all call into it, so a fix lands
everywhere at once. Resist recomputing any of it in a template or in JavaScript;
a second copy is a second answer.

Repo conventions worth matching: cross-asset math runs on `data.align`, never on
positional slices, because MSTR doesn't trade weekends and BTC does — zipping
them raw compares Saturday's BTC against Monday's MSTR. Treasury data
degradation is always surfaced as a user-visible warning rather than a silent
default. Add tests alongside the existing `test_mnav_*` cases; `tests/conftest.py`
provides `series` and `holdings` fixtures with a fixed run date.

## Reporting a valuation

Lead with the multiple and what it means in plain language, then the per-share
figures, then the caveat. A useful shape:

```
MSTR $X — gross mNAV N.NNx (premium/discount of P%), net of debt and preferreds M.MMx.
Each share carries B.BBBB BTC; the market cap implies a BTC price of $Y versus $Z spot.
[Treasury figures as of DATE — verified / UNVERIFIED PLACEHOLDER.]
```

Give the numbers with their uncertainty rather than hedging the whole answer
into uselessness. And keep the frame straight: this is a valuation of a bitcoin
pile plus a capital structure. It says nothing about whether BTC goes up. For
information only, not investment advice.

## References

- `references/methodology.md` — derivations, accretion/BTC-yield math, leverage decomposition, sensitivity grids, other treasury companies
- `references/data-sources.md` — where every input comes from, how to refresh it, what to do when a source is stale or missing
