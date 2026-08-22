"""Web layer: payload shape, query validation and routing.

The cache is populated directly from the shared fixtures so nothing here
touches the network or the Monte Carlo path count twice.
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from pricemodel.model import run_model
from pricemodel.valuation import load_capital_structure
from pricemodel.web import (
    Bundle,
    ModelCache,
    build_server,
    report_payload,
    value_payload,
)


@pytest.fixture
def bundle(series, holdings, params, run_date):
    output = run_model(series, holdings, params, run_date=run_date)
    return Bundle(series, output, load_capital_structure(), [], holdings)


@pytest.fixture
def server(bundle):
    cache = ModelCache(offline=True)
    cache._bundle = bundle  # skip the fetch; the fixtures are the snapshot
    srv = build_server("127.0.0.1", 0, cache)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.headers["Content-Type"], r.read()


def get_json(base, path):
    status, _, body = get(base, path)
    return status, json.loads(body)


# --- payloads -------------------------------------------------------------


def test_value_payload_defaults_to_spot(bundle):
    p = value_payload(bundle, {})
    assert p["overrides"] == []
    assert p["valuation"]["btc_price"] == pytest.approx(bundle.series["BTC"].last_close)
    assert p["valuation"]["mstr_price"] == pytest.approx(
        bundle.series["MSTR"].last_close
    )


def test_value_payload_applies_overrides_and_flags_them(bundle):
    p = value_payload(bundle, {"btc_price": 123_456.0})
    assert p["valuation"]["btc_price"] == pytest.approx(123_456.0)
    assert p["overrides"] == ["btc_price"]
    assert any("SCENARIO" in w for w in p["warnings"])


def test_value_payload_surfaces_unverified_treasury(bundle):
    bundle.holdings.verified = False
    assert any("UNVERIFIED" in w for w in value_payload(bundle, {})["warnings"])


def test_payloads_are_json_serializable(bundle):
    for payload in (value_payload(bundle, {}), report_payload(bundle)):
        json.loads(json.dumps(payload, default=str))


def test_report_payload_carries_every_asset_and_a_sparkline_tail(bundle):
    p = report_payload(bundle)
    assert set(p["assets"]) == {"BTC", "ETH", "MSTR"}
    btc = p["assets"]["BTC"]
    assert 0 < len(btc["history"]) <= 90
    assert [h["d"] for h in btc["history"]] == sorted(h["d"] for h in btc["history"])
    assert {f["horizon_days"] for f in btc["forecasts"]} == {1, 7, 30}
    assert set(btc["forecasts"][0]["percentiles"]) == {"5", "25", "50", "75", "95"}


def test_report_payload_matches_the_model_it_serializes(bundle):
    p = report_payload(bundle)
    assert p["mnav"]["mnav"] == pytest.approx(bundle.output.mnav.mnav)
    assert p["assets"]["BTC"]["price"] == pytest.approx(
        bundle.output.signals["BTC"].price
    )


# --- routes ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/report"])
def test_pages_render(server, path):
    status, ctype, body = get(server, path)
    assert status == 200 and ctype.startswith("text/html")
    assert b"</html>" in body


@pytest.mark.parametrize(
    "path,ctype",
    [("/static/app.css", "text/css"), ("/static/common.js", "text/javascript")],
)
def test_static_assets_serve_with_the_right_type(server, path, ctype):
    status, got, _ = get(server, path)
    assert status == 200 and got.startswith(ctype)


def test_api_value_round_trips_an_override(server):
    status, body = get_json(server, "/api/value?mstr_price=1234.5")
    assert status == 200
    assert body["valuation"]["mstr_price"] == pytest.approx(1234.5)


def test_api_report_serves(server):
    status, body = get_json(server, "/api/report")
    assert status == 200 and body["assets"]


@pytest.mark.parametrize(
    "query",
    ["btc_price=abc", "btc_price=-1", "btc_price=0", "shares=nan", "mstr_price=1e999"],
)
def test_bad_query_values_are_rejected(server, query):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/api/value?" + query)
    assert exc.value.code == 400
    assert "error" in json.loads(exc.value.read())


def test_thousands_separators_are_accepted(server):
    _, body = get_json(server, "/api/value?btc_price=123,456")
    assert body["valuation"]["btc_price"] == pytest.approx(123456.0)


def test_unknown_route_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/nope")
    assert exc.value.code == 404


@pytest.mark.parametrize("path", ["/static/../web.py", "/static/../../config/holdings.json"])
def test_static_traversal_is_refused(server, path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, path)
    assert exc.value.code == 404


# --- cache ----------------------------------------------------------------


def test_cache_serves_the_same_snapshot_within_its_ttl(bundle):
    cache = ModelCache(offline=True, ttl=60)
    cache._bundle = bundle
    assert cache.get() is bundle


def test_cache_rebuilds_when_expired(bundle, monkeypatch):
    cache = ModelCache(offline=True, ttl=0.0)
    cache._bundle = bundle
    rebuilt = Bundle(bundle.series, bundle.output, bundle.cap, [], bundle.holdings)
    monkeypatch.setattr(cache, "_build", lambda: rebuilt)
    assert cache.get() is rebuilt


def test_force_refresh_rebuilds_inside_the_ttl(bundle, monkeypatch):
    cache = ModelCache(offline=True, ttl=9999)
    cache._bundle = bundle
    rebuilt = Bundle(bundle.series, bundle.output, bundle.cap, [], bundle.holdings)
    monkeypatch.setattr(cache, "_build", lambda: rebuilt)
    assert cache.get(force=True) is rebuilt


def test_concurrent_requests_trigger_one_build_not_many(bundle, monkeypatch):
    """The lock is what keeps a burst of slider moves from stampeding the APIs."""
    cache = ModelCache(offline=True, ttl=9999)
    builds = []

    def slow_build():
        builds.append(1)
        time.sleep(0.05)
        return bundle

    monkeypatch.setattr(cache, "_build", slow_build)
    threads = [threading.Thread(target=cache.get) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1
