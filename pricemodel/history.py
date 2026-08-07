"""Append-only forecast log plus scoring of past forecasts against outcomes.

Each run appends one record. The next run reads the log back and scores the
forecasts whose horizon has now elapsed, so the report can show whether the
model has actually been any good rather than only what it predicts next.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ScoredForecast:
    asset: str
    horizon_days: int
    made_on: date
    target_date: date
    predicted_median: float
    actual: float
    error_pct: float
    within_band: bool
    direction_correct: bool


@dataclass
class Accuracy:
    """Aggregate scorecard for one asset/horizon pair."""

    asset: str
    horizon_days: int
    n: int
    median_abs_error_pct: float
    band_hit_rate: float
    direction_hit_rate: float


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with open(path) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A truncated line from an interrupted run should not poison
                # the whole history.
                log.warning("skipping malformed history line %d in %s", line_no, path)
    return records


def score_history(
    records: list[dict[str, Any]],
    actuals: dict[str, dict[date, float]],
    max_lookback_days: int = 180,
) -> tuple[list[ScoredForecast], list[Accuracy]]:
    """Score every past forecast whose target date now has a known close.

    ``actuals`` maps asset key -> {date: close}. Forecast target dates are
    calendar-based, so for equities the target may land on a weekend; we look
    forward up to four days for the next available close before giving up.
    """
    cutoff = date.today() - timedelta(days=max_lookback_days)
    scored: list[ScoredForecast] = []

    for rec in records:
        try:
            made_on = datetime.strptime(rec["run_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if made_on < cutoff:
            continue

        for asset_key, payload in (rec.get("assets") or {}).items():
            asset_actuals = actuals.get(asset_key)
            if not asset_actuals:
                continue
            for horizon_raw, fc in (payload.get("forecasts") or {}).items():
                try:
                    horizon = int(horizon_raw)
                except ValueError:
                    continue
                median = fc.get("median")
                p5, p95 = fc.get("p5"), fc.get("p95")
                base_price = payload.get("price")
                if median is None or base_price is None:
                    continue

                target = made_on + timedelta(days=horizon)
                actual = None
                resolved_date = target
                for offset in range(5):  # roll forward past weekends/holidays
                    candidate = target + timedelta(days=offset)
                    if candidate in asset_actuals:
                        actual, resolved_date = asset_actuals[candidate], candidate
                        break
                if actual is None:
                    continue  # horizon has not elapsed yet, or no close available

                error_pct = (actual / median - 1.0) * 100.0
                within = (
                    p5 is not None and p95 is not None and p5 <= actual <= p95
                )
                direction_correct = (actual >= base_price) == (median >= base_price)

                scored.append(
                    ScoredForecast(
                        asset=asset_key,
                        horizon_days=horizon,
                        made_on=made_on,
                        target_date=resolved_date,
                        predicted_median=float(median),
                        actual=float(actual),
                        error_pct=error_pct,
                        within_band=bool(within),
                        direction_correct=bool(direction_correct),
                    )
                )

    return scored, summarize(scored)


def summarize(scored: list[ScoredForecast]) -> list[Accuracy]:
    buckets: dict[tuple[str, int], list[ScoredForecast]] = {}
    for s in scored:
        buckets.setdefault((s.asset, s.horizon_days), []).append(s)

    out: list[Accuracy] = []
    for (asset, horizon), items in sorted(buckets.items()):
        errors = sorted(abs(i.error_pct) for i in items)
        n = len(errors)
        mid = n // 2
        median_err = errors[mid] if n % 2 else (errors[mid - 1] + errors[mid]) / 2
        out.append(
            Accuracy(
                asset=asset,
                horizon_days=horizon,
                n=n,
                median_abs_error_pct=median_err,
                band_hit_rate=sum(i.within_band for i in items) / n * 100.0,
                direction_hit_rate=sum(i.direction_correct for i in items) / n * 100.0,
            )
        )
    return out
