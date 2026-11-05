"""What the report and the digest show per hotel watch: verdict, one row per channel, problems."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import Config
from ..models import BookedRule, HotelRate, HotelWatch, PointsRule
from ..report import change, date_range, money
from ..storage import HotelCheckRow, Storage
from .rules import Verdict, evaluate

#: Channel ids the Hilton actor uses -> how the family knows them.
CHANNELS = {"direct": "Hilton direct", "booking": "Booking.com", "expedia": "Expedia",
            "hotelscom": "Hotels.com", "agoda": "Agoda", "ctrip": "Trip.com"}
SOURCES = {"booking": "Booking.com", "hilton": "Hilton", "google": "Google Hotels"}


@dataclass
class HotelRow:
    label: str
    detail: str
    price: str
    change: str
    change_class: str
    url: str | None


@dataclass
class HotelCard:
    watch_id: str
    name: str
    dates: str
    verdict: Verdict
    rows: list[HotelRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _refund_text(r: HotelRate) -> str:
    return {True: "free cancellation", False: "non-refundable"}.get(r.refundable, "cancellation unknown")


def _relevant_cash(watch: HotelWatch, rates: list[HotelRate]) -> dict[str, HotelRate]:
    """Cheapest relevant cash rate per channel."""
    best: dict[str, HotelRate] = {}
    for r in rates:
        if r.kind != "cash" or not r.total:
            continue
        if isinstance(watch.rule, BookedRule) and not watch.matches_room(r.room):
            continue
        if r.channel not in best or r.total < best[r.channel].total:
            best[r.channel] = r
    return best


def _points_cost(watch: HotelWatch, rates: list[HotelRate], point_value: float | None) -> tuple[float, HotelRate] | None:
    """(points cost in EUR, the cheapest Standard award) for a points watch."""
    if not isinstance(watch.rule, PointsRule):
        return None
    std = [r for r in rates if r.kind == "points" and r.award_type == "standard" and r.points]
    if not std or point_value is None:
        return None
    r = min(std, key=lambda x: x.points)
    return r.points * point_value + watch.rule.surcharge, r


def _problem(c: HotelCheckRow) -> str:
    src = SOURCES.get(c.source.value, c.source.value)
    label = src if c.mode in ("rates", "rooms") else f"{src} {c.mode}"
    text = f"skipped ({c.error})" if c.status == "skipped" else (c.error or c.status)
    if text.lower().startswith(label.lower()):
        return text
    return f"{label}: {text}"


def _card(storage: Storage, config: Config, watch: HotelWatch, today: date) -> HotelCard | None:
    batches = storage.hotel_batches(watch.id)
    if not batches:
        return None
    rates = [s.rate for s in storage.hotel_rates(watch.id, batches[0])]
    before = [s.rate for s in storage.hotel_rates(watch.id, batches[1])] if len(batches) > 1 else []
    verdict = evaluate(watch, rates, config.point_value_eur, today)
    rows: list[HotelRow] = []
    previous = _relevant_cash(watch, before)
    for channel, r in sorted(_relevant_cash(watch, rates).items(), key=lambda kv: kv[1].total):
        prev = previous.get(channel)
        label, cls = change(prev.total if prev else None, r.total)
        room = r.room or r.rate_name
        refund = _refund_text(r)
        has_refund_text = room and any(t in room.lower() for t in ("non-refundable", "free cancellation"))
        detail = " · ".join(x for x in (room, None if has_refund_text else refund) if x)
        rows.append(HotelRow(CHANNELS.get(channel, channel), detail, money(r.total), label, cls, r.url))
    points = _points_cost(watch, rates, config.point_value_eur)
    if points:
        cost, r = points
        prev_points = _points_cost(watch, before, config.point_value_eur)
        label, cls = change(prev_points[0] if prev_points else None, cost)
        detail = " · ".join(x for x in (r.room, f"{r.points:,} pts") if x)
        rows.append(HotelRow("Hilton points", detail, money(cost), label, cls, None))
    problems = [_problem(c) for c in storage.hotel_checks(watch.id, batches[0]) if c.status != "ok"]
    return HotelCard(watch.id, watch.name, f"{date_range(watch.check_in, watch.check_out)} {watch.check_out.year}",
                     verdict, rows, problems)


def build_hotel_cards(storage: Storage, config: Config, today: date) -> list[HotelCard]:
    return [c for w in config.hotels if (c := _card(storage, config, w, today)) is not None]
