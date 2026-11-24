"""Hotel price sources: Booking.com and Hilton through Apify actors, Google Hotels through SerpApi.

Every parser checks that the response is for the requested dates and guests; anything else
is `no_rates`, never a price. Tested live 2026-11-03/05; the fixtures are those responses.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

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
#: Google Hotels prices at most this many nights in one search; longer stays are split.
GOOGLE_MAX_NIGHTS = 8
#: "Advance Purchase", "Adv Purchase", "Adv. Purchase" (any casing) are always prepaid.
_ADVANCE_PURCHASE_RE = re.compile(r"\badv(ance|\.)?\s+purchase\b", re.IGNORECASE)
#: Booking's own API reports its price currency with the symbol, not the ISO code.
_EUR_CURRENCIES = {"EUR", "€"}


class HotelClient(Protocol):
    source: HotelSource

    def check(self, stay: HotelStay) -> HotelResult: ...


def _no_rates(stay: HotelStay, note: str) -> HotelResult:
    return HotelResult(stay, "no_rates", [], note)


def _guests(adults, ages) -> tuple[int, tuple[int, ...]] | None:
    """(adults, sorted child ages) from echoed values, or None if they don't parse."""
    try:
        return int(adults), tuple(sorted(int(a) for a in ages))
    except (TypeError, ValueError):
        return None


def _stay_guests(stay: HotelStay) -> tuple[int, tuple[int, ...]]:
    return stay.adults, tuple(sorted(stay.child_ages))


def _guest_text(g: tuple[int, tuple[int, ...]] | None) -> str:
    if g is None:
        return "not echoed"
    return f"{g[0]}A" + (f", ages {', '.join(map(str, g[1]))}" if g[1] else "")


def _is_advance_purchase(name: str | None) -> bool:
    return bool(name) and _ADVANCE_PURCHASE_RE.search(name) is not None


def _refundable(raw: bool | None, name: str | None) -> bool | None:
    """A cash rate's refundability, with the Advance Purchase override applied.

    A source may call its Advance Purchase plan "refundable" (Hilton does); it is prepaid
    and never is, whatever the source says. Anything else passes through unchanged, so an
    unknown (`None`) refundability stays unknown rather than becoming a `False` that would
    never alert for the wrong reason.
    """
    return False if _is_advance_purchase(name) else raw


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


def _booking_total(o: dict) -> float | None:
    """The payable total of one Booking.com option.

    On a discounted option (early-booker "10% sparen") `price` is the struck-through
    pre-discount price; what the guest pays is `displayedPrice` + `excludedTaxesPrice`
    (equal to `discount.discountedPrice`). Undiscounted options have the same sum in `price`.
    """
    shown = o.get("displayedPrice")
    if shown:
        return round(float(shown) + float(o.get("excludedTaxesPrice") or 0), 2)
    return round(float(o["price"]), 2) if o.get("price") else None


def parse_booking(items: list[dict], stay: HotelStay) -> HotelResult:
    if not items:
        return _no_rates(stay, "Booking.com returned no hotel")
    h = items[0]
    if (h.get("checkInDate"), h.get("checkOutDate")) != (stay.check_in.isoformat(), stay.check_out.isoformat()):
        return _no_rates(stay, f"Booking.com priced other dates ({h.get('checkInDate')}..{h.get('checkOutDate')})")
    q = parse_qs(urlsplit(h.get("url") or "").query)
    got = None
    if "group_adults" in q and "group_children" in q:
        ages = q.get("age", [])
        got = _guests(q["group_adults"][0], ages)
        try:
            if got is not None and int(q["group_children"][0]) != len(ages):
                got = None   # a child count without matching ages can't be verified
        except ValueError:
            got = None
    if got != _stay_guests(stay):   # also when the URL doesn't echo the guests: unverifiable
        return _no_rates(stay, f"Booking.com priced other guests ({_guest_text(got)})")
    rates = []
    for room in h.get("rooms") or []:
        for o in room.get("options") or []:
            total = _booking_total(o)
            if not total or o.get("currency") not in _EUR_CURRENCIES:
                continue
            name = ", ".join(o.get("yourChoices") or []) or None
            rates.append(HotelRate(
                "booking", "cash", total=total, room=room.get("roomType"),
                rate_name=name, refundable=_refundable(bool(o.get("freeCancellation")), name), url=h.get("url")))
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
            if (r.get("channel") == "direct" or not r.get("public_total") or r.get("currency") not in _EUR_CURRENCIES
                    or r.get("basis") != "after_tax"):
                # the `rates` check prices direct room by room; other-currency quotes and
                # "native"-basis totals (which may exclude taxes) aren't comparable
                continue
            name = r.get("rate_name")
            rates.append(HotelRate(r["channel"], "cash", total=float(r["public_total"]),
                                   rate_name=name, refundable=_refundable(r.get("refundable"), name)))
        else:   # rates
            if not r.get("price_total") or r.get("currency") not in _EUR_CURRENCIES:
                continue   # award plans appear here with price 0; other-currency quotes aren't comparable
            name = r.get("rate_name")
            rates.append(HotelRate("direct", "cash", total=float(r["price_total"]), room=r.get("room_name"),
                                   rate_name=name, refundable=_refundable(bool(r.get("refundable")), name)))
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


def _google_guests(params: dict) -> tuple[int, tuple[int, ...]] | None:
    """The guests SerpApi echoes in `search_parameters`; children absent means none."""
    ages_raw = params.get("children_ages")
    ages = [a for a in str(ages_raw).split(",") if a.strip()] if ages_raw not in (None, "") else []
    got = _guests(params.get("adults"), ages)
    try:
        if got is not None and int(params.get("children") or 0) != len(ages):
            return None
    except (TypeError, ValueError):
        return None
    return got


def parse_google(data: dict, stay: HotelStay) -> HotelResult:
    params = data.get("search_parameters") or {}
    if params.get("currency") != "EUR":
        return _no_rates(stay, "Google Hotels: not EUR")
    got = _google_guests(params)
    if got != _stay_guests(stay):
        return _no_rates(stay, f"Google priced other guests ({_guest_text(got)})")
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
            room_total = _lowest(room.get("total_rate"))
            entries = room.get("rates") or []
            if not entries:
                if room_total:
                    rates.append(HotelRate(src.get("source") or "google", "cash", total=room_total,
                                           room=room.get("name"), refundable=None, url=room.get("link")))
                continue
            for entry in entries:
                t = _lowest(entry.get("total_rate")) or room_total
                if not t:
                    continue
                name = ", ".join(entry.get("inclusions") or []) or None
                raw = entry.get("free_cancellation") if "free_cancellation" in entry else None
                rates.append(HotelRate(src.get("source") or "google", "cash", total=t, room=room.get("name"),
                                       rate_name=name, refundable=_refundable(raw, name),
                                       url=entry.get("link") or room.get("link")))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Google Hotels: no price")


def google_parts(stay: HotelStay) -> list[tuple[date, date]]:
    """The stay cut into as few, as even parts of at most GOOGLE_MAX_NIGHTS nights as possible."""
    n = math.ceil(stay.nights / GOOGLE_MAX_NIGHTS)
    base, extra = divmod(stay.nights, n)
    parts, start = [], stay.check_in
    for i in range(n):
        end = start + timedelta(days=base + (1 if i < extra else 0))
        parts.append((start, end))
        start = end
    return parts


def google_credits(stay: HotelStay) -> int:
    """SerpApi searches a Google check may spend: one per part, and one retry each."""
    return 2 * len(google_parts(stay))


def _add_parts(stay: HotelStay, results: list[HotelResult]) -> HotelResult:
    """One total per (room, refundability) offered in every part: the sum of each part's cheapest site.

    Sites come and go between parts (Hotels.com in one, Expedia.de in the other), so a total may mix
    sites; the channel then names each part's site, e.g. "Hotels.com + Expedia.de".
    """
    per_part: list[dict[tuple, HotelRate]] = []
    for res in results:
        cheapest: dict[tuple, HotelRate] = {}
        for r in res.rates:
            k = (r.room, r.refundable)
            if k not in cheapest or r.total < cheapest[k].total:
                cheapest[k] = r
        per_part.append(cheapest)
    rates = []
    for k, first in per_part[0].items():
        if not all(k in p for p in per_part[1:]):
            continue
        picks = [p[k] for p in per_part]
        channels = list(dict.fromkeys(r.channel for r in picks))
        rates.append(replace(first, channel=" + ".join(channels), total=round(sum(r.total for r in picks), 2)))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Google Hotels: no room offered in every part")


class GoogleHotelsClient:
    source = HotelSource.GOOGLE

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=60)

    def check(self, stay: HotelStay) -> HotelResult:
        parts = google_parts(stay)
        if len(parts) == 1:
            return self._check_once(stay)
        results = []
        for check_in, check_out in parts:
            res = self._check_once(replace(stay, check_in=check_in, check_out=check_out))
            if res.status != "ok":
                return _no_rates(stay, f"{res.note} ({check_in:%d.%m.}–{check_out:%d.%m.})")
            results.append(res)
        return _add_parts(stay, results)

    def _check_once(self, stay: HotelStay) -> HotelResult:
        res = self._fetch(stay, no_cache=False)
        if res.status == "no_rates" and res.note == "Google Hotels: no price":
            # Google fills in prices a moment after the first answer; SerpApi may have cached that empty answer
            res = self._fetch(stay, no_cache=True)
        return res

    def _fetch(self, stay: HotelStay, no_cache: bool) -> HotelResult:
        params = {"engine": "google_hotels", "q": stay.watch_id, "property_token": stay.ref,
                  "check_in_date": stay.check_in.isoformat(), "check_out_date": stay.check_out.isoformat(),
                  "adults": str(stay.adults), "currency": "EUR", "gl": "de", "hl": "de", "api_key": self.api_key}
        if stay.child_ages:
            params["children"] = str(len(stay.child_ages))
            params["children_ages"] = ",".join(str(a) for a in stay.child_ages)
        if no_cache:
            params["no_cache"] = "true"
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
