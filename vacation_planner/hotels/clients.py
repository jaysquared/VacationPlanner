"""Hotel price sources: Booking.com and Hilton through Apify actors, Google Hotels through SerpApi.

Every parser checks that the response is for the requested dates and guests; anything else
is `no_rates`, never a price. Tested live 2026-11-03/05; the fixtures are those responses.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

import httpx

from ..models import HotelRate, HotelResult, HotelSource, HotelStay
from ..providers.base import AuthError, ProviderError, redact

APIFY_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
BOOKING_ACTOR = "voyager~booking-scraper"
HILTON_ACTOR = "mu0i~hotel-rates-award-points"
SERPAPI_URL = "https://serpapi.com/search.json"
#: An actor run is synchronous and a Booking.com page takes a while to load.
APIFY_TIMEOUT = 300
#: Google's wrong-date fallback prices one night; a real total is about nightly x nights.
TOTAL_TOLERANCE = 0.15


class HotelClient(Protocol):
    source: HotelSource

    def check(self, stay: HotelStay) -> HotelResult: ...


def _no_rates(stay: HotelStay, note: str) -> HotelResult:
    return HotelResult(stay, "no_rates", [], note)


def _non_refundable_name(name: str | None) -> bool:
    # Hilton reports its Advance Purchase plans as refundable; they are prepaid and are not.
    return "advance purchase" in (name or "").lower()


class ApifyActor:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self.token = token
        self.http = http or httpx.Client(timeout=APIFY_TIMEOUT)

    def run(self, actor: str, payload: dict) -> list[dict]:
        try:
            r = self.http.post(APIFY_URL.format(actor=actor), params={"token": self.token}, json=payload,
                               timeout=APIFY_TIMEOUT)
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"apify {actor}: network error: {e}", [self.token])) from e
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code in (401, 403):
            raise AuthError(redact(f"apify {actor}: {r.status_code} token rejected", [self.token]))
        if r.status_code >= 400 or not isinstance(body, list):
            err = (body or {}).get("error") if isinstance(body, dict) else None
            detail = f"{err.get('type')}: {err.get('message')}" if isinstance(err, dict) else str(err or r.status_code)
            raise ProviderError(redact(f"apify {actor}: {detail}", [self.token]))
        return body


# ---- Booking.com ----

def booking_url(base: str, stay: HotelStay) -> str:
    q = {"checkin": stay.check_in.isoformat(), "checkout": stay.check_out.isoformat(),
         "group_adults": stay.adults, "group_children": len(stay.child_ages), "no_rooms": 1,
         "selected_currency": "EUR", "lang": "de"}
    ages = "".join(f"&age={a}" for a in stay.child_ages)   # Booking repeats `age` once per child
    return f"{base}?{urlencode(q)}{ages}"


def parse_booking(items: list[dict], stay: HotelStay) -> HotelResult:
    if not items:
        return _no_rates(stay, "Booking.com returned no hotel")
    h = items[0]
    if (h.get("checkInDate"), h.get("checkOutDate")) != (stay.check_in.isoformat(), stay.check_out.isoformat()):
        return _no_rates(stay, f"Booking.com priced other dates ({h.get('checkInDate')}..{h.get('checkOutDate')})")
    rates = []
    for room in h.get("rooms") or []:
        for o in room.get("options") or []:
            if not o.get("price"):
                continue
            rates.append(HotelRate(
                "booking", "cash", total=round(float(o["price"]), 2), room=room.get("roomType"),
                rate_name=", ".join(o.get("yourChoices") or []) or None,
                refundable=bool(o.get("freeCancellation")), url=h.get("url")))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Booking.com: no room available")


class BookingClient:
    source = HotelSource.BOOKING

    def __init__(self, actor: ApifyActor):
        self.actor = actor

    def check(self, stay: HotelStay) -> HotelResult:
        payload = {"startUrls": [{"url": booking_url(stay.ref, stay)}], "maxItems": 1,
                   "checkIn": stay.check_in.isoformat(), "checkOut": stay.check_out.isoformat(),
                   "rooms": 1, "adults": stay.adults, "children": len(stay.child_ages),
                   "currency": "EUR", "language": "de"}
        return parse_booking(self.actor.run(BOOKING_ACTOR, payload), stay)


# ---- Hilton ----

def _matches_stay(row: dict, stay: HotelStay) -> bool:
    ages = row.get("children_ages")
    got = tuple(int(a) for a in str(ages).split(",")) if ages else ()
    return (row.get("checkin"), row.get("checkout"), row.get("adults"), got) == (
        stay.check_in.isoformat(), stay.check_out.isoformat(), stay.adults, stay.child_ages)


def parse_hilton(rows: list[dict], stay: HotelStay) -> HotelResult:
    rows = [r for r in rows if r.get("status") == "available"]
    if not rows:
        return _no_rates(stay, "Hilton: nothing available")
    if not all(_matches_stay(r, stay) for r in rows):
        return _no_rates(stay, "Hilton answered for other dates or guests")
    rates: list[HotelRate] = []
    for r in rows:
        if stay.mode == "award":
            if not r.get("points_total"):
                continue
            std = (r.get("rate_name") or "").lower().startswith("standard")
            rates.append(HotelRate("direct", "points", points=int(r["points_total"]), room=r.get("room_name"),
                                   rate_name=r.get("rate_name"), award_type="standard" if std else "premium"))
        elif stay.mode == "parity":
            if r.get("channel") == "direct" or not r.get("public_total"):
                continue   # the `rates` check prices direct room by room
            rates.append(HotelRate(r["channel"], "cash", total=float(r["public_total"]),
                                   rate_name=r.get("rate_name"), refundable=r.get("refundable")))
        else:   # rates
            if not r.get("price_total"):
                continue   # award plans appear here with price 0
            name = r.get("rate_name")
            refundable = bool(r.get("refundable")) and not _non_refundable_name(name)
            rates.append(HotelRate("direct", "cash", total=float(r["price_total"]), room=r.get("room_name"),
                                   rate_name=name, refundable=refundable))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Hilton: no priced rate")


class HiltonClient:
    source = HotelSource.HILTON

    def __init__(self, actor: ApifyActor):
        self.actor = actor

    def check(self, stay: HotelStay) -> HotelResult:
        payload = {"mode": stay.mode, "chain": "hilton", "hotelCodes": [stay.ref],
                   "checkin": stay.check_in.isoformat(), "los": stay.nights, "adults": stay.adults,
                   "childrenAges": list(stay.child_ages), "currency": "EUR", "maxCredits": 40}
        return parse_hilton(self.actor.run(HILTON_ACTOR, payload), stay)


# ---- Google Hotels (SerpApi) ----

def _lowest(block: dict | None) -> float | None:
    v = (block or {}).get("extracted_lowest")
    return float(v) if v else None


def parse_google(data: dict, stay: HotelStay) -> HotelResult:
    nightly, total = _lowest(data.get("rate_per_night")), _lowest(data.get("total_rate"))
    if nightly is None or total is None:
        return _no_rates(stay, "Google Hotels: no price")
    if stay.nights > 1 and abs(total - nightly * stay.nights) > TOTAL_TOLERANCE * nightly * stay.nights:
        return _no_rates(stay, f"Google priced other dates ({total:,.0f} total vs {nightly:,.0f} per night)")
    rates: list[HotelRate] = []
    for src in data.get("prices") or []:
        t = _lowest(src.get("total_rate"))
        if t:
            rates.append(HotelRate(src.get("source") or "google", "cash", total=t, url=src.get("link")))
    for src in data.get("featured_prices") or []:
        for room in src.get("rooms") or []:
            t = _lowest(room.get("total_rate"))
            if not t:
                continue
            free = [x.get("free_cancellation") for x in room.get("rates") or [] if "free_cancellation" in x]
            rates.append(HotelRate(src.get("source") or "google", "cash", total=t, room=room.get("name"),
                                   refundable=(any(free) if free else None), url=room.get("link")))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Google Hotels: no price")


class GoogleHotelsClient:
    source = HotelSource.GOOGLE

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=60)

    def check(self, stay: HotelStay) -> HotelResult:
        params = {"engine": "google_hotels", "q": stay.watch_id, "property_token": stay.ref,
                  "check_in_date": stay.check_in.isoformat(), "check_out_date": stay.check_out.isoformat(),
                  "adults": str(stay.adults), "currency": "EUR", "gl": "de", "hl": "de", "api_key": self.api_key}
        if stay.child_ages:
            params["children"] = str(len(stay.child_ages))
            params["children_ages"] = ",".join(str(a) for a in stay.child_ages)
        try:
            r = self.http.get(SERPAPI_URL, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"google hotels: network error: {e}", [self.api_key])) from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if r.status_code in (401, 403):
            raise AuthError(redact(f"google hotels {r.status_code}: {err}", [self.api_key]))
        if err and "hasn't returned any results" in err:
            return _no_rates(stay, "Google Hotels: no results")
        if r.status_code >= 400 or err:
            raise ProviderError(redact(f"google hotels {r.status_code}: {err}", [self.api_key]))
        return parse_google(body, stay)
