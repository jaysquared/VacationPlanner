from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import httpx

from ..config import Settings
from ..models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from .base import AuthError, ProviderError, QuotaExhausted, filter_excluded, per_person, redact
from .serpapi import airline_codes   # same `flight_number` shape on the legs

URL = "https://www.searchapi.io/api/v1/search"
TRAVEL_CLASS = {SeatClass.ECONOMY: "economy", SeatClass.BUSINESS: "business"}
STOPS = {0: "nonstop", 1: "one_stop_or_fewer", 2: "two_stops_or_fewer"}
BACKOFF = [2, 4]

__all__ = ["SearchApiClient", "airline_codes", "parse_response"]


def _when(airport: dict) -> str:
    # SearchApi splits what SerpApi joins; the stored format stays "YYYY-MM-DD HH:MM".
    return f"{airport['date']} {airport['time']}"


def parse_response(data: dict, req: SearchRequest, price_is_total: bool) -> list[Offer]:
    insights = data.get("price_insights") or {}
    rng = insights.get("typical_price_range") or {}
    url = (data.get("search_metadata") or {}).get("request_url", "")
    offers = []
    for it in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = it.get("flights") or []
        if not legs or it.get("price") is None:
            continue
        total, pp = per_person(it["price"], req, price_is_total)
        offers.append(Offer(
            provider=Provider.SEARCHAPI, price_total=total, currency="EUR", per_person=pp,
            airlines=airline_codes(it), stops=len(legs) - 1,
            duration_minutes=int(it.get("total_duration") or sum(l.get("duration", 0) for l in legs)),
            departs_at=_when(legs[0]["departure_airport"]), arrives_at=_when(legs[-1]["arrival_airport"]),
            price_level=insights.get("price_level"),
            typical_low=rng.get("low_price"), typical_high=rng.get("high_price"),
            google_url=url, raw=it,
        ))
    return offers


QUOTA_WORDINGS = ("credit", "out of searches", "out of credits", "run out of")


def _is_quota(status: int, body: dict | None) -> bool:
    # Only the exhausted plan, not every message mentioning a "limit" or something being
    # "out of range": anything else is transient and must stay retryable instead of killing
    # the provider for the whole run.
    msg = ((body or {}).get("error") or "").lower()
    return status in (402, 429) or any(w in msg for w in QUOTA_WORDINGS)


class SearchApiClient:
    provider = Provider.SEARCHAPI

    def __init__(self, api_key: str, settings: Settings, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key
        self.settings = settings
        self.http = http or httpx.Client(timeout=60)
        self.sleep = sleep

    def _safe(self, text: str) -> str:
        # The key reaches messages through echoed error bodies and httpx URLs; both get stored.
        return redact(text, [self.api_key])

    def headers(self) -> dict[str, str]:
        # Bearer instead of ?api_key=: a query key ends up in httpx logs and in CI job output.
        return {"Authorization": f"Bearer {self.api_key}"}

    def params_for(self, req: SearchRequest) -> dict[str, str]:
        p = {
            "engine": "google_flights", "departure_id": req.origin, "arrival_id": req.destination,
            "outbound_date": req.outbound_date.isoformat(), "return_date": req.return_date.isoformat(),
            "flight_type": "round_trip", "travel_class": TRAVEL_CLASS[req.seat],
            "adults": str(req.adults), "children": str(req.children),
            "currency": "EUR", "hl": "en", "gl": "de",
            "stops": STOPS.get(self.settings.max_stops, "any"),
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
                r = self.http.get(URL, params=self.params_for(req), headers=self.headers())
                body = None
                try:
                    body = r.json()
                except ValueError:
                    pass
                err = (body or {}).get("error")
                if r.status_code in (401, 403):
                    raise AuthError(self._safe(f"searchapi {r.status_code}: {err or 'credentials rejected'}"))
                if _is_quota(r.status_code, body):
                    raise QuotaExhausted(self._safe(str(err or r.status_code)))
                if r.status_code >= 400 or body is None or err:
                    last = ProviderError(self._safe(f"searchapi {r.status_code}: {err}"))
                    continue
                return body
            except QuotaExhausted:   # AuthError too: neither is worth a retry
                raise
            except httpx.HTTPError as e:
                last = ProviderError(self._safe(f"searchapi network error: {e}"))
        raise last or ProviderError("searchapi: unknown failure")

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        data = self._fetch(req)
        raw_path = None
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            f = raw_dir / f"searchapi_{req.origin}-{req.destination}_{req.outbound_date}_{req.return_date}_{req.seat.value}.json"
            f.write_text(json.dumps(data))
            raw_path = str(f)
        try:
            parsed = parse_response(data, req, self.settings.providers.price_is_total[Provider.SEARCHAPI])
        except Exception as e:   # layout change: the raw JSON above is kept for a fixture
            raise ProviderError(self._safe(f"searchapi: parse failed: {type(e).__name__}: {e}")) from e
        offers = filter_excluded(parsed, self.settings.excluded_airlines)
        return SearchResult(req, self.provider, offers, raw_path)
