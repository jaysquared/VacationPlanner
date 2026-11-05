"""Turn one batch of hotel rates into a verdict. Pure functions: no storage, no network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import BookedRule, HotelRate, HotelWatch, PointsRule
from ..report import MONTHS, money


@dataclass
class Verdict:
    watch_id: str
    state: str                    # alert | fine | no_rates | ended
    headline: str
    reason: str | None = None     # cheaper_room | cash_beats_points | no_standard_reward
    value: float | None = None    # the cash price compared
    threshold: float | None = None
    best: HotelRate | None = None


def _refundable_cash(rates: list[HotelRate]) -> list[HotelRate]:
    return [r for r in rates if r.kind == "cash" and r.total and r.refundable is True]


def _plan(r: HotelRate) -> str:
    """`channel · rate name`: Hilton marks every plan refundable, so the family sees which one it is."""
    return f"{r.channel} · {r.rate_name}" if r.rate_name else r.channel


def evaluate_booked(watch: HotelWatch, rates: list[HotelRate]) -> Verdict:
    rule = watch.rule
    assert isinstance(rule, BookedRule)
    room = watch.room[0] if watch.room else "your room"
    same = [r for r in rates if r.kind == "cash" and r.total and watch.matches_room(r.room)]
    refundable = [r for r in same if r.refundable is True]
    threshold = round(rule.paid_total * (1 - rule.min_saving_ratio), 2)
    if not refundable:
        return Verdict(watch.id, "no_rates", f"{room} not offered this week", threshold=threshold)
    best = min(refundable, key=lambda r: r.total)
    if best.total <= threshold:
        return Verdict(watch.id, "alert",
                       f"Rebook: {room} {money(best.total)} on {best.channel} "
                       f"(you pay {money(rule.paid_total)}, alert at ≤ {money(threshold)})",
                       "cheaper_room", best.total, threshold, best)
    headline = f"Keep your booking: cheapest {room} {money(best.total)} on {best.channel} (you pay {money(rule.paid_total)})"
    cheaper_fixed = [r for r in same if r.refundable is not True and r.total < best.total]
    if cheaper_fixed:
        headline += f"; non-refundable {money(min(r.total for r in cheaper_fixed))}"
    return Verdict(watch.id, "fine", headline, None, best.total, threshold, best)


def evaluate_points(watch: HotelWatch, rates: list[HotelRate], point_value: float) -> Verdict:
    rule = watch.rule
    assert isinstance(rule, PointsRule)
    standard = [r for r in rates if r.kind == "points" and r.award_type == "standard" and r.points]
    if not standard:
        if not any(r.kind == "points" for r in rates):
            return Verdict(watch.id, "no_rates", "No award availability this week")
        return Verdict(watch.id, "alert",
                       f"Only Premium rewards this week — no Standard Room Reward for {rule.points_adults} adults",
                       "no_standard_reward")
    pts = min(r.points for r in standard)
    cost = round(pts * point_value + rule.surcharge, 2)
    detail = f"{pts:,} pts" + (f" + {money(rule.surcharge)}" if rule.surcharge else "")
    cash = _refundable_cash(rates)
    if not cash:
        return Verdict(watch.id, "no_rates", f"No refundable cash rate this week; points cost {money(cost)} ({detail})",
                       threshold=cost)
    best = min(cash, key=lambda r: r.total)
    if best.total < cost:
        return Verdict(watch.id, "alert", f"Pay cash: {money(best.total)} ({_plan(best)}) vs points {money(cost)} ({detail})",
                       "cash_beats_points", best.total, cost, best)
    return Verdict(watch.id, "fine", f"Points still better: cash {money(best.total)} ({_plan(best)}) "
                   f"vs points {money(cost)} ({detail})",
                   None, best.total, cost, best)


def evaluate(watch: HotelWatch, rates: list[HotelRate], point_value: float | None, today: date) -> Verdict:
    if today > watch.until:
        u = watch.until
        return Verdict(watch.id, "ended", f"Watch ended on {u.day} {MONTHS[u.month - 1]} {u.year}")
    if isinstance(watch.rule, BookedRule):
        return evaluate_booked(watch, rates)
    assert point_value is not None, "config validation guarantees a point value for points watches"
    return evaluate_points(watch, rates, point_value)
