from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Slot, Travellers

SATURDAY, SUNDAY, MONDAY, FRIDAY = 5, 6, 0, 4


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def free_window(slot: Slot, before: int = 0, after: int = 0) -> Window:
    start, end = slot.start, slot.end
    if start.weekday() == MONDAY:
        start -= timedelta(days=2)
    if end.weekday() == FRIDAY:
        end += timedelta(days=2)
    return Window(start - timedelta(days=before), end + timedelta(days=after))


def child_age_on(birthdate: date, on: date) -> int:
    age = on.year - birthdate.year
    if (on.month, on.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age


def pax_for(travellers: Travellers, on: date) -> tuple[int, int, int]:
    adults, children, infants = travellers.adults, 0, 0
    for c in travellers.children:
        age = child_age_on(c.birthdate, on)
        if age < 2:
            infants += 1
        elif age < 12:
            children += 1
        else:
            adults += 1
    return adults, children, infants
