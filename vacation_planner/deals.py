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


def median_for(history: list[float], history_runs: int, route_history: list[float],
               route_history_runs: int, settings: DealSettings) -> float | None:
    """The reference price for BELOW_MEDIAN: this route's history, else the route across slots.

    `*_runs` is how many earlier runs each list came from, and that is what
    `min_history_points` weighs: a single weekly scan leaves one price per date pair,
    so counting prices would call one week of data three weeks of history.
    """
    if history_runs >= settings.min_history_points and history:
        return float(_median(history))
    if route_history_runs >= settings.min_history_points and route_history:
        return float(_median(route_history))
    return None


def evaluate(price_total: float, per_person: float, price_level: str | None,
             history: list[float], history_runs: int,
             route_history: list[float], route_history_runs: int,
             max_pp: float | None, settings: DealSettings) -> tuple[list[DealReason], float | None]:
    reasons: list[DealReason] = []
    median = median_for(history, history_runs, route_history, route_history_runs, settings)
    if median is not None and price_total <= settings.median_ratio * median:
        reasons.append(DealReason.BELOW_MEDIAN)
    if price_level == "low":
        reasons.append(DealReason.GOOGLE_LOW)
    if max_pp is not None and per_person <= max_pp:
        reasons.append(DealReason.UNDER_MAX)
    # A record needs a field to beat: after one or two weekly scans "lowest ever"
    # says nothing, so NEW_LOW waits for the same history the median rule wants.
    if history_runs >= settings.min_history_points and history and price_total < min(history):
        reasons.append(DealReason.NEW_LOW)
    return reasons, median


def detect_for_run(storage: Storage, run_id: int, config: Config, now: datetime) -> list[DetectedDeal]:
    s = config.settings.deals
    alt = config.settings.alternate_origins
    out: list[DetectedDeal] = []
    for search in storage.searches_in_run(run_id, status="ok"):
        offer = storage.cheapest_offer(search.id)
        if offer is None:
            continue
        beats_home = False
        if search.origin != alt.home:
            # A fare from an alternate origin only counts once it beats the home
            # airport by both margins; otherwise it is no deal, whatever else says so.
            home_price = storage.best_price_for(search.slot_id, alt.home, search.destination, search.seat)
            if home_price is not None:
                if not alt.beats_home(offer.price_total, home_price):
                    continue
                beats_home = True
        # Earlier runs only: the other date pairs of this same scan are today's prices, not history.
        history = storage.prior_cheapest_prices(search.slot_id, search.origin, search.destination, search.seat, run_id)
        route_history = storage.prior_route_prices(search.origin, search.destination, search.seat, run_id)
        history_runs = storage.prior_run_count(search.slot_id, search.origin, search.destination, search.seat, run_id)
        route_runs = storage.prior_route_run_count(search.origin, search.destination, search.seat, run_id)
        max_pp = config.destination(search.destination).max_price_per_person if search.destination in config.destinations else None
        reasons, median = evaluate(offer.price_total, offer.per_person, offer.price_level,
                                   history, history_runs, route_history, route_runs, max_pp, s)
        if not reasons:
            continue
        if beats_home:
            reasons.append(DealReason.CHEAPER_THAN_HOME)
        score = len(reasons) + ((1 - offer.price_total / median) if median else 0.0)
        last = storage.last_notified_price(search.slot_id, search.origin, search.destination, search.seat)
        notifiable = last is None or offer.price_total <= s.renotify_drop_ratio * last
        deal_id = storage.insert_deal(offer.id, search.id, search.slot_id, reasons, score, now, notifiable)
        out.append(DetectedDeal(deal_id, search, offer, reasons, score, median, notifiable))
    out.sort(key=lambda d: -d.score)
    return out
