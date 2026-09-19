import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError, QuotaExhausted
from vacation_planner.providers.serpapi import SerpApiClient, airline_codes, parse_response

FIX = Path(__file__).parent / "fixtures" / "serpapi_ham_bkk.json"
REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


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
    it = json.loads(FIX.read_text())["best_flights"][0]
    assert airline_codes(it) == ["LH", "TG"]


def test_parse_response_reads_both_lists_and_insights():
    offers = parse_response(json.loads(FIX.read_text()), REQ, price_is_total=True)
    assert [o.price_total for o in offers] == [5940, 4100, 6420]
    o = offers[0]
    assert o.provider is Provider.SERPAPI and o.per_person == 1980 and o.stops == 1
    assert o.duration_minutes == 875 and o.departs_at == "2026-10-17 10:35" and o.arrives_at == "2026-10-18 06:10"
    assert o.price_level == "low" and o.typical_low == 7200 and o.typical_high == 9800
    assert o.google_url.endswith("tfs=FIXTURE")


def test_search_filters_excluded_airlines_and_saves_raw(client, httpx_mock, tmp_path):
    httpx_mock.add_response(json=json.loads(FIX.read_text()))
    res = client.search(REQ, raw_dir=tmp_path)
    assert [o.price_total for o in res.offers] == [5940, 6420]   # AI itinerary dropped
    assert res.raw_path and Path(res.raw_path).exists()
    assert json.loads(Path(res.raw_path).read_text())["search_metadata"]["status"] == "Success"


def test_quota_error_is_not_retried(client, httpx_mock):
    httpx_mock.add_response(status_code=429, json={"error": "Your account has run out of searches."})
    with pytest.raises(QuotaExhausted):
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
    httpx_mock.add_response(json=json.loads(FIX.read_text()))
    assert len(client.search(REQ).offers) == 2


def test_malformed_payload_raises_provider_error_and_keeps_raw(client, httpx_mock, tmp_path):
    data = json.loads(FIX.read_text())
    data["best_flights"] = {"price": 1}          # layout change: object instead of list
    httpx_mock.add_response(json=data)
    with pytest.raises(ProviderError, match="serpapi: parse failed: TypeError"):
        client.search(REQ, raw_dir=tmp_path)
    assert list(tmp_path.glob("serpapi_*.json"))   # raw response kept for the fixture


def test_client_reads_its_own_price_flag(config_dir, httpx_mock):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"( +)serpapi: true", r"\1serpapi: false", st.read_text()))
    cfg = load_config(config_dir, env={})
    c = SerpApiClient("KEY", cfg.settings, sleep=lambda s: None)
    httpx_mock.add_response(json=json.loads(FIX.read_text()))
    o = c.search(REQ).offers[0]
    assert (o.per_person, o.price_total) == (5940, 5940 * 3)   # per-person price, 3 travellers


def test_rate_limit_wording_is_a_transient_error_not_quota(client, httpx_mock):
    """A bare "limit" in the message is not the monthly quota: retry it, do not kill the primary."""
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
