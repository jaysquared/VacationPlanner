from datetime import date

from vacation_planner.models import (
    Cabin, SearchRequest, SeatClass, seat_for,
)


def make_request(**over):
    base = dict(
        slot_id="herbst-2026", origin="HAM", destination="BKK",
        outbound_date=date(2026, 10, 17), return_date=date(2026, 10, 31),
        seat=SeatClass.BUSINESS, adults=2, children=1,
    )
    base.update(over)
    return SearchRequest(**base)


def test_nights_and_pax():
    r = make_request()
    assert r.nights == 14
    assert r.pax == 3


def test_request_is_hashable_and_equal_by_value():
    assert make_request() == make_request()
    assert len({make_request(), make_request()}) == 1


def test_seat_for_cabin():
    assert seat_for(Cabin.BUSINESS) is SeatClass.BUSINESS
    assert seat_for(Cabin.ANY) is SeatClass.ECONOMY
