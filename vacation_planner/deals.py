from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median as _median

from .config import Config, DealSettings
from .models import DealReason
from .storage import OfferRow, SearchRow, Storage


@dataclass
class DetectedDeal:
    deal_id: int
    search: SearchRow
    offer: OfferRow
    reasons: list[DealReason]
    score: float
    median: float | None
    notifiable: bool


def evaluate(price_total: float, per_person: float, price_level: str | None, history: list[float],
             route_history: list[float], max_pp: float | None, settings: DealSettings) -> tuple[list[DealReason], float | None]:
    reasons: list[DealReason] = []
    median: float | None = None
    if len(history) >= settings.min_history_points:
        median = float(_median(history))
    elif len(route_history) >= settings.min_history_points:
        median = float(_median(route_history))
    if median is not None and price_total <= settings.median_ratio * median:
        reasons.append(DealReason.BELOW_MEDIAN)
    if price_level == "low":
        reasons.append(DealReason.GOOGLE_LOW)
    if max_pp is not None and per_person <= max_pp:
        reasons.append(DealReason.UNDER_MAX)
    if history and price_total < min(history):
        reasons.append(DealReason.NEW_LOW)
    return reasons, median


def detect_for_run(storage: Storage, run_id: int, config: Config, now: datetime) -> list[DetectedDeal]:
    s = config.settings.deals
    out: list[DetectedDeal] = []
    for search in storage.searches_in_run(run_id, status="ok"):
        offer = storage.cheapest_offer(search.id)
        if offer is None:
            continue
        history = storage.prior_cheapest_prices(search.slot_id, search.origin, search.destination, search.seat, search.id)
        route_history = storage.prior_route_prices(search.origin, search.destination, search.seat, search.id)
        max_pp = config.destination(search.destination).max_price_per_person if search.destination in config.destinations else None
        reasons, median = evaluate(offer.price_total, offer.per_person, offer.price_level, history, route_history, max_pp, s)
        if not reasons:
            continue
        score = len(reasons) + ((1 - offer.price_total / median) if median else 0.0)
        last = storage.last_notified_price(search.slot_id, search.origin, search.destination, search.seat)
        notifiable = last is None or offer.price_total <= s.renotify_drop_ratio * last
        deal_id = storage.insert_deal(offer.id, search.id, search.slot_id, reasons, score, now, notifiable)
        out.append(DetectedDeal(deal_id, search, offer, reasons, score, median, notifiable))
    out.sort(key=lambda d: -d.score)
    return out
