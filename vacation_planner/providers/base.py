from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from ..models import Offer, Provider, SearchRequest, SearchResult


class ProviderError(Exception):
    """The provider failed for this search; the executor may fall back."""


class QuotaExhausted(ProviderError):
    """The provider's monthly quota is used up."""


class FlightClient(Protocol):
    provider: Provider

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult: ...


def filter_excluded(offers: list[Offer], excluded: Iterable[str]) -> list[Offer]:
    ex = {e.upper() for e in excluded}
    return [o for o in offers if not ({a.upper() for a in o.airlines} & ex)]


def per_person(price: float, req: SearchRequest, price_is_total: bool) -> tuple[float, float]:
    if price_is_total:
        return float(price), float(price) / req.pax
    return float(price) * req.pax, float(price)
