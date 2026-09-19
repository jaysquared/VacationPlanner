import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import AuthError, ProviderError, QuotaExhausted
from vacation_planner.providers.serpapi import SerpApiClient, airline_codes, parse_response

FIX = Path(__file__).parent / "fixtures" / "serpapi_ham_bkk.json"
REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)

PRICES = [13830, 16421, 16648, 16824, 16949, 18642, 22913, 23062]

AI_ITINERARY = {
    "flights": [{"departure_airport": {"id": "HAM", "time": "2026-10-17 06:00"},
                 "arrival_airport": {"id": "BKK", "time": "2026-10-18 06:00"},
                 "duration": 900, "airline": "Air India", "flight_number": "AI 120"}],
    "total_duration": 900, "price": 9000,
}


def fixture() -> dict:
    return json.loads(FIX.read_text())


@pytest.fixture
def client(config_dir):
    cfg = load_config(config_dir, env={})
    sleeps = []
    c = SerpApiClient("KEY", cfg.settings, sleep=sleeps.append)
    c._sleeps = sleeps
    return c


def test_params_mapping(client):
    p = client.params_for(REQ)
    assert p["engine"] == "google_flights" and p["departure_id"] == "HAM" and p["arrival_id"] == "BKK"
    assert p["outbound_date"] == "2026-10-17" and p["return_date"] == "2026-10-31" and p["type"] == "1"
    assert p["travel_class"] == "3" and p["adults"] == "2" and p["children"] == "1"
    assert p["currency"] == "EUR" and p["stops"] == "2" and p["exclude_airlines"] == "AI"
    assert p["api_key"] == "KEY"


def test_airline_codes():
    assert airline_codes(fixture()["best_flights"][0]) == ["DE"]
    assert airline_codes(fixture()["other_flights"][1]) == ["EW", "LX"]


def test_parse_response_reads_both_lists():
    offers = parse_response(fixture(), REQ, price_is_total=True)
    assert [o.price_total for o in offers] == PRICES
    o = offers[0]
    assert o.provider is Provider.SERPAPI and o.per_person == 4610 and o.stops == 1
    assert o.airlines == ["DE"]
    assert o.duration_minutes == 870 and o.departs_at == "2026-10-17 17:05" and o.arrives_at == "2026-10-18 12:35"
    assert o.google_url.startswith("https://www.google.com/travel/flights")


def test_parse_response_without_price_insights_has_no_level_or_range():
    o = parse_response(fixture(), REQ, price_is_total=True)[0]
    assert (o.price_level, o.typical_low, o.typical_high) == (None, None, None)


def test_parse_response_reads_price_insights_when_present():
    data = fixture()
    data["price_insights"] = {"lowest_price": 13830, "price_level": "low", "typical_price_range": [7200, 9800]}
    o = parse_response(data, REQ, price_is_total=True)[0]
    assert o.price_level == "low" and o.typical_low == 7200 and o.typical_high == 9800


def test_search_filters_excluded_airlines_and_saves_raw(client, httpx_mock, tmp_path):
    data = fixture()
    data["other_flights"].append(copy.deepcopy(AI_ITINERARY))   # the API ignored exclude_airlines
    httpx_mock.add_response(json=data)
    res = client.search(REQ, raw_dir=tmp_path)
    assert [o.price_total for o in res.offers] == PRICES        # the AI itinerary is dropped
    assert res.raw_path and Path(res.raw_path).exists()
    assert json.loads(Path(res.raw_path).read_text())["search_metadata"]["status"] == "Success"


def test_quota_error_is_not_retried(client, httpx_mock):
    httpx_mock.add_response(status_code=429, json={"error": "Your account has run out of searches."})
    with pytest.raises(QuotaExhausted):
        client.search(REQ)
    assert client._sleeps == []


def test_bad_key_is_an_auth_error_and_is_not_retried(client, httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": "Invalid API key."})
    with pytest.raises(AuthError):
        client.search(REQ)
    assert client._sleeps == []


def test_forbidden_is_an_auth_error(client, httpx_mock):
    httpx_mock.add_response(status_code=403, json={"error": "Forbidden."})
    with pytest.raises(AuthError):
        client.search(REQ)
    assert client._sleeps == []


def test_transient_error_retries_then_raises(client, httpx_mock):
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    with pytest.raises(ProviderError):
        client.search(REQ)
    assert client._sleeps == [2, 4]


def test_transient_then_success(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_response(json=fixture())
    assert len(client.search(REQ).offers) == 8


def test_malformed_payload_raises_provider_error_and_keeps_raw(client, httpx_mock, tmp_path):
    data = fixture()
    data["best_flights"] = {"price": 1}          # layout change: object instead of list
    httpx_mock.add_response(json=data)
    with pytest.raises(ProviderError, match="serpapi: parse failed: TypeError"):
        client.search(REQ, raw_dir=tmp_path)
    assert list(tmp_path.glob("serpapi_*.json"))   # raw response kept for the fixture


def test_client_reads_its_own_price_flag(config_dir, httpx_mock):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("{ serpapi: true,", "{ serpapi: false,"))
    cfg = load_config(config_dir, env={})
    c = SerpApiClient("KEY", cfg.settings, sleep=lambda s: None)
    httpx_mock.add_response(json=fixture())
    o = c.search(REQ).offers[0]
    assert (o.per_person, o.price_total) == (13830, 13830 * 3)   # per-person price, 3 travellers


def test_rate_limit_wording_is_a_transient_error_not_quota(client, httpx_mock):
    """A bare "limit" in the message is not the monthly quota: retry it, do not kill the provider."""
    for _ in range(3):
        httpx_mock.add_response(status_code=400, json={"error": "Rate limit exceeded for this endpoint."})
    with pytest.raises(ProviderError) as e:
        client.search(REQ)
    assert not isinstance(e.value, QuotaExhausted)
    assert client._sleeps == [2, 4]


def test_quota_wording_without_429_is_quota(client, httpx_mock):
    httpx_mock.add_response(status_code=200, json={"error": "You have run out of searches this month."})
    with pytest.raises(QuotaExhausted):
        client.search(REQ)
