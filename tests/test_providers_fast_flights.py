from datetime import date

import pytest
from fast_flights import FlightsNotFound

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError
from vacation_planner.providers.fast_flights import FastFlightsClient, parse_results
from tests.conftest import REPO_CONFIG
from tests.fixtures.fast_flights_result import build

REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


@pytest.fixture
def settings(config_dir):
    return load_config(config_dir, env={}).settings


def test_build_query(settings):
    q = FastFlightsClient(settings).build_query(REQ)
    assert q.url().startswith("https://www.google.com/travel/flights/search?tfs=")
    assert q.currency == "EUR"
    assert q.get_trip_type() == "round-trip" and q.get_seat_type() == "business"
    assert len(q.flight_data) == 2 and len(q.passengers) == 3


def test_parse_results_maps_codes_and_times():
    offers = parse_results(build(), REQ, "https://g/url", price_is_total=True)
    assert [o.price_total for o in offers] == [5940, 4100, 6100]
    o = offers[0]
    assert o.provider is Provider.FAST_FLIGHTS and o.airlines == ["LH", "TG"] and o.stops == 1
    assert o.departs_at == "2026-10-17 10:35" and o.arrives_at == "2026-10-18 06:10"
    assert o.duration_minutes == 1175 and o.price_level is None and o.google_url == "https://g/url"
    assert o.per_person == 1980


def test_search_filters_excluded_and_pauses(settings):
    sleeps, calls = [], []

    def fetch(q):
        calls.append(q)
        return build()

    c = FastFlightsClient(settings, fetch=fetch, sleep=sleeps.append)
    res = c.search(REQ)
    assert [o.price_total for o in res.offers] == [5940, 6100]
    assert sleeps == [5.0] and len(calls) == 1 and res.raw_path is None


def test_search_wraps_failures(settings):
    def not_found(q):
        raise FlightsNotFound("none")

    with pytest.raises(ProviderError):
        FastFlightsClient(settings, fetch=not_found, sleep=lambda s: None).search(REQ)

    def empty(q):
        return build().__class__()

    with pytest.raises(ProviderError):
        FastFlightsClient(settings, fetch=empty, sleep=lambda s: None).search(REQ)


def test_consent_fetch_sends_cookie_and_returns_text():
    from vacation_planner.providers.fast_flights import CONSENT_COOKIE, ConsentFetch
    calls = {}

    class FakeResp:
        text = "<html>ok</html>"

    class FakeClient:
        def __init__(self, **kw):
            calls["kw"] = kw
        def get(self, url, params=None):
            calls["url"], calls["params"] = url, params
            return FakeResp()

    q = FastFlightsClient(load_config(REPO_CONFIG, env={}).settings).build_query(REQ)
    html = ConsentFetch(client_factory=FakeClient).fetch_html(q)
    assert html == "<html>ok</html>"
    assert calls["kw"]["headers"] == {"Cookie": CONSENT_COOKIE}
    assert calls["url"].startswith("https://www.google.com/travel/flights")
    assert calls["params"]["tfs"] == q.params()["tfs"]


def test_default_fetch_is_consent_aware(settings):
    from vacation_planner.providers.fast_flights import fetch_with_consent
    assert FastFlightsClient(settings).fetch is fetch_with_consent


def test_malformed_result_raises_provider_error(settings):
    rl = build()
    rl[0].flights[0].departure = None            # layout change: missing departure

    with pytest.raises(ProviderError, match="fast_flights: parse failed"):
        FastFlightsClient(settings, fetch=lambda q: rl, sleep=lambda s: None).search(REQ)
