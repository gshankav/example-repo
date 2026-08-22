"""Valuation layer: capital structure, net NAV, per-share and sensitivity."""

from datetime import date

import pytest

from pricemodel.valuation import (
    CapitalStructure,
    ConvertibleNote,
    Preferred,
    load_capital_structure,
    parse_capital_structure,
    sensitivity,
    value,
    warnings_for,
)

BTC_PRICE = 100_000.0
SHARES = 285_000_000.0
HOLDINGS = 653_000.0


@pytest.fixture
def cap():
    return CapitalStructure(
        notes=[
            ConvertibleNote("cheap", 3e9, 500.0, 6e6),
            ConvertibleNote("dear", 2e9, 1500.0, 4e6 / 3),
        ],
        preferred=[Preferred("perp", 5e9, 4e8)],
        cash_usd=5e8,
        other_assets_usd=1e9,
        as_of=date(2026, 8, 1),
        verified=True,
    )


def val(mstr_price, cap, **kw):
    kw.setdefault("btc_price", BTC_PRICE)
    kw.setdefault("btc_holdings", HOLDINGS)
    kw.setdefault("basic_shares", SHARES)
    return value(mstr_price=mstr_price, cap=cap, **kw)


# --- the convertible branch: debt or equity, never both -------------------


def test_note_below_strike_is_debt_not_dilution(cap):
    v = val(400.0, cap)  # below both conversion prices
    assert v["convert_shares"] == 0
    assert v["diluted_shares"] == SHARES
    assert v["debt_claim"] == pytest.approx(5e9)
    assert v["equity_like_notes"] == []
    assert len(v["debt_like_notes"]) == 2


def test_note_above_strike_dilutes_and_stops_being_debt(cap):
    v = val(1600.0, cap)  # above both
    assert v["convert_shares"] == pytest.approx(6e6 + 4e6 / 3)
    assert v["debt_claim"] == 0
    assert len(v["equity_like_notes"]) == 2


def test_notes_are_never_counted_as_both(cap):
    """The classic error: subtracting face *and* counting the shares."""
    for price in (300.0, 700.0, 2000.0):
        v = val(price, cap)
        for note in cap.notes:
            equity = note.name in v["equity_like_notes"]
            debt = note.name in v["debt_like_notes"]
            assert equity != debt, f"{note.name} counted twice at {price}"
        charged = v["debt_claim"] + sum(
            n.face_usd for n in cap.notes if n.name in v["equity_like_notes"]
        )
        assert charged == pytest.approx(5e9)


def test_straddling_price_splits_the_branch(cap):
    v = val(700.0, cap)  # above 500, below 1500
    assert v["equity_like_notes"] == ["cheap"]
    assert v["debt_like_notes"] == ["dear"]
    assert v["debt_claim"] == pytest.approx(2e9)
    assert v["convert_shares"] == pytest.approx(6e6)


def test_conversion_price_boundary_is_inclusive(cap):
    assert val(500.0, cap)["equity_like_notes"] == ["cheap"]
    assert val(499.99, cap)["equity_like_notes"] == []


# --- core identities ------------------------------------------------------


def test_gross_mnav_is_market_cap_over_btc_nav(cap):
    v = val(400.0, cap)
    assert v["gross_mnav_diluted"] == pytest.approx(
        v["market_cap_diluted"] / v["btc_nav"]
    )


def test_net_nav_subtracts_only_senior_claims(cap):
    v = val(400.0, cap)
    assert v["net_nav"] == pytest.approx(
        v["btc_nav"] + 1.5e9 - 5e9 - 5e9
    )
    assert v["net_nav_per_share"] == pytest.approx(v["net_nav"] / v["diluted_shares"])


def test_net_mnav_exceeds_gross_when_claims_exceed_other_assets(cap):
    v = val(400.0, cap)
    assert v["net_mnav"] > v["gross_mnav_diluted"]


def test_implied_btc_price_is_consistent_with_the_multiple(cap):
    """BTC price at par over spot must equal gross mNAV, by construction."""
    v = val(400.0, cap)
    assert v["btc_price_at_par"] / v["btc_price"] == pytest.approx(
        v["gross_mnav_diluted"]
    )


def test_structural_leverage_is_gross_over_net(cap):
    v = val(400.0, cap)
    assert v["structural_leverage"] == pytest.approx(v["btc_nav"] / v["net_nav"])


def test_leverage_rises_as_btc_falls(cap):
    high = val(400.0, cap, btc_price=200_000.0)["structural_leverage"]
    low = val(400.0, cap, btc_price=50_000.0)["structural_leverage"]
    assert low > high > 1.0


def test_no_capital_structure_means_net_equals_gross():
    v = val(400.0, CapitalStructure())
    assert v["net_nav"] == pytest.approx(v["btc_nav"])
    assert v["net_mnav"] == pytest.approx(v["gross_mnav_diluted"])


def test_negative_net_nav_reports_no_multiple():
    crushed = CapitalStructure(preferred=[Preferred("huge", 1e15, 0.0)])
    v = val(400.0, crushed)
    assert v["net_nav"] < 0
    assert v["net_mnav"] is None
    assert v["structural_leverage"] is None


# --- the accretion inequality --------------------------------------------


@pytest.mark.parametrize("mnav,expected", [(0.8, "dilutive"), (1.5, "accretive")])
def test_issuance_is_accretive_exactly_above_one_times_nav(cap, mnav, expected):
    """Issuing at a premium raises BTC per share; at a discount it lowers it."""
    base = val(400.0, cap)
    nav_ps = base["gross_nav_per_share_diluted"]
    issue_price = mnav * nav_ps
    new_shares = 5e6
    proceeds = issue_price * new_shares

    before = base["btc_per_share"]
    after = val(
        issue_price,
        cap,
        btc_holdings=HOLDINGS + proceeds / BTC_PRICE,
        basic_shares=SHARES + new_shares,
    )["btc_per_share"]

    assert (after > before) == (expected == "accretive")


def test_issuance_at_par_is_neutral(cap):
    base = val(400.0, cap)
    nav_ps = base["gross_nav_per_share_diluted"]
    new_shares = 5e6
    after = val(
        nav_ps,
        cap,
        btc_holdings=HOLDINGS + (nav_ps * new_shares) / BTC_PRICE,
        basic_shares=SHARES + new_shares,
    )
    assert after["btc_per_share"] == pytest.approx(base["btc_per_share"], rel=1e-9)


# --- input guards ---------------------------------------------------------


@pytest.mark.parametrize("bad", ["btc_price", "mstr_price", "btc_holdings", "basic_shares"])
def test_non_positive_inputs_are_rejected(cap, bad):
    kwargs = dict(
        btc_price=BTC_PRICE, mstr_price=400.0, btc_holdings=HOLDINGS,
        basic_shares=SHARES, cap=cap,
    )
    kwargs[bad] = 0
    with pytest.raises(ValueError):
        value(**kwargs)


# --- sensitivity ----------------------------------------------------------


def test_sensitivity_grid_shape_and_zero_row(cap):
    v = val(400.0, cap)
    rows = sensitivity(v)
    assert len(rows) == 6
    spot_row = next(r for r in rows if r["btc_move"] == 0.0)
    assert spot_row["btc_price"] == pytest.approx(BTC_PRICE)
    assert spot_row["gross_nav_per_share"] == pytest.approx(
        v["gross_nav_per_share_diluted"]
    )


def test_sensitivity_cells_are_multiple_times_nav(cap):
    v = val(400.0, cap)
    for row in sensitivity(v):
        for m, price in row["implied"].items():
            assert price == pytest.approx(float(m) * row["gross_nav_per_share"])
            assert row["returns"][m] == pytest.approx(price / v["mstr_price"] - 1.0)


def test_sensitivity_is_monotonic_in_both_axes(cap):
    rows = sensitivity(val(400.0, cap))
    prices = [r["implied"]["1.0"] for r in rows]
    assert prices == sorted(prices)
    first = rows[0]["implied"]
    assert [first[m] for m in ("0.8", "1.0", "1.5", "2.0", "2.5")] == sorted(
        first[m] for m in ("0.8", "1.0", "1.5", "2.0", "2.5")
    )


# --- config loading -------------------------------------------------------


def test_shares_derived_from_face_over_strike():
    cap = parse_capital_structure(
        {
            "convertible_notes": [{"face_usd": 1e9, "conversion_price": 250.0}],
            "as_of": "2026-08-01",
        }
    )
    assert cap.notes[0].shares_if_converted == pytest.approx(4e6)


def test_explicit_share_count_wins_over_the_derived_one():
    cap = parse_capital_structure(
        {
            "convertible_notes": [
                {"face_usd": 1e9, "conversion_price": 250.0, "shares_if_converted": 5e6}
            ],
            "as_of": "2026-08-01",
        }
    )
    assert cap.notes[0].shares_if_converted == pytest.approx(5e6)


def test_missing_file_degrades_to_empty_but_flagged(tmp_path):
    cap = load_capital_structure(tmp_path / "nope.json")
    assert cap.notes == [] and cap.preferred == []
    assert not cap.verified
    assert warnings_for(cap, date(2026, 8, 22))


def test_repo_config_parses():
    cap = load_capital_structure()
    assert cap.notes, "config/capital_structure.json should ship example notes"
    assert not cap.verified, "shipped placeholders must stay flagged unverified"


# --- warnings -------------------------------------------------------------


def test_unverified_structure_is_flagged(cap):
    cap.verified = False
    assert "UNVERIFIED" in warnings_for(cap, date(2026, 8, 22))[0]


def test_stale_structure_is_flagged(cap):
    assert warnings_for(cap, date(2027, 8, 22))  # far past stale_after_days
    assert not warnings_for(cap, date(2026, 8, 22))
