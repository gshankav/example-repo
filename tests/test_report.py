import re

import pytest

from pricemodel.config import ModelParams
from pricemodel.model import run_model
from pricemodel.report import (
    fmt_big,
    fmt_pct,
    fmt_price,
    render_html,
    render_text,
    subject_line,
)


@pytest.fixture
def output(series, holdings, run_date):
    return run_model(series, holdings, ModelParams(n_paths=1000), run_date=run_date)


def test_fmt_price_scales_by_magnitude():
    assert fmt_price(68123.4) == "$68,123"
    assert fmt_price(12.345) == "$12.35"
    assert fmt_price(0.00123) == "$0.0012"
    assert fmt_price(None) == "n/a"


def test_fmt_pct_sign_handling():
    assert fmt_pct(0.0312) == "+3.12%"
    assert fmt_pct(-0.0312) == "-3.12%"
    assert fmt_pct(0.0312, signed=False) == "3.12%"
    assert fmt_pct(None) == "n/a"


def test_fmt_big_uses_suffixes():
    assert fmt_big(1.5e12) == "$1.50T"
    assert fmt_big(2.25e9) == "$2.25B"
    assert fmt_big(None) == "n/a"


def test_render_text_contains_every_section(output):
    text = render_text(output)
    for heading in (
        "DAILY PRICE MODEL",
        "PERFORMANCE",
        "TECHNICAL POSITION",
        "FORWARD DISTRIBUTION",
        "MSTR mNAV",
        "CROSS-ASSET",
    ):
        assert heading in text
    for key in ("BTC", "ETH", "MSTR"):
        assert key in text


def test_render_text_never_signs_volatility(output):
    """Volatility is non-negative; a leading '+' reads as a change, not a level."""
    text = render_text(output)
    assert not re.search(r"vol=\+", text)


def test_render_html_is_self_contained(output):
    html = render_html(output)
    assert "<table" in html
    assert "MSTR mNAV" in html
    # Mail clients strip these, so they must never be relied on.
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert "@media" not in html


def test_render_html_escapes_nothing_unexpected(output):
    html = render_html(output)
    # Every opened table is closed - a broken table collapses the whole layout.
    assert html.count("<table") == html.count("</table>")


def test_html_shows_unverified_banner(series, run_date):
    from pricemodel.config import MstrHoldings

    unverified = MstrHoldings(
        btc_holdings=650_000.0,
        diluted_shares=285_000_000.0,
        as_of=run_date,
        verified=False,
    )
    out = run_model(series, unverified, ModelParams(n_paths=500), run_date=run_date)
    html = render_html(out)
    assert "unverified placeholders" in html.lower()


def test_subject_line_has_all_three_assets(output, run_date):
    subject = subject_line(output, run_date)
    assert subject.startswith("Price model")
    for key in ("BTC", "ETH", "MSTR"):
        assert key in subject
    assert len(subject) < 200  # keep it inside typical client truncation


def test_report_renders_without_mnav(series, run_date):
    out = run_model(series, None, ModelParams(n_paths=500), run_date=run_date)
    assert out.mnav is None
    assert "MSTR mNAV" not in render_text(out)
    assert render_html(out)  # still renders


def test_report_renders_with_accuracy_scorecard(output):
    from pricemodel.history import Accuracy

    accuracy = [Accuracy("BTC", 1, 30, 1.4, 92.0, 55.0)]
    assert "FORECAST SCORECARD" in render_text(output, accuracy)
    assert "Forecast scorecard" in render_html(output, accuracy)
