import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import AuthError, ProviderError, QuotaExhausted, redact
from vacation_planner.providers.searchapi import SearchApiClient, parse_response

FIX = Path(__file__).parent / "fixtures" / "searchapi_ham_bkk.json"
REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)

PRICES = [12330, 16690, 16691, 16824, 23062]

AI_ITINERARY = {
    "flights": [{"departure_airport": {"id": "HAM", "date": "2026-10-17", "time": "06:00"},
                 "arrival_airport": {"id": "BKK", "date": "2026-10-18", "time": "06:00"},
                 "duration": 900, "airline": "Air India", "flight_number": "AI 120"}],
    "total_duration": 900, "price": 9000,
}


def fixture() -> dict:
    return json.loads(FIX.read_text())


@pytest.fixture
def client(config_dir):
    cfg = load_config(config_dir, env={})
    sleeps = []
    c = SearchApiClient("KEY", cfg.settings, sleep=sleeps.append)
    c._sleeps = sleeps
    return c


def test_params_mapping(client):
    p = client.params_for(REQ)
    assert p["engine"] == "google_flights" and p["departure_id"] == "HAM" and p["arrival_id"] == "BKK"
    assert p["outbound_date"] == "2026-10-17" and p["return_date"] == "2026-10-31"
    assert p["flight_type"] == "round_trip" and p["travel_class"] == "business"
    assert p["adults"] == "2" and p["children"] == "1"
    assert p["currency"] == "EUR" and p["hl"] == "en" and p["gl"] == "de"
    assert p["stops"] == "one_stop_or_fewer" and p["exclude_airlines"] == "AI"
    assert "api_key" not in p   # the key travels in the Authorization header, not the URL


def test_key_is_sent_as_a_bearer_header_and_never_in_the_url(client, httpx_mock):
    httpx_mock.add_response(json=fixture())
    client.search(REQ)
    sent = httpx_mock.get_requests()[0]
    assert sent.headers["Authorization"] == "Bearer KEY"
    assert "KEY" not in str(sent.url)


def test_errors_never_carry_the_key(client, httpx_mock):
    httpx_mock.add_response(status_code=500, json={"error": "failed for api_key=KEY"}, is_reusable=True)
    with pytest.raises(ProviderError) as e:
        client.search(REQ)
    assert "KEY" not in str(e.value) and "***" in str(e.value)


@pytest.mark.parametrize("max_stops, expected", [(0, "nonstop"), (1, "one_stop_or_fewer"),
                                                 (2, "two_stops_or_fewer"), (3, "any")])
def test_stops_mapping(config_dir, max_stops, expected):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_stops: 1", f"max_stops: {max_stops}"))
    cfg = load_config(config_dir, env={})
    assert SearchApiClient("KEY", cfg.settings).params_for(REQ)["stops"] == expected


def test_economy_travel_class(client):
    econ = SearchRequest("pfingsten-2027", "HAM", "PMI", date(2027, 5, 22), date(2027, 5, 29),
                         SeatClass.ECONOMY, 2, 1)
    assert client.params_for(econ)["travel_class"] == "economy"


def test_airline_codes_come_from_the_flight_numbers():
    from vacation_planner.providers.searchapi import airline_codes
    assert airline_codes(fixture()["best_flights"][0]) == ["DE"]
    assert airline_codes(fixture()["other_flights"][0]) == ["EK"]


def test_parse_response_reads_both_lists_and_insights():
    offers = parse_response(fixture(), REQ, price_is_total=True)
    assert [o.price_total for o in offers] == PRICES
    o = offers[0]
    assert o.provider is Provider.SEARCHAPI and o.per_person == 4110 and o.stops == 1
    assert o.airlines == ["DE"] and o.duration_minutes == 870
    assert o.departs_at == "2026-10-17 17:05" and o.arrives_at == "2026-10-18 12:35"
    assert o.price_level == "typical" and o.typical_low == 8900 and o.typical_high == 13000
    assert o.google_url.startswith("https://www.google.com/travel/flights")


def test_parse_response_without_insights():
    data = fixture()
    del data["price_insights"]
    o = parse_response(data, REQ, price_is_total=True)[0]
    assert (o.price_level, o.typical_low, o.typical_high) == (None, None, None)


def test_search_filters_excluded_airlines_and_saves_raw(client, httpx_mock, tmp_path):
    data = fixture()
    data["other_flights"].append(copy.deepcopy(AI_ITINERARY))   # the API ignored exclude_airlines
    httpx_mock.add_response(json=data)
    res = client.search(REQ, raw_dir=tmp_path)
    assert [o.price_total for o in res.offers] == PRICES        # the AI itinerary is dropped
    assert res.raw_path == str(tmp_path / "searchapi_HAM-BKK_2026-10-17_2026-10-31_business.json")
    assert json.loads(Path(res.raw_path).read_text())["search_metadata"]["status"] == "Success"


def test_bad_key_is_an_auth_error_and_is_not_retried(client, httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": "Invalid API key."})
    with pytest.raises(AuthError):
        client.search(REQ)
    assert client._sleeps == []


def test_forbidden_is_an_auth_error(client, httpx_mock):
    httpx_mock.add_response(status_code=403, json={"error": "Forbidden."})
    with pytest.raises(AuthError) as e:
        client.search(REQ)
    assert isinstance(e.value, QuotaExhausted)   # dead for the rest of the run, like a quota
    assert client._sleeps == []


@pytest.mark.parametrize("status", [402, 429])
def test_quota_status_is_not_retried(client, httpx_mock, status):
    httpx_mock.add_response(status_code=status, json={"error": "Payment required."})
    with pytest.raises(QuotaExhausted):
        client.search(REQ)
    assert client._sleeps == []


@pytest.mark.parametrize("msg", ["You have no credits left.", "You are out of searches for this month."])
def test_quota_wording_is_quota(client, httpx_mock, msg):
    httpx_mock.add_response(status_code=400, json={"error": msg})
    with pytest.raises(QuotaExhausted):
        client.search(REQ)
    assert client._sleeps == []


def test_transient_error_retries_then_raises(client, httpx_mock):
    for _ in range(3):
        httpx_mock.add_response(status_code=500, json={"error": "Internal server error."})
    with pytest.raises(ProviderError) as e:
        client.search(REQ)
    assert not isinstance(e.value, QuotaExhausted)
    assert client._sleeps == [2, 4]


def test_non_json_body_is_transient(client, httpx_mock):
    for _ in range(3):
        httpx_mock.add_response(status_code=200, text="<html>gateway</html>")
    with pytest.raises(ProviderError):
        client.search(REQ)
    assert client._sleeps == [2, 4]


def test_transient_then_success(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_response(json=fixture())
    res = client.search(REQ)
    assert len(res.offers) == 5 and client._sleeps == [2]


def test_malformed_payload_raises_provider_error_and_keeps_raw(client, httpx_mock, tmp_path):
    data = fixture()
    data["best_flights"] = {"price": 1}          # layout change: object instead of list
    httpx_mock.add_response(json=data)
    with pytest.raises(ProviderError, match="searchapi: parse failed: TypeError"):
        client.search(REQ, raw_dir=tmp_path)
    assert list(tmp_path.glob("searchapi_*.json"))   # raw response kept for the fixture


def test_client_reads_its_own_price_flag(config_dir, httpx_mock):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("searchapi: true", "searchapi: false"))
    cfg = load_config(config_dir, env={})
    c = SearchApiClient("KEY", cfg.settings, sleep=lambda s: None)
    httpx_mock.add_response(json=fixture())
    o = c.search(REQ).offers[0]
    assert (o.per_person, o.price_total) == (12330, 12330 * 3)   # per-person price, 3 travellers


def test_rate_limit_wording_is_a_transient_error_not_quota(client, httpx_mock):
    """A bare "limit" in the message is not the exhausted plan: retry it, keep the provider."""
    for _ in range(3):
        httpx_mock.add_response(status_code=400, json={"error": "Rate limit exceeded for this endpoint."})
    with pytest.raises(ProviderError) as e:
        client.search(REQ)
    assert not isinstance(e.value, QuotaExhausted)
    assert client._sleeps == [2, 4]


def test_running_out_of_range_is_not_quota(client, httpx_mock):
    """Only the plan wordings kill the provider; "out of" on its own must stay retryable."""
    for _ in range(3):
        httpx_mock.add_response(status_code=400, json={"error": "Date is out of range."})
    with pytest.raises(ProviderError) as e:
        client.search(REQ)
    assert not isinstance(e.value, QuotaExhausted)


def test_redact_replaces_every_secret():
    assert redact("a=KEY b=OTHER", ["KEY", "", None or ""]) == "a=*** b=OTHER"
