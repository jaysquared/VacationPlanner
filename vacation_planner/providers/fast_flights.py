from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query
from fast_flights.fetcher import URL as _GOOGLE_FLIGHTS_URL
from fast_flights.integrations.base import FetchIntegration
from fast_flights.parser import ResultList, parse
from fast_flights.querying import Query
from primp import Client

from ..config import Settings
from ..models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from .base import ProviderError, filter_excluded, per_person

SEAT = {SeatClass.ECONOMY: "economy", SeatClass.BUSINESS: "business"}

CONSENT_COOKIE = "SOCS=CAI"   # skips Google's EU consent interstitial


class ConsentFetch(FetchIntegration):
    """Fetch the Google Flights page with a consent cookie so EU IPs get the data page, not the consent wall."""

    def __init__(self, client_factory: Callable[..., Any] = Client):
        self._client_factory = client_factory

    def fetch_html(self, q: Query | str, /) -> str:
        client = self._client_factory(
            impersonate="chrome_145", impersonate_os="macos", referer=True,
            cookie_store=True, headers={"Cookie": CONSENT_COOKIE},
        )
        params = q.params() if isinstance(q, Query) else {"q": q}
        return client.get(_GOOGLE_FLIGHTS_URL, params=params).text


def fetch_with_consent(q: Query) -> ResultList:
    return parse(ConsentFetch().fetch_html(q))


def _fmt(sd) -> str:
    (y, m, d), (h, mi) = sd.date, sd.time
    return f"{y:04d}-{m:02d}-{d:02d} {h:02d}:{mi:02d}"


def _minutes_between(a, b) -> int:
    da = datetime(*a.date, *a.time)
    db = datetime(*b.date, *b.time)
    return int((db - da).total_seconds() // 60)


def parse_results(results: ResultList, req: SearchRequest, url: str, price_is_total: bool) -> list[Offer]:
    meta = getattr(results, "metadata", None)
    name_to_code = {a.name: a.code for a in (meta.airlines if meta else [])}
    offers = []
    for it in results:
        if not it.flights or it.price is None:
            continue
        total, pp = per_person(it.price, req, price_is_total)
        codes = []
        for name in it.airlines:
            code = name_to_code.get(name, name)
            if code not in codes:
                codes.append(code)
        first, last = it.flights[0], it.flights[-1]
        offers.append(Offer(
            provider=Provider.FAST_FLIGHTS, price_total=total, currency="EUR", per_person=pp,
            airlines=codes, stops=len(it.flights) - 1,
            duration_minutes=_minutes_between(first.departure, last.arrival),
            departs_at=_fmt(first.departure), arrives_at=_fmt(last.arrival),
            price_level=None, typical_low=None, typical_high=None, google_url=url,
            raw={"type": it.type, "airlines": it.airlines,
                 "legs": [{"from": l.from_airport.code, "to": l.to_airport.code, "dep": _fmt(l.departure),
                           "arr": _fmt(l.arrival), "duration": l.duration, "plane": l.plane_type} for l in it.flights]},
        ))
    return offers


class FastFlightsClient:
    provider = Provider.FAST_FLIGHTS

    def __init__(self, settings: Settings, fetch: Callable[[Query], ResultList] = fetch_with_consent,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.fetch = fetch
        self.sleep = sleep

    def build_query(self, req: SearchRequest) -> Query:
        ms = self.settings.max_stops
        return create_query(
            flights=[
                FlightQuery(date=req.outbound_date.isoformat(), from_airport=req.origin, to_airport=req.destination, max_stops=ms),
                FlightQuery(date=req.return_date.isoformat(), from_airport=req.destination, to_airport=req.origin, max_stops=ms),
            ],
            seat=SEAT[req.seat], trip="round-trip",
            passengers=Passengers(adults=req.adults, children=req.children),
            currency="EUR", language="en-US",
        )

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        q = self.build_query(req)
        self.sleep(self.settings.providers.backup_pause_seconds)
        try:
            results = self.fetch(q)
        except FlightsNotFound as e:
            raise ProviderError(f"fast_flights: no flights: {e}") from e
        except Exception as e:  # network, parse, layout change
            raise ProviderError(f"fast_flights: {type(e).__name__}: {e}") from e
        if not results:
            raise ProviderError("fast_flights: empty result")
        offers = filter_excluded(parse_results(results, req, q.url(), self.settings.providers.price_is_total),
                                 self.settings.excluded_airlines)
        return SearchResult(req, self.provider, offers, None)
