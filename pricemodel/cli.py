"""Entry point: fetch data, run the model, write the report, send the email.

    python -m pricemodel.cli                 # full run, sends email
    python -m pricemodel.cli --no-email      # local dry run
    python -m pricemodel.cli --offline       # synthetic data, no network
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import report as report_mod
from .config import (
    ASSETS,
    DEFAULT_PARAMS,
    HISTORY_DIR,
    REPORTS_DIR,
    EmailConfig,
    ModelParams,
    load_holdings,
)
from .data import DataError, Series, fetch_all, fetch_shares_outstanding
from .history import append_record, load_records, score_history
from .mailer import build_message, send
from .model import run_model, to_record

log = logging.getLogger("pricemodel")

FORECAST_LOG = "forecasts.jsonl"


def _synthetic_series() -> dict[str, Series]:
    """Deterministic fake data so the pipeline can be exercised without network.

    The walk is pulled gently back toward its starting level. A pure random walk
    over 600 days wanders far enough to produce absurd levels (and an absurd
    mNAV), which makes the offline run useless as a visual check of the report.
    """
    rng = np.random.default_rng(7)
    today = date.today()
    out: dict[str, Series] = {}
    spec = {"BTC": (68000.0, 0.030), "ETH": (3300.0, 0.038), "MSTR": (1350.0, 0.055)}
    reversion = 0.02  # pull back toward the anchor each day
    for key, (start, vol) in spec.items():
        n = 600
        log_anchor = np.log(start)
        log_px = log_anchor
        closes = []
        for _ in range(n):
            log_px += reversion * (log_anchor - log_px) + rng.normal(0.0, vol)
            closes.append(float(np.exp(log_px)))
        dates = [today - timedelta(days=n - 1 - i) for i in range(n)]
        if key == "MSTR":  # equities do not trade on weekends
            pairs = [(d, c) for d, c in zip(dates, closes) if d.weekday() < 5]
            dates, closes = [p[0] for p in pairs], [p[1] for p in pairs]
        out[key] = Series(key, dates, [float(c) for c in closes], "synthetic")
    return out


def _actuals_from_series(series: dict[str, Series]) -> dict[str, dict[date, float]]:
    return {k: dict(zip(s.dates, s.closes)) for k, s in series.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily BTC/ETH/MSTR price model report")
    parser.add_argument("--no-email", action="store_true", help="skip sending email")
    parser.add_argument("--offline", action="store_true", help="use synthetic data")
    parser.add_argument("--out-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    parser.add_argument("--paths", type=int, default=DEFAULT_PARAMS.n_paths)
    parser.add_argument("--no-record", action="store_true", help="do not append to history")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    params = ModelParams(n_paths=args.paths)
    run_date = date.today()

    # 1. Data
    if args.offline:
        log.info("running offline with synthetic data")
        series = _synthetic_series()
    else:
        try:
            series = fetch_all(ASSETS, params.history_days)
        except DataError as exc:
            log.error("data fetch failed: %s", exc)
            return 2

    # 2. Treasury inputs, refined by SEC filings where possible
    holdings = load_holdings()
    if not args.offline:
        sec = fetch_shares_outstanding()
        if sec:
            shares, filed = sec
            if filed >= holdings.as_of:
                log.info("using SEC share count %.0f as of %s", shares, filed)
                holdings.diluted_shares = shares
                holdings.as_of = max(holdings.as_of, filed)

    # 3. Model
    output = run_model(series, holdings, params, run_date=run_date)

    # 4. Score past forecasts before appending today's
    history_path = args.history_dir / FORECAST_LOG
    _, accuracy = score_history(load_records(history_path), _actuals_from_series(series))

    # 5. Render
    html = report_mod.render_html(output, accuracy)
    text = report_mod.render_text(output, accuracy)
    subject = report_mod.subject_line(output, run_date)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{run_date:%Y-%m-%d}.html").write_text(html)
    (args.out_dir / "latest.html").write_text(html)
    (args.out_dir / "latest.txt").write_text(text)
    log.info("wrote report to %s", args.out_dir / f"{run_date:%Y-%m-%d}.html")

    print("\n" + text + "\n")

    # 6. Record today's forecast for future scoring
    if not args.no_record:
        append_record(history_path, to_record(output))

    # 7. Email
    if args.no_email:
        log.info("--no-email set, skipping delivery")
        return 0

    try:
        cfg = EmailConfig.from_env()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 3

    send(cfg, build_message(cfg, subject, text, html))
    return 0


if __name__ == "__main__":
    sys.exit(main())
