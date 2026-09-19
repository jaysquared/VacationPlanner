from __future__ import annotations

from pathlib import Path

from ..models import Offer, Provider, SearchRequest, SearchResult
from .base import ProviderError, QuotaExhausted


class FakeFlightClient:
    provider = Provider.FAKE

    def __init__(self, prices: dict[tuple[str, str], list[float]] | None = None,
                 fail: set[tuple[str, str]] = frozenset(), quota_after: int | None = None):
        self.prices = prices or {}
        self.fail = set(fail)
        self.quota_after = quota_after
        self.calls: list[SearchRequest] = []

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        self.calls.append(req)
        key = (req.origin, req.destination)
        if key in self.fail:
            raise ProviderError(f"fake failure for {key}")
        if self.quota_after is not None and len(self.calls) > self.quota_after + 1:
            raise QuotaExhausted("fake quota exhausted")
        prices = self.prices[key] if key in self.prices else [1000 + 100 * req.nights]
        offers = [
            Offer(provider=self.provider, price_total=float(p), currency="EUR", per_person=float(p) / req.pax,
                  airlines=["LH"], stops=1, duration_minutes=720,
                  departs_at=f"{req.outbound_date}T10:00", arrives_at=f"{req.outbound_date}T22:00",
                  price_level=None, typical_low=None, typical_high=None,
                  google_url=f"https://www.google.com/travel/flights?fake={req.origin}-{req.destination}", raw={})
            for p in prices
        ]
        return SearchResult(req, self.provider, offers)
