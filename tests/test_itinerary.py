from datetime import datetime

import pytest

from vacation_planner.config import LayoverSettings
from vacation_planner.itinerary import Layover, layovers, passes_layover_rule
from vacation_planner.models import Leg

RULE = LayoverSettings(max_minutes=180, forbidden_window=("23:00", "05:00"))
NO_WINDOW = LayoverSettings(max_minutes=180, forbidden_window=None)
LONG_STOPS_OK = LayoverSettings(max_minutes=10_000, forbidden_window=("23:00", "05:00"))

ONE_LEG = [Leg("HAM", "BKK", "2026-12-19 10:00", "2026-12-20 06:00")]


def stop(arrives_at: str, departs_at: str) -> list[Leg]:
    """Two legs with a stop in FRA from `arrives_at` to `departs_at`."""
    return [Leg("HAM", "FRA", "2026-12-19 08:00", arrives_at),
            Leg("FRA", "BKK", departs_at, "2026-12-21 12:00")]


def test_layovers_are_computed_from_consecutive_legs():
    out = layovers(stop("2026-12-19 12:00", "2026-12-19 14:05"))
    assert out == [Layover("FRA", datetime(2026, 12, 19, 12, 0), datetime(2026, 12, 19, 14, 5), 125)]


def test_a_single_leg_has_no_layovers():
    assert layovers(ONE_LEG) == [] and layovers([]) == []


def test_nonstop_passes():
    assert passes_layover_rule(ONE_LEG, RULE) is True
    assert passes_layover_rule([], RULE) is True


@pytest.mark.parametrize("arr, dep, ok", [
    ("2026-12-19 12:00", "2026-12-19 14:00", True),    # 2 h in the afternoon
    ("2026-12-19 12:00", "2026-12-19 15:00", True),    # exactly 3 h
    ("2026-12-19 12:00", "2026-12-19 15:01", False),   # 3 h 1 min
    ("2026-12-19 22:30", "2026-12-20 01:00", False),   # runs into the night
    ("2026-12-19 04:30", "2026-12-19 06:00", False),   # touches the 05:00 edge from below
    ("2026-12-19 05:00", "2026-12-19 07:00", True),    # starts exactly when the window ends
    ("2026-12-19 21:00", "2026-12-19 23:00", True),    # ends exactly when the window starts
    ("2026-12-19 22:00", "2026-12-19 23:01", False),   # one minute into the window
])
def test_duration_and_night_window(arr, dep, ok):
    assert passes_layover_rule(stop(arr, dep), RULE) is ok


def test_a_stop_spanning_a_whole_day_hits_the_window():
    assert passes_layover_rule(stop("2026-12-19 12:00", "2026-12-20 12:00"), LONG_STOPS_OK) is False


def test_without_a_window_only_the_duration_counts():
    assert passes_layover_rule(stop("2026-12-19 22:30", "2026-12-20 01:00"), NO_WINDOW) is True
    assert passes_layover_rule(stop("2026-12-19 22:30", "2026-12-20 02:00"), NO_WINDOW) is False


def test_legs_out_of_order_never_pass():
    """A departure before the previous arrival is not a bookable itinerary."""
    legs = stop("2026-12-19 14:00", "2026-12-19 12:00")
    assert layovers(legs)[0].minutes == -120
    assert passes_layover_rule(legs, RULE) is False
    assert passes_layover_rule(legs, NO_WINDOW) is False


def test_every_stop_must_pass():
    legs = [Leg("HAM", "FRA", "2026-12-19 08:00", "2026-12-19 09:00"),
            Leg("FRA", "DXB", "2026-12-19 10:00", "2026-12-19 20:00"),    # 60 min, fine
            Leg("DXB", "BKK", "2026-12-20 01:00", "2026-12-20 10:00")]    # 20:00 -> 01:00, night
    assert passes_layover_rule(legs, RULE) is False
    assert passes_layover_rule(legs[:2], RULE) is True
