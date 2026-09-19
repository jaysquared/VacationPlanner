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
