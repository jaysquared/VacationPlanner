from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import httpx

from ..config import Settings
from ..models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from .base import ProviderError, QuotaExhausted, filter_excluded, per_person

URL = "https://serpapi.com/search.json"
TRAVEL_CLASS = {SeatClass.ECONOMY: "1", SeatClass.BUSINESS: "3"}
BACKOFF = [2, 4]


def airline_codes(itinerary: dict) -> list[str]:
    codes = []
    for leg in itinerary.get("flights", []):
        fn = leg.get("flight_number") or ""
        code = fn.split(" ")[0].strip() if fn else (leg.get("airline") or "?")
        if code not in codes:
            codes.append(code)
    return codes


def parse_response(data: dict, req: SearchRequest, price_is_total: bool) -> list[Offer]:
    insights = data.get("price_insights") or {}
    rng = insights.get("typical_price_range") or [None, None]
    url = (data.get("search_metadata") or {}).get("google_flights_url", "")
    offers = []
    for it in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = it.get("flights") or []
        if not legs or it.get("price") is None:
            continue
        total, pp = per_person(it["price"], req, price_is_total)
        offers.append(Offer(
            provider=Provider.SERPAPI, price_total=total, currency="EUR", per_person=pp,
            airlines=airline_codes(it), stops=len(legs) - 1,
            duration_minutes=int(it.get("total_duration") or sum(l.get("duration", 0) for l in legs)),
            departs_at=legs[0]["departure_airport"]["time"], arrives_at=legs[-1]["arrival_airport"]["time"],
            price_level=insights.get("price_level"), typical_low=rng[0], typical_high=rng[1],
            google_url=url, raw=it,
        ))
    return offers


def _is_quota(status: int, body: dict | None) -> bool:
    msg = ((body or {}).get("error") or "").lower()
    return status == 429 or "out of searches" in msg or "limit" in msg


class SerpApiClient:
    provider = Provider.SERPAPI

    def __init__(self, api_key: str, settings: Settings, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key
        self.settings = settings
        self.http = http or httpx.Client(timeout=60)
        self.sleep = sleep

    def params_for(self, req: SearchRequest) -> dict[str, str]:
        p = {
            "engine": "google_flights", "departure_id": req.origin, "arrival_id": req.destination,
            "outbound_date": req.outbound_date.isoformat(), "return_date": req.return_date.isoformat(),
            "type": "1", "travel_class": TRAVEL_CLASS[req.seat], "adults": str(req.adults),
            "children": str(req.children), "currency": "EUR", "hl": "en", "gl": "de",
            "stops": str(self.settings.max_stops + 1), "api_key": self.api_key,
        }
        if self.settings.excluded_airlines:
            p["exclude_airlines"] = ",".join(self.settings.excluded_airlines)
        return p

    def _fetch(self, req: SearchRequest) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            if attempt:
                self.sleep(BACKOFF[attempt - 1])
            try:
                r = self.http.get(URL, params=self.params_for(req))
                body = None
                try:
                    body = r.json()
                except ValueError:
                    pass
                if _is_quota(r.status_code, body):
                    raise QuotaExhausted(str((body or {}).get("error") or r.status_code))
                if r.status_code >= 400 or body is None or body.get("error"):
                    last = ProviderError(f"serpapi {r.status_code}: {(body or {}).get('error')}")
                    continue
                return body
            except QuotaExhausted:
                raise
            except httpx.HTTPError as e:
                last = ProviderError(f"serpapi network error: {e}")
        raise last or ProviderError("serpapi: unknown failure")

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        data = self._fetch(req)
        raw_path = None
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            f = raw_dir / f"serpapi_{req.origin}-{req.destination}_{req.outbound_date}_{req.return_date}_{req.seat.value}.json"
            f.write_text(json.dumps(data))
            raw_path = str(f)
        try:
            parsed = parse_response(data, req, self.settings.providers.price_is_total)
        except Exception as e:   # layout change: the raw JSON above is kept for a fixture
            raise ProviderError(f"serpapi: parse failed: {type(e).__name__}: {e}") from e
        offers = filter_excluded(parsed, self.settings.excluded_airlines)
        return SearchResult(req, self.provider, offers, raw_path)
