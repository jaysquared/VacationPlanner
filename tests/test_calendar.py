from datetime import date

from vacation_planner.calendar import Window, child_age_on, free_window, pax_for
from vacation_planner.models import Child, Slot, Travellers


def slot(start, end):
    return Slot("x", "x", start, end, ())


def test_monday_to_friday_extends_to_enclosing_weekend():
    w = free_window(slot(date(2026, 10, 19), date(2026, 10, 30)))  # Mon..Fri
    assert w == Window(date(2026, 10, 17), date(2026, 11, 1))       # Sat..Sun
    assert w.days == 16


def test_midweek_boundaries_are_not_extended():
    # Sommerferien 2027: Thu 1 Jul .. Wed 11 Aug
    w = free_window(slot(date(2027, 7, 1), date(2027, 8, 11)))
    assert w == Window(date(2027, 7, 1), date(2027, 8, 11))


def test_friday_start_is_kept_but_friday_end_extends():
    # Pfingsten 2027: Fri 7 May .. Fri 14 May
    w = free_window(slot(date(2027, 5, 7), date(2027, 5, 14)))
    assert w == Window(date(2027, 5, 7), date(2027, 5, 16))


def test_bridge_days_widen_window():
    w = free_window(slot(date(2026, 10, 19), date(2026, 10, 30)), before=2, after=1)
    assert w == Window(date(2026, 10, 15), date(2026, 11, 2))


def test_child_age_on_birthday_edges():
    b = date(2019, 4, 21)
    assert child_age_on(b, date(2027, 4, 20)) == 7
    assert child_age_on(b, date(2027, 4, 21)) == 8


def test_pax_for_buckets_children():
    t = Travellers(2, (Child(date(2019, 4, 21)), Child(date(2025, 1, 1)), Child(date(2013, 1, 1))))
    assert pax_for(t, date(2026, 10, 17)) == (3, 1, 1)   # 13yo counts as adult, 1yo infant
