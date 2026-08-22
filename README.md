# Daily BTC / ETH / MSTR price model

A daily quantitative report on Bitcoin, Ethereum and Strategy (MicroStrategy),
emailed automatically by a GitHub Actions cron job.

## What the report contains

| Section | What it shows |
| --- | --- |
| **Performance** | Price and 1D / 7D / 30D / 90D / 1Y returns |
| **Technical position** | Price vs the 20/50/200-day moving averages, 30D and 90D annualized realized volatility, RSI(14), 200-day z-score, drawdown from the trailing 1-year high, and a trend regime label |
| **Forward distribution** | Monte Carlo simulated price distribution at 1, 7 and 30 days — 5th/25th/50th/75th/95th percentiles plus P(up) |
| **MSTR mNAV** | MSTR's market cap against the market value of its bitcoin treasury: BTC NAV per share, the mNAV multiple, the premium or discount, and where that premium sits in its own trailing range |
| **Cross-asset** | 30D and 90D return correlations and BTC-betas for ETH and MSTR |
| **Forecast scorecard** | How this model's previous forecasts actually turned out — median absolute error, 5–95 band hit rate, and directional accuracy |

## Setup

The model runs without configuration. Only email delivery needs secrets.

### 1. Add the email secrets

In **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Required | Example |
| --- | --- | --- |
| `SMTP_HOST` | yes | `smtp.gmail.com` |
| `SMTP_USERNAME` | yes | `you@gmail.com` |
| `SMTP_PASSWORD` | yes | a Gmail **app password**, not your account password |
| `REPORT_RECIPIENTS` | yes | `you@gmail.com` (comma-separate for several) |
| `SMTP_PORT` | no | defaults to `587`; use `465` for implicit TLS |
| `SMTP_SENDER` | no | defaults to `SMTP_USERNAME` |

For Gmail you need 2-Step Verification enabled, then an app password from
<https://myaccount.google.com/apppasswords>. A normal account password will be
rejected.

### 2. Update the MSTR treasury figures

`config/holdings.json` ships with **unverified placeholder values** and the
report says so on every run until you fix them. mNAV is meaningless until you
do. Update `btc_holdings`, `diluted_shares` and `as_of` from Strategy's latest
8-K or 10-Q, and set `"verified": true`.

The share count is refined automatically from the SEC XBRL API on each run when
a newer filing is available; the BTC count has no free API and must be updated
by hand. The report warns once the data is older than `stale_after_days` (45 by
default), so a forgotten update fails loudly rather than silently.

You can also override without a commit via **Settings → Secrets and variables →
Actions → Variables**: `MSTR_BTC_HOLDINGS`, `MSTR_DILUTED_SHARES`,
`MSTR_HOLDINGS_AS_OF`.

`config/capital_structure.json` holds the convertible notes and preferred stock
used by the valuation page. It ships with placeholders too, and net-NAV figures
stay labelled UNVERIFIED until you replace them from the latest 10-Q.

### 3. Check it works

Run the workflow manually from the **Actions** tab — *Daily price report* →
*Run workflow*. Untick "Send the email" for a dry run that renders the report
without delivering it.

After that it runs on its own at **23:00 UTC daily**, chosen so MSTR's closing
price has settled. GitHub delays scheduled jobs under load, so expect some
drift. If a run fails, GitHub emails the repository owner by default.

## The web app

Two browsable views over the same model, with no extra dependencies — it runs on
`http.server`, so `requirements.txt` stays at numpy and requests.

```bash
python -m pricemodel.web              # http://127.0.0.1:8000
python -m pricemodel.web --offline    # synthetic data, no network
```

| Route | What it is |
| --- | --- |
| `/` | **MSTR valuation.** Move BTC price, MSTR price, coins held or share count and watch gross and net mNAV, BTC per share, implied BTC price, leverage and a BTC-price × multiple sensitivity grid recompute |
| `/report` | **Daily report as a dashboard.** The emailed report, browsable: prices with sparklines, the Monte Carlo forecast fan, technical position, cross-asset stats, mNAV and the scorecard |
| `/api/value`, `/api/report` | The same payloads as JSON, for scripting |

The valuation maths runs in Python (`pricemodel/valuation.py`) and the browser
only renders it, so the page, the CLI and the report cannot drift apart. Prices
are cached for 15 minutes (`--ttl`) because free endpoints rate-limit and a
slider would otherwise refetch on every move; **Refresh** forces a refetch.

It binds to localhost, has no authentication, and serves treasury figures that
are unverified until you fix them — it is a local analysis tool, not a service.
Don't expose it.

## Running locally

```bash
pip install -r requirements.txt

python -m pricemodel.cli --offline --no-email   # synthetic data, no network
python -m pricemodel.cli --no-email             # live data, no email
python -m pricemodel.cli                        # full run (needs SMTP env vars)
python -m pricemodel.web --offline              # the web app, no network
```

Useful flags: `--paths N` (Monte Carlo path count), `--no-record` (don't append
to the forecast log), `-v` (debug logging).

Each run writes `reports/YYYY-MM-DD.html`, `reports/latest.html` and
`reports/latest.txt`, and appends one line to `history/forecasts.jsonl`. The
workflow commits those back to the repo, which is what makes the forecast
scorecard accumulate over time.

## How the model works

**Data.** Each asset has several independent free sources tried in order — BTC
and ETH via Kraken → Coinbase → CoinGecko → Binance, MSTR via Stooq → Yahoo.
Free endpoints rate-limit and change shape, so a single-source fetcher would
eventually take the report down with it. The report names the source that
actually answered.

**Volatility.** An EWMA estimator (λ = 0.94) blended 60/40 with 30-day realized
volatility. EWMA reacts to a regime change far faster than a flat window, which
matters because the forecast is built on top of it. Crypto is annualized over
365 days and MSTR over 252.

**Drift.** Trailing 90-day mean return, shrunk to 15% of its value and capped at
±60% annualized. Trailing drift is a weak predictor, so it nudges the
distribution rather than steering it — without the cap, a parabolic run would
extrapolate into nonsense.

**Innovations.** Student-t (df = 4) rescaled to unit variance, not Gaussian.
Note what this does and does not do: because the draws are variance-matched, the
t and the normal agree closely at the 5th/95th percentiles — a standardized t
has slightly *thinner* shoulders. The difference appears further out, in the
probability of a genuine 5-sigma day, which a Gaussian treats as essentially
impossible and which crypto delivers regularly. The choice is about not
understating crash and melt-up risk, not about widening the headline band.

**Cross-asset statistics.** Computed on date-aligned returns. MSTR doesn't trade
weekends and crypto does, so zipping the two series positionally would silently
compare Saturday's BTC move against Monday's MSTR move.

**mNAV.** `MSTR price ÷ (BTC held × BTC price ÷ diluted shares)`. Above 1 means
the equity trades at a premium to the bitcoin backing it. The historical
percentile holds today's BTC and share counts constant, so it shows how the
*premium* moved with BTC's price rather than reconstructing the true historical
treasury — a comparison aid, not a restatement.

**Net of the capital structure.** The emailed report stops at gross mNAV.
`pricemodel/valuation.py` continues: it subtracts convertible debt and preferred
liquidation preference to get net NAV to common, adds BTC per share and the
structural leverage that follows from the claims. Convert terms come from
`config/capital_structure.json` (placeholders until you fill them from a 10-Q).
A note is treated as equity above its conversion price and as debt below it,
never as both — counting the face *and* the shares is the classic double-count.
Available at `/` in the web app and from the `mstr-valuation` skill's CLI.

## Limitations

- The forecast is a volatility model, not an alpha model. It describes a plausible
  range of outcomes given recent volatility; it has no view on where price is going.
  Treat the median as "roughly spot", because that is essentially what it is.
- No fundamentals, flows, funding rates, options-implied volatility, or news.
- The emailed report's mNAV ignores MSTR's convertible debt and preferred stock,
  measuring the premium to gross bitcoin value rather than to equity value net of
  liabilities. The web app's valuation page nets them off, but only as accurately
  as `config/capital_structure.json` is kept up to date.
- Free data sources occasionally disagree at the margin and revise.
- The forecast scorecard needs weeks of runs before its sample size means much.

For information only. Not investment advice.
