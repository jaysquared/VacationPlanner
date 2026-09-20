import re
from datetime import date

import pytest
from fast_flights import FlightsNotFound

from vacation_planner.config import load_config
from vacation_planner.models import Leg, Provider, SearchRequest, SeatClass
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
    assert o.legs == [Leg("HAM", "FRA", "2026-10-17 10:35", "2026-10-17 11:45"),
                      Leg("FRA", "BKK", "2026-10-17 13:55", "2026-10-18 06:10")]


def test_search_filters_excluded_and_pauses(settings):
    sleeps, calls = [], []

    def fetch(q):
        calls.append(q)
        return build()

    c = FastFlightsClient(settings, fetch=fetch, sleep=sleeps.append)
    res = c.search(REQ)
    # 4100 is Air India (excluded); 6100 stops in DXB 23:05 -> 03:30 (too long, and at night)
    assert [o.price_total for o in res.offers] == [5940]
    assert sleeps == [5.0] and len(calls) == 1 and res.raw_path is None


def test_search_wraps_failures(settings):
    def boom(q):
        raise RuntimeError("the network is on fire")

    with pytest.raises(ProviderError, match="RuntimeError"):
        FastFlightsClient(settings, fetch=boom, sleep=lambda s: None).search(REQ)

    def empty(q):
        return build().__class__()

    with pytest.raises(ProviderError):
        FastFlightsClient(settings, fetch=empty, sleep=lambda s: None).search(REQ)


@pytest.mark.parametrize("error", [FlightsNotFound("none"), TypeError("'NoneType' object is not subscriptable"),
                                   IndexError("list index out of range")])
def test_a_page_without_itineraries_is_an_empty_result_not_an_error(settings, error, caplog):
    """No Business fare for PQC is a search with nothing to report, not a failed search."""
    def no_itineraries(q):
        raise error

    with caplog.at_level("INFO", logger="vacation_planner.providers.fast_flights"):
        res = FastFlightsClient(settings, fetch=no_itineraries, sleep=lambda s: None).search(REQ)
    assert res.offers == [] and res.provider is Provider.FAST_FLIGHTS and res.request is REQ
    assert "no itineraries" in caplog.text and "BKK" in caplog.text


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


def test_client_reads_its_own_price_flag(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"( +)fast_flights: true", r"\1fast_flights: false", st.read_text()))
    settings = load_config(config_dir, env={}).settings
    c = FastFlightsClient(settings, fetch=lambda q: build(), sleep=lambda s: None)
    o = c.search(REQ).offers[0]
    assert (o.per_person, o.price_total) == (5940, 5940 * 3)   # per-person price, 3 travellers


def test_missing_airline_metadata_is_an_error_not_a_silent_bypass(settings):
    rl = build()
    rl.metadata = None                      # no code <-> name mapping at all
    with pytest.raises(ProviderError, match="no airline metadata"):
        FastFlightsClient(settings, fetch=lambda q: rl, sleep=lambda s: None).search(REQ)

    rl2 = build()
    rl2.metadata.airlines = []
    with pytest.raises(ProviderError, match="no airline metadata"):
        FastFlightsClient(settings, fetch=lambda q: rl2, sleep=lambda s: None).search(REQ)


def test_excluded_airline_is_dropped_by_name_too(settings):
    """`AI` is excluded; the itinerary only names 'Air India', mapped via the result metadata."""
    rl = build()
    res = FastFlightsClient(settings, fetch=lambda q: rl, sleep=lambda s: None).search(REQ)
    assert all("Air India" not in o.airlines and "AI" not in o.airlines for o in res.offers)


def test_unmapped_airline_name_keeps_the_raw_name(caplog):
    rl = build()
    rl.metadata.airlines = [a for a in rl.metadata.airlines if a.name != "Emirates"]
    with caplog.at_level("DEBUG", logger="vacation_planner.providers.fast_flights"):
        offers = parse_results(rl, REQ, "https://g/url", price_is_total=True)
    assert ["Emirates"] in [o.airlines for o in offers]
    assert "Emirates" in caplog.text
    # A missing code is routine on Google's pages; it is a note, not a warning.
    assert [r.levelname for r in caplog.records] == ["DEBUG"]


def test_a_known_airline_keeps_its_code_when_the_metadata_omits_it(caplog):
    """The page metadata regularly drops Edelweiss, Discover, LH City and Transavia."""
    rl = build()
    rl[2].airlines = ["Edelweiss Air"]
    rl.metadata.airlines = [a for a in rl.metadata.airlines if a.name != "Emirates"]
    with caplog.at_level("DEBUG", logger="vacation_planner.providers.fast_flights"):
        offers = parse_results(rl, REQ, "https://g/url", price_is_total=True)
    assert ["WK"] in [o.airlines for o in offers]
    assert caplog.records == []          # a known airline is no surprise at all


def test_search_drops_a_night_stop(settings):
    """The Emirates itinerary waits in DXB from 23:05 to 03:30."""
    offers = parse_results(build(), REQ, "https://g/url", price_is_total=True)
    assert 6100 in [o.price_total for o in offers]          # parsing keeps it
    res = FastFlightsClient(settings, fetch=lambda q: build(), sleep=lambda s: None).search(REQ)
    assert 6100 not in [o.price_total for o in res.offers]  # the client drops it


def test_pause_comes_from_the_provider_entry(config_dir):
    settings = load_config(config_dir, env={}).settings
    sleeps = []
    FastFlightsClient(settings, fetch=lambda q: build(), sleep=sleeps.append).search(REQ)
    assert sleeps == [5.0]


def test_pause_defaults_to_zero_when_fast_flights_is_not_listed(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"    - \{ name: fast_flights.*\n", "", st.read_text()))
    settings = load_config(config_dir, env={}).settings
    sleeps = []
    FastFlightsClient(settings, fetch=lambda q: build(), sleep=sleeps.append).search(REQ)
    assert sleeps == [0.0]
