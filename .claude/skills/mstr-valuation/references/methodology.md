# MSTR valuation methodology

Full derivations behind `SKILL.md`. Read the section you need rather than the
whole file.

- [Notation](#notation)
- [Gross NAV and mNAV](#gross-nav-and-mnav)
- [Net NAV: debt and preferreds](#net-nav-debt-and-preferreds)
- [Convertibles: the if-converted branch](#convertibles-the-if-converted-branch)
- [Per-share bitcoin and accretion](#per-share-bitcoin-and-accretion)
- [Implied BTC price and the wipeout level](#implied-btc-price-and-the-wipeout-level)
- [Leverage: structural vs realized](#leverage-structural-vs-realized)
- [Sensitivity grids](#sensitivity-grids)
- [Where the premium comes from](#where-the-premium-comes-from)
- [Other treasury companies](#other-treasury-companies)

## Notation

| Symbol | Meaning |
| --- | --- |
| `B` | bitcoin held (coins) |
| `P_btc` | BTC spot price |
| `P` | MSTR share price |
| `S` | share count (see the basic/diluted note below) |
| `D` | debt claims ahead of common (convertible face, term loans) |
| `F` | preferred liquidation preference |
| `C` | cash and other non-BTC assets |

`S` is the number everything divides by, so it deserves more care than it
usually gets. This repo's `MstrHoldings.diluted_shares` is refined from SEC
`EntityCommonStockSharesOutstanding`, which is **common shares outstanding** —
basic. In-the-money convert shares must be added on top to get a genuinely
diluted count. Quote which basis you used; a 10% difference in `S` moves every
number here by 10%.

## Gross NAV and mNAV

```
BTC NAV          = B × P_btc
Gross NAV/share  = B × P_btc / S
Market cap       = P × S
Gross mNAV       = P / (B × P_btc / S) = (P × S) / (B × P_btc)
Premium          = (Gross mNAV − 1) × 100%
```

Note the second form: gross mNAV is just market cap ÷ BTC NAV, so `S` cancels
when you compute it that way. It does **not** cancel in NAV per share, which is
why a wrong share count still corrupts the per-share view even when the multiple
looks fine.

`> 1` means the equity trades above the bitcoin backing it; `< 1` means the
market values the company at less than its own bitcoin, which happens and is
where forced-seller and buyback dynamics start to matter.

This is what `pricemodel/model.py:compute_mnav` implements.

**Enterprise-value convention.** Many sell-side notes define mNAV as
`EV / BTC NAV` where `EV = market cap + D + F − C`. That is a different number,
usually meaningfully higher. Neither is wrong; they answer different questions
(what common pays vs what the whole capital structure pays). Always establish
which one an external figure used before comparing.

## Net NAV: debt and preferreds

Common stock owns what's left after everything senior to it:

```
Net asset value to common = B × P_btc + C − D − F
Net NAV/share             = (B × P_btc + C − D − F) / S
Net mNAV                  = P / Net NAV/share
```

Net mNAV is always above gross mNAV (smaller denominator), and the ratio between
them, `Gross NAV / Net NAV`, is the structural leverage — see below.

Two judgment calls worth making explicitly:

- **Debt at face or at market?** Face value is the conservative, reproducible
  choice and is what the script uses. Fair value is more correct when converts
  trade far from par, but it needs bond quotes that no free API provides. Say
  which you used.
- **Preferreds at liquidation preference.** Strategy's perpetual preferreds
  (STRK/STRF/STRD-style instruments) have no maturity, so they never force a
  sale — but they do rank ahead of common and carry a fixed dividend. Subtract
  the liquidation preference, and note the annual dividend separately as a cash
  drain: it is a recurring claim on the treasury that the NAV snapshot doesn't
  capture.

## Convertibles: the if-converted branch

A convertible note is debt that becomes equity above its conversion price. It is
one or the other, and treating it as both is the classic double-count.

Per note, compare `P` to the conversion price `K`:

| Condition | Treatment | Effect |
| --- | --- | --- |
| `P ≥ K` (in the money) | if-converted: add `face / K` shares to `S`, do **not** subtract face from NAV | dilutes per-share, removes the claim |
| `P < K` (out of the money) | debt: subtract face from NAV, leave shares out | reduces NAV, no dilution |

Both branches reduce value to common — that's expected, it's the cost of the
financing. What you must not do is subtract the face *and* count the shares,
which charges for the same obligation twice.

Two refinements the simple branch ignores, worth mentioning when precision
matters: notes near the conversion price are economically part-way between the
two states (the honest answer is a range, computed both ways), and notes MSTR
intends to settle in cash behave as debt regardless of price. The script prints
its branch choice per note so the assumption is visible rather than buried.

## Per-share bitcoin and accretion

```
BTC per share = B / S
```

This is the figure a long-term holder compounds, and Strategy's own "BTC Yield"
KPI is just its percentage change over a period. It is a better scorecard for
management than the share price, because it isolates what capital-markets
activity did to existing holders from what BTC's price did.

**When is issuing stock accretive?** Issue `N` shares at price `P`, spend the
proceeds on BTC:

```
new BTC/share = (B + P·N/P_btc) / (S + N)
```

This exceeds `B/S` exactly when `P·S > B·P_btc`, i.e. when

```
P > (B/S) × P_btc = gross NAV per share   ⟺   gross mNAV > 1
```

So **any** equity raise above 1.0x gross mNAV increases bitcoin per share, and
any raise below 1.0x decreases it. That single inequality explains the entire
ATM-at-a-premium strategy, and it's why the premium is not merely a sentiment
artifact — it is the mechanism by which the company converts market enthusiasm
into coins per share. It also explains the reflexive risk: at a discount, the
same machine runs in reverse and issuance destroys per-share value.

Debt-funded purchases are accretive to BTC/share at any price (no new shares),
but add to `D`, so they lift gross BTC/share while lowering net NAV per share.
Check both before calling a debt raise good or bad.

## Implied BTC price and the wipeout level

```
BTC price at par     = P × S / B
```

The BTC price at which market cap equals BTC NAV — what the equity is already
pricing in. Comparing it to spot is usually the most legible way to express a
premium: "the market is paying as if BTC were $X" lands better than "2.1x".

```
BTC price at zero net NAV = (D + F − C) / B
```

The BTC price at which senior claims consume the whole treasury and common's
asset backing goes to zero. Not a margin call — these are unsecured obligations
with distant maturities, not collateralized positions, so this is a structural
reference point rather than a liquidation trigger. Explain it that way, or it
reads as a prediction of forced selling that the instruments don't actually
support.

## Leverage: structural vs realized

Hold the multiple constant: equity value moves with net asset value, so

```
Structural leverage = Gross NAV / Net NAV = (B·P_btc) / (B·P_btc + C − D − F)
```

A 1% BTC move should move equity ~`structural leverage` percent, multiple
unchanged. With no debt it is 1.0; the more senior claims, the higher, and it
rises as BTC falls (the denominator shrinks faster than the numerator).

The realized 90-day beta of MSTR to BTC — already computed by
`pricemodel/model.py:build_cross_asset` — is typically well above the structural
figure. The excess is premium dynamics: the multiple expands into strength and
compresses into weakness, so the premium adds beta on top of the balance sheet.

Reporting both is more informative than either alone: structural leverage is
mechanical and persistent, premium-driven beta is behavioural and mean-reverts.
When realized beta runs far above structural, the equity is being driven by
sentiment rather than by its bitcoin.

## Sensitivity grids

The most useful single output for a decision-maker. Vary BTC price down the
rows, mNAV multiple across the columns, implied MSTR price in the cells:

```
implied MSTR price = mNAV × (B × P_btc_scenario / S)
```

Anchor the columns on observed history — roughly 0.8x to 3x, with today's
multiple marked — rather than a symmetric arbitrary range, and be explicit that
the multiple is an assumption, not a forecast. The grid's value is showing that
BTC and the multiple contribute comparably to the outcome: a doubling in BTC
with the multiple halving leaves the holder flat. That is the actual risk in
owning MSTR instead of BTC, and a grid makes it obvious in a way a point
estimate never does.

For a distributional rather than scenario view, the repo already simulates BTC
paths (`pricemodel/model.py:_forecast`); pushing those through this formula at a
fixed multiple gives a forward MSTR distribution conditional on the premium
holding. Say "conditional on" out loud — the multiple is the larger uncertainty,
and a band that ignores it looks far more precise than it is.

## Where the premium comes from

When asked to justify or challenge a premium, these are the substantive
arguments rather than a verdict. A defensible answer weighs them; it doesn't
pick a side.

Supporting a premium: access to capital markets that converts enthusiasm into
BTC per share (the accretion inequality above); index and mandate inclusion for
funds that cannot hold spot BTC or an ETF; a convert market that has funded
purchases at low cash cost; embedded optionality on continued issuance; and the
operating software business, small but not zero.

Arguing against: spot ETFs have removed most of the access rationale that
justified early premiums; the preferred dividends are a real recurring claim on
the treasury; the whole machine is reflexive — issuance funds purchases that
support the premium that enables issuance — and reflexive machines run backwards;
and a persistent discount is self-reinforcing in the same way, since issuance
then destroys per-share value and the lever has to be turned off.

## Other treasury companies

The framework transfers unchanged to MetaPlanet, Semler Scientific, Bitcoin
Group and similar. Only the inputs change. Watch for:

- **Non-USD reporting.** Convert coin value and market cap in the same currency
  before dividing, or the multiple is nonsense.
- **A real operating business.** Where the operating company is material, value
  it separately and add it to `C`; folding it into "other assets" at book value
  understates it.
- **Different disclosure cadence.** Some report holdings only quarterly, so
  `as_of` staleness matters far more than it does for MSTR, which discloses
  purchases as they happen.
- **Much smaller floats.** Multiples are more volatile and less meaningful at
  small size; a percentile rank over a short history says very little.
