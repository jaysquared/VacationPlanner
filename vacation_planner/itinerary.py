"""Layover arithmetic: what happens between the legs of one itinerary.

Times come from the providers as naive local strings ("YYYY-MM-DD HH:MM") at the
airport they refer to, so a stop is measured in the stopover airport's own local
time -- which is exactly what the rule is about ("no waiting around at night").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Sequence

from .config import LayoverSettings
from .models import Leg

TIME_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class Layover:
    airport: str
    starts_at: datetime
    ends_at: datetime
    minutes: int


def parse_when(text: str) -> datetime:
    return datetime.strptime(text.strip(), TIME_FORMAT)


def layovers(legs: Sequence[Leg]) -> list[Layover]:
    """The waits between consecutive legs, in the order they happen."""
    out: list[Layover] = []
    for arriving, departing in zip(legs, legs[1:]):
        starts_at = parse_when(arriving.arrives_at)
        ends_at = parse_when(departing.departs_at)
        out.append(Layover(arriving.destination, starts_at, ends_at,
                           int((ends_at - starts_at).total_seconds() // 60)))
    return out


def _clock(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def _touches_window(starts_at: datetime, ends_at: datetime, window: tuple[str, str]) -> bool:
    """Does [starts_at, ends_at) overlap the daily window on any day it spans?

    The window wraps midnight when its start is later than its end (23:00-05:00),
    so every candidate day contributes one interval [day+start, day(+1)+end).
    """
    if ends_at <= starts_at:
        return False
    start, end = _clock(window[0]), _clock(window[1])
    day = starts_at.date() - timedelta(days=1)   # yesterday's window can reach into today
    while day <= ends_at.date():
        opens = datetime.combine(day, start)
        closes = datetime.combine(day, end)
        if end <= start:
            closes += timedelta(days=1)
        if max(starts_at, opens) < min(ends_at, closes):
            return True
        day += timedelta(days=1)
    return False


def passes_layover_rule(legs: Sequence[Leg], settings: LayoverSettings) -> bool:
    """True when every stop is short enough and none of them touches the night window.

    A nonstop itinerary (or one whose legs
    the provider did not report) has no layovers and always passes.
    """
    for layover in layovers(legs):
        if layover.minutes > settings.max_minutes:
            return False
        if settings.forbidden_window and _touches_window(
                layover.starts_at, layover.ends_at, settings.forbidden_window):
            return False
    return True
