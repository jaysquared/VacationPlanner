from datetime import date, datetime, timedelta, timezone

from vacation_planner.models import HotelRate, HotelResult, HotelSource, HotelStay, Provider
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
STAY = HotelStay("ikos-pfingsten-2027", HotelSource.BOOKING, "https://www.booking.com/hotel/es/ikos-porto-petro.de.html",
                 "rates", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))
GOOGLE = HotelStay("ikos-pfingsten-2027", HotelSource.GOOGLE, "tok", "rooms", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def rate(total, room="Junior Suite mit eigenem Garten", refundable=True):
    return HotelRate("booking", "cash", total=total, room=room, rate_name="All-Inclusive", refundable=refundable,
                     url="https://www.booking.com/x")


def test_a_result_round_trips_with_its_rates():
    db = Storage(":memory:")
    batch = NOW.isoformat()
    db.save_hotel_result(batch, HotelResult(STAY, "ok", [rate(6897.96), rate(6503.58, "Junior Suite mit Gartenblick")]), NOW)
    rows = db.hotel_rates("ikos-pfingsten-2027", batch)
    assert [r.rate.total for r in rows] == [6897.96, 6503.58]
    assert rows[0].rate == rate(6897.96)
    assert rows[0].check.source is HotelSource.BOOKING and rows[0].check.child_ages == (8,)
    assert db.hotel_checks("ikos-pfingsten-2027", batch)[0].status == "ok"


def test_batches_are_listed_newest_first_per_watch():
    db = Storage(":memory:")
    for days in (0, 7, 14):
        t = NOW + timedelta(days=days)
        db.save_hotel_result(t.isoformat(), HotelResult(STAY, "ok", [rate(6000 - days)]), t)
    assert db.hotel_batches("ikos-pfingsten-2027") == [(NOW + timedelta(days=d)).isoformat() for d in (14, 7, 0)]
    assert db.hotel_batches("other") == []


def test_errors_and_no_rates_are_stored_without_rates():
    db = Storage(":memory:")
    b = NOW.isoformat()
    db.record_hotel_check(b, STAY, "error", NOW, error="apify 500")
    db.save_hotel_result(b, HotelResult(GOOGLE, "no_rates", [], "Google priced other dates"), NOW)
    checks = db.hotel_checks("ikos-pfingsten-2027", b)
    assert [(c.source, c.status, c.error) for c in checks] == [
        (HotelSource.BOOKING, "error", "apify 500"), (HotelSource.GOOGLE, "no_rates", "Google priced other dates")]
    assert db.hotel_rates("ikos-pfingsten-2027", b) == []


def test_google_hotel_checks_count_against_the_serpapi_budget():
    db = Storage(":memory:")
    b = NOW.isoformat()
    db.save_hotel_result(b, HotelResult(GOOGLE, "ok", []), NOW)
    db.record_hotel_check(b, GOOGLE, "error", NOW, error="x")
    db.record_hotel_check(b, GOOGLE, "skipped", NOW, error="no key")   # never sent: free
    db.save_hotel_result(b, HotelResult(STAY, "ok", []), NOW)          # Apify, not SerpApi
    assert db.searches_by_provider_since(Provider.SERPAPI, NOW - timedelta(days=1)) == 2
    assert db.searches_by_provider_since(Provider.SEARCHAPI, NOW - timedelta(days=1)) == 0


def test_alerts_are_pending_until_notified():
    db = Storage(":memory:")
    b = NOW.isoformat()
    a = db.insert_hotel_alert("ikos-pfingsten-2027", b, "cheaper_room", 4980.0, 5076.0, "Rebook: Booking.com 4,980 €", NOW)
    assert [x.id for x in db.pending_hotel_alerts()] == [a]
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cheaper_room").value == 4980.0
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cash_beats_points") is None
    db.mark_hotel_alerts_notified([a], NOW)
    assert db.pending_hotel_alerts() == []
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cheaper_room").notified_at == NOW
