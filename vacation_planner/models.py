from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Cabin(str, Enum):
    ANY = "any"
    BUSINESS = "business"


class SeatClass(str, Enum):
    ECONOMY = "economy"
    BUSINESS = "business"


class Provider(str, Enum):
    SERPAPI = "serpapi"
    SEARCHAPI = "searchapi"
    FAST_FLIGHTS = "fast_flights"
    FAKE = "fake"


class DealReason(str, Enum):
    BELOW_MEDIAN = "below_median"
    GOOGLE_LOW = "google_low"
    UNDER_MAX = "under_max"
    NEW_LOW = "new_low"
    CHEAPER_THAN_HOME = "cheaper_than_home"


def seat_for(cabin: Cabin) -> SeatClass:
    return SeatClass.BUSINESS if cabin is Cabin.BUSINESS else SeatClass.ECONOMY


@dataclass(frozen=True)
class Nights:
    min: int
    max: int


@dataclass(frozen=True)
class Slot:
    id: str
    name: str
    start: date
    end: date
    targets: tuple[str, ...]
    nights: Nights | None = None


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    cabin: Cabin
    max_price_per_person: float | None = None
    #: Origins to search for this destination; None falls back to `settings.origins`.
    origins: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Child:
    birthdate: date


@dataclass(frozen=True)
class Travellers:
    adults: int
    children: tuple[Child, ...]


@dataclass(frozen=True)
class Leg:
    """One flight inside an itinerary. Times are local, "YYYY-MM-DD HH:MM"."""

    origin: str
    destination: str
    departs_at: str
    arrives_at: str
    #: the provider's cabin for this flight ("Business Class", "Economy"); None when unknown
    cabin: str | None = None
    #: flight time; the local clock times cannot give it across time zones
    minutes: int | None = None


@dataclass(frozen=True)
class SearchRequest:
    slot_id: str
    origin: str
    destination: str
    outbound_date: date
    return_date: date
    seat: SeatClass
    adults: int
    children: int

    @property
    def nights(self) -> int:
        return (self.return_date - self.outbound_date).days

    @property
    def pax(self) -> int:
        return self.adults + self.children


@dataclass
class Offer:
    provider: Provider
    price_total: float
    currency: str
    per_person: float
    airlines: list[str]
    stops: int
    duration_minutes: int
    departs_at: str
    arrives_at: str
    price_level: str | None
    typical_low: float | None
    typical_high: float | None
    google_url: str
    raw: dict = field(default_factory=dict)
    legs: list[Leg] = field(default_factory=list)


@dataclass
class SearchResult:
    request: SearchRequest
    provider: Provider
    offers: list[Offer]
    raw_path: str | None = None


@dataclass(frozen=True)
class PlannedSearch:
    request: SearchRequest
    provider: Provider


class HotelSource(str, Enum):
    BOOKING = "booking"
    HILTON = "hilton"
    GOOGLE = "google"


@dataclass(frozen=True)
class BookedRule:
    """A stay already booked: alert when the same room is clearly cheaper to rebook."""

    paid_total: float
    min_saving_ratio: float
    until: date


@dataclass(frozen=True)
class PointsRule:
    """A stay planned on Hilton points: alert when cash for the family beats the points cost."""

    points_adults: int
    surcharge: float
    until: date


@dataclass
class HotelWatch:
    id: str
    name: str
    slot_id: str
    check_in: date
    check_out: date
    #: room-name substrings, any of which identifies the watched room; empty matches every room
    room: tuple[str, ...]
    sources: dict[HotelSource, str]
    rule: BookedRule | PointsRule

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def until(self) -> date:
        return self.rule.until

    def matches_room(self, name: str | None) -> bool:
        if not self.room:
            return True
        return name is not None and any(r.lower() in name.lower() for r in self.room)


@dataclass(frozen=True)
class HotelStay:
    """One query: a watch, a source, and the guests it is priced for."""

    watch_id: str
    source: HotelSource
    ref: str                  # Booking URL, Hilton hotel code or Google property token
    mode: str                 # rates | award | parity | rooms
    check_in: date
    check_out: date
    adults: int
    child_ages: tuple[int, ...]

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass
class HotelRate:
    channel: str              # direct, booking, agoda, expedia, ... or Google's site name
    kind: str                 # cash | points
    total: float | None = None
    points: int | None = None
    room: str | None = None
    rate_name: str | None = None
    #: None when the source does not say; such a rate never alerts
    refundable: bool | None = None
    award_type: str | None = None   # standard | premium, points rows only
    url: str | None = None


@dataclass
class HotelResult:
    stay: HotelStay
    status: str               # ok | no_rates
    rates: list[HotelRate]
    note: str | None = None
