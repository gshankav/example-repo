"""Local web app: interactive MSTR valuation plus the daily report dashboard.

Two views over the same model the CLI reports on:

    /        an MSTR valuation you can push around - move BTC price, MSTR price,
             coins held or share count and watch gross/net mNAV, BTC per share
             and the sensitivity grid respond
    /report  the daily report as a browsable dashboard rather than an email

Built on ``http.server`` so the repo keeps its two runtime dependencies. That is
a deliberate trade: this is a local analysis tool for one analyst, not a service.
It binds to localhost, has no authentication, and should not be exposed.

    python -m pricemodel.web              # live data
    python -m pricemodel.web --offline    # synthetic data, no network
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ASSETS, DEFAULT_PARAMS, HISTORY_DIR, load_holdings
from .data import DataError, Series, fetch_all, fetch_shares_outstanding
from .history import load_records, score_history
from .model import ModelOutput, run_model
from .valuation import (
    MNAV_SCENARIOS,
    CapitalStructure,
    load_capital_structure,
    sensitivity,
    value,
    warnings_for,
)

log = logging.getLogger("pricemodel.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SPARKLINE_POINTS = 90
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


# --------------------------------------------------------------------------
# Data bundle and cache
# --------------------------------------------------------------------------


class Bundle:
    """One consistent snapshot: prices, treasury inputs, model output."""

    def __init__(
        self,
        series: dict[str, Series],
        output: ModelOutput,
        cap: CapitalStructure,
        accuracy: list[Any],
        holdings: Any,
    ) -> None:
        self.series = series
        self.output = output
        self.cap = cap
        self.accuracy = accuracy
        self.holdings = holdings
        self.built_at = time.time()


class ModelCache:
    """Fetch once, serve many.

    Every slider move re-requests a valuation, and a fetch walks a dozen free
    endpoints that rate-limit. Without a cache the page would exhaust the data
    sources within a minute of use, so the snapshot is held for ``ttl`` seconds
    and refreshed on demand. The lock means a concurrent burst waits for one
    fetch instead of starting several.
    """

    def __init__(self, offline: bool = False, ttl: float = 900.0) -> None:
        self.offline = offline
        self.ttl = ttl
        self._lock = threading.Lock()
        self._bundle: Bundle | None = None

    def get(self, force: bool = False) -> Bundle:
        with self._lock:
            fresh = self._bundle is not None and (
                time.time() - self._bundle.built_at < self.ttl
            )
            if fresh and not force:
                return self._bundle  # type: ignore[return-value]
            self._bundle = self._build()
            return self._bundle

    def _build(self) -> Bundle:
        run_date = date.today()
        if self.offline:
            from .cli import _synthetic_series

            series = _synthetic_series()
        else:
            series = fetch_all(ASSETS, DEFAULT_PARAMS.history_days)

        holdings = load_holdings()
        if not self.offline:
            sec = fetch_shares_outstanding()
            if sec and sec[1] >= holdings.as_of:
                holdings.diluted_shares, holdings.as_of = sec[0], sec[1]

        output = run_model(series, holdings, DEFAULT_PARAMS, run_date=run_date)
        actuals = {k: dict(zip(s.dates, s.closes)) for k, s in series.items()}
        _, accuracy = score_history(
            load_records(HISTORY_DIR / "forecasts.jsonl"), actuals
        )
        cap = load_capital_structure()
        log.info("model snapshot rebuilt (%s)", "offline" if self.offline else "live")
        return Bundle(series, output, cap, accuracy, holdings)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def _asset_payload(key: str, bundle: Bundle) -> dict[str, Any]:
    s = bundle.output.signals[key]
    series = bundle.series[key]
    return {
        "key": s.key,
        "name": s.name,
        "price": s.price,
        "as_of": s.as_of.isoformat(),
        "source": s.source,
        "observations": s.observations,
        "changes": {
            "1d": s.change_1d,
            "7d": s.change_7d,
            "30d": s.change_30d,
            "90d": s.change_90d,
            "365d": s.change_365d,
        },
        "sma": {str(k): v for k, v in s.sma.items()},
        "price_vs_sma": {str(k): v for k, v in s.price_vs_sma.items()},
        "vol": {str(k): v for k, v in s.vol.items()},
        "ewma_vol": s.ewma_vol,
        "rsi": s.rsi,
        "rsi_label": s.rsi_label,
        "zscore_200": s.zscore_200,
        "drawdown": s.drawdown,
        "trailing_high": s.trailing_high,
        "regime": s.regime,
        "forecasts": [
            {
                "horizon_days": f.horizon_days,
                "percentiles": {str(p): v for p, v in f.percentiles.items()},
                "prob_up": f.prob_up,
                "expected_move_pct": f.expected_move_pct,
            }
            for f in s.forecasts
        ],
        # Enough tail for a sparkline; the full 750-day series would be ~10x the
        # payload for a chart 200px wide.
        "history": [
            {"d": d.isoformat(), "c": c}
            for d, c in zip(
                series.dates[-SPARKLINE_POINTS:], series.closes[-SPARKLINE_POINTS:]
            )
        ],
    }


def report_payload(bundle: Bundle) -> dict[str, Any]:
    out = bundle.output
    m = out.mnav
    return {
        "run_date": out.run_date.isoformat(),
        "offline": False,
        "assets": {k: _asset_payload(k, bundle) for k in out.signals},
        "cross": {
            "correlations": {
                label: {str(w): v for w, v in windows.items()}
                for label, windows in out.cross.correlations.items()
            },
            "betas": out.cross.betas,
        },
        "mnav": (
            {
                "mnav": m.mnav,
                "premium_pct": m.premium_pct,
                "btc_nav": m.btc_nav,
                "nav_per_share": m.nav_per_share,
                "market_cap": m.market_cap,
                "btc_holdings": m.btc_holdings,
                "diluted_shares": m.diluted_shares,
                "as_of": m.as_of.isoformat(),
                "verified": m.verified,
                "stale": m.stale,
                "days_old": m.days_old,
                "source": m.source,
                "percentile_rank": m.percentile_rank,
                "history": [{"d": d.isoformat(), "v": v} for d, v in m.history],
            }
            if m
            else None
        ),
        "accuracy": [
            {
                "asset": a.asset,
                "horizon_days": a.horizon_days,
                "n": a.n,
                "median_abs_error_pct": a.median_abs_error_pct,
                "band_hit_rate": a.band_hit_rate,
                "direction_hit_rate": a.direction_hit_rate,
            }
            for a in bundle.accuracy
        ],
        "warnings": out.warnings,
    }


def value_payload(bundle: Bundle, overrides: dict[str, float]) -> dict[str, Any]:
    """Run the valuation at spot, or at whatever the caller overrode."""
    spot = {
        "btc_price": bundle.series["BTC"].last_close,
        "mstr_price": bundle.series["MSTR"].last_close,
        "btc_holdings": bundle.holdings.btc_holdings,
        "shares": bundle.holdings.diluted_shares,
    }
    used = {**spot, **overrides}
    v = value(
        btc_price=used["btc_price"],
        mstr_price=used["mstr_price"],
        btc_holdings=used["btc_holdings"],
        basic_shares=used["shares"],
        cap=bundle.cap,
    )

    run_date = bundle.output.run_date
    notes = warnings_for(bundle.cap, run_date)
    if not bundle.holdings.verified:
        notes.insert(
            0,
            "MSTR treasury figures are UNVERIFIED placeholders - update "
            "config/holdings.json from the latest 8-K/10-Q before quoting these.",
        )
    elif bundle.holdings.is_stale(run_date):
        notes.insert(
            0,
            f"MSTR treasury figures are {bundle.holdings.days_old(run_date)} days "
            f"old (as of {bundle.holdings.as_of}).",
        )
    if overrides:
        notes.append(
            "SCENARIO: "
            + ", ".join(sorted(overrides))
            + " overridden - not today's market."
        )

    return {
        "valuation": v,
        "sensitivity": sensitivity(v),
        "multiples": [str(m) for m in MNAV_SCENARIOS],
        "spot": spot,
        "overrides": sorted(overrides),
        "warnings": notes,
        "meta": {
            "run_date": run_date.isoformat(),
            "holdings_as_of": bundle.holdings.as_of.isoformat(),
            "holdings_verified": bundle.holdings.verified,
            "holdings_source": bundle.holdings.source,
            "capital_structure_verified": bundle.cap.verified,
            "capital_structure_as_of": bundle.cap.as_of.isoformat(),
            "capital_structure_source": bundle.cap.source,
            "btc_source": bundle.series["BTC"].source,
            "mstr_source": bundle.series["MSTR"].source,
            "mnav_percentile": (
                bundle.output.mnav.percentile_rank if bundle.output.mnav else None
            ),
            "realized_beta_90d": bundle.output.cross.betas.get("MSTR/BTC"),
        },
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class BadRequest(ValueError):
    """A malformed query parameter, reported to the client as a 400."""


def _float_param(qs: dict[str, list[str]], name: str) -> float | None:
    raw = qs.get(name, [""])[0].strip()
    if not raw:
        return None
    try:
        v = float(raw.replace(",", "").replace("_", ""))
    except ValueError:
        raise BadRequest(f"{name!r} is not a number: {raw!r}") from None
    # The page sends whatever is typed into a number field, so guard here
    # rather than letting a zero or a NaN reach the arithmetic.
    if v != v or v in (float("inf"), float("-inf")):
        raise BadRequest(f"{name!r} must be finite")
    if v <= 0:
        raise BadRequest(f"{name!r} must be greater than zero")
    return v


class Handler(BaseHTTPRequestHandler):
    server_version = "pricemodel"
    cache: ModelCache  # set on the server class below

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if route == "/":
                return self._send_static("index.html")
            if route == "/report":
                return self._send_static("report.html")
            if route.startswith("/static/"):
                return self._send_static(route[len("/static/") :])
            if route == "/api/value":
                return self._api_value(parse_qs(parsed.query))
            if route == "/api/report":
                return self._api_report(parse_qs(parsed.query))
            if route == "/api/health":
                return self._send_json({"ok": True, "offline": self.cache.offline})
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except BadRequest as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except DataError as exc:
            # No price source answered. A valuation with no prices is not a
            # degraded valuation, it is no valuation - say so rather than
            # rendering something that looks like data.
            self._send_json(
                {
                    "error": "data fetch failed",
                    "detail": str(exc),
                    "hint": "check network access, or restart with --offline",
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("unhandled error serving %s", self.path)
            self._send_json(
                {"error": exc.__class__.__name__, "detail": str(exc)},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _api_value(self, qs: dict[str, list[str]]) -> None:
        overrides = {
            name: v
            for name in ("btc_price", "mstr_price", "btc_holdings", "shares")
            if (v := _float_param(qs, name)) is not None
        }
        bundle = self.cache.get(force=qs.get("refresh", [""])[0] == "1")
        self._send_json(value_payload(bundle, overrides))

    def _api_report(self, qs: dict[str, list[str]]) -> None:
        bundle = self.cache.get(force=qs.get("refresh", [""])[0] == "1")
        payload = report_payload(bundle)
        payload["offline"] = self.cache.offline
        self._send_json(payload)

    def _send_static(self, rel: str) -> None:
        path = (STATIC_DIR / rel).resolve()
        # Refuse anything that escapes the static directory - this server is
        # local-only, but a traversal bug is a traversal bug.
        if not path.is_relative_to(STATIC_DIR) or not path.is_file():
            return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        body = path.read_bytes()
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._respond(HTTPStatus.OK, body, ctype)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode()
        self._respond(status, body, "application/json")

    def _respond(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)


def build_server(host: str, port: int, cache: ModelCache) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"cache": cache})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Local web app for the price model")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--offline", action="store_true", help="synthetic data, no network")
    p.add_argument("--ttl", type=float, default=900.0, help="cache seconds")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "binding to %s - this server has no authentication and serves "
            "unverified treasury data; do not expose it",
            args.host,
        )

    cache = ModelCache(offline=args.offline, ttl=args.ttl)
    server = build_server(args.host, args.port, cache)
    log.info("serving on http://%s:%d  (valuation at /, dashboard at /report)",
             args.host, args.port)
    if args.offline:
        log.warning("OFFLINE: synthetic prices, meaningless as a valuation")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
