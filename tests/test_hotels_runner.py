from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.hotels.runner import build_hotel_clients, check_hotels, planned_stays
from vacation_planner.models import HotelRate, HotelResult, HotelSource
from vacation_planner.providers.base import ProviderError
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


class FakeHotelClient:
    """Answers every stay from `prices` (watch_id, mode) -> list[HotelRate]; raises for `failing` modes."""

    def __init__(self, source, prices=None, failing=()):
        self.source, self.prices, self.failing, self.calls = source, prices or {}, failing, []

    def check(self, stay):
        self.calls.append(stay)
        if stay.mode in self.failing:
            raise ProviderError(f"{self.source.value} {stay.mode} down")
        rates = self.prices.get((stay.watch_id, stay.mode), [])
        return HotelResult(stay, "ok" if rates else "no_rates", rates, None if rates else "nothing")


GARDEN = "Junior Suite mit eigenem Garten"


def clients(ikos_total=6898.0, waldorf_cash=7402.5, failing=()):
    booking = FakeHotelClient(HotelSource.BOOKING, {
        ("ikos-pfingsten-2027", "rates"): [HotelRate("booking", "cash", total=ikos_total, room=GARDEN, refundable=True)]})
    hilton = FakeHotelClient(HotelSource.HILTON, {
        ("waldorf-herbst-2027", "award"): [HotelRate("direct", "points", points=850000, room="V", award_type="standard")],
        ("waldorf-herbst-2027", "rates"): [HotelRate("direct", "cash", total=waldorf_cash, room="V", refundable=True)],
        ("mango-herbst-2027", "award"): [HotelRate("direct", "points", points=600000, room="K", award_type="standard")],
        ("mango-herbst-2027", "rates"): [HotelRate("direct", "cash", total=3850, room="K", refundable=True)],
    }, failing)
    google = FakeHotelClient(HotelSource.GOOGLE)
    return {HotelSource.BOOKING: booking, HotelSource.HILTON: hilton, HotelSource.GOOGLE: google}


def test_planned_stays_cover_every_source_and_the_points_guests(config_dir: Path):
    cfg = load_config(config_dir, env={})
    got = [(s.watch_id, s.source.value, s.mode, s.adults, s.child_ages) for s in planned_stays(cfg, TODAY)]
    assert got == [
        ("ikos-pfingsten-2027", "booking", "rates", 2, (8,)),
        ("ikos-pfingsten-2027", "google", "rooms", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "rates", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "parity", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "award", 2, ()),
        ("waldorf-herbst-2027", "google", "rooms", 2, (8,)),
        ("mango-herbst-2027", "hilton", "rates", 2, (8,)),
        ("mango-herbst-2027", "hilton", "parity", 2, (8,)),
        ("mango-herbst-2027", "hilton", "award", 2, ()),
        ("mango-herbst-2027", "google", "rooms", 2, (8,)),
    ]


def test_an_ended_watch_spends_nothing(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert [s.watch_id for s in planned_stays(cfg, date(2027, 4, 8))].count("ikos-pfingsten-2027") == 0
    db = Storage(":memory:")
    cl = clients()
    s = check_hotels(cfg, db, cl, date(2027, 4, 8), NOW)
    assert cl[HotelSource.BOOKING].calls == []
    assert {v.watch_id: v.state for v in s.verdicts}["ikos-pfingsten-2027"] == "ended"
    assert db.pending_hotel_alerts() == []


def test_a_quiet_week_stores_everything_and_alerts_nothing(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    s = check_hotels(cfg, db, clients(), TODAY, NOW)
    # ok: Ikos booking + Waldorf rates/award + Mango rates/award; the fake answers parity and Google empty
    assert (s.ok, s.no_rates, s.errors, s.skipped, s.alerts) == (5, 5, 0, 0, 0)
    assert {v.watch_id: v.state for v in s.verdicts} == {
        "ikos-pfingsten-2027": "fine", "waldorf-herbst-2027": "fine", "mango-herbst-2027": "fine"}
    assert db.hotel_batches("waldorf-herbst-2027") == [NOW.isoformat()]


def test_an_alert_is_stored_once_and_repeated_only_after_a_further_drop(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    check_hotels(cfg, db, clients(ikos_total=5000), TODAY, NOW)
    assert [(a.watch_id, a.reason, a.value) for a in db.pending_hotel_alerts()] == [
        ("ikos-pfingsten-2027", "cheaper_room", 5000)]
    later = NOW + timedelta(days=7)
    check_hotels(cfg, db, clients(ikos_total=4900), later.date(), later)    # only 2 % lower: quiet
    assert len(db.pending_hotel_alerts()) == 1
    latest = NOW + timedelta(days=14)
    check_hotels(cfg, db, clients(ikos_total=4700), latest.date(), latest)  # 6 % below the alerted 5,000
    assert [a.value for a in db.pending_hotel_alerts()] == [5000, 4700]


def test_no_standard_reward_alerts_once(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    cl = clients()
    cl[HotelSource.HILTON].prices[("mango-herbst-2027", "award")] = [
        HotelRate("direct", "points", points=900000, room="K", award_type="premium")]
    check_hotels(cfg, db, cl, TODAY, NOW)
    later = NOW + timedelta(days=7)
    check_hotels(cfg, db, cl, later.date(), later)
    assert [a.reason for a in db.pending_hotel_alerts()] == ["no_standard_reward"]


def test_award_check_failing_does_not_falsely_alert(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    check_hotels(cfg, db, clients(failing=("award",)), TODAY, NOW)
    assert db.pending_hotel_alerts() == []


def test_no_award_rows_at_all_does_not_falsely_alert(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    cl = clients()
    del cl[HotelSource.HILTON].prices[("waldorf-herbst-2027", "award")]
    del cl[HotelSource.HILTON].prices[("mango-herbst-2027", "award")]
    check_hotels(cfg, db, cl, TODAY, NOW)
    assert db.pending_hotel_alerts() == []


def test_a_failing_source_is_stored_as_error_and_the_rest_still_runs(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    s = check_hotels(cfg, db, clients(failing=("parity",)), TODAY, NOW)
    assert s.errors == 2
    errs = [c for c in db.hotel_checks("waldorf-herbst-2027", NOW.isoformat()) if c.status == "error"]
    assert [(c.mode, c.error) for c in errs] == [("parity", "hilton parity down")]
    assert {v.watch_id: v.state for v in s.verdicts}["waldorf-herbst-2027"] == "fine"


def test_an_unexpected_client_bug_is_contained(config_dir: Path):
    cfg = load_config(config_dir, env={})
    cl = clients()

    def boom(stay):
        raise KeyError("rooms")

    cl[HotelSource.BOOKING].check = boom
    s = check_hotels(cfg, Storage(":memory:"), cl, TODAY, NOW)
    assert s.errors == 1


def test_without_apify_token_booking_and_hilton_are_skipped_and_google_runs(config_dir: Path):
    cfg = load_config(config_dir, env={"SERPAPI_KEY": "k"})
    built = build_hotel_clients(cfg)
    assert set(built) == {HotelSource.GOOGLE}
    db = Storage(":memory:")
    s = check_hotels(cfg, db, {HotelSource.GOOGLE: FakeHotelClient(HotelSource.GOOGLE)}, TODAY, NOW)
    assert s.skipped == 7 and s.no_rates == 3
    skipped = [c for c in db.hotel_checks("ikos-pfingsten-2027", NOW.isoformat()) if c.status == "skipped"]
    assert [(c.source, c.error) for c in skipped] == [(HotelSource.BOOKING, "APIFY_TOKEN not set")]
    assert s.alerts == 0
    assert db.pending_hotel_alerts() == []


def test_with_both_keys_all_three_clients_are_built(config_dir: Path):
    cfg = load_config(config_dir, env={"SERPAPI_KEY": "k", "APIFY_TOKEN": "t"})
    assert set(build_hotel_clients(cfg)) == {HotelSource.BOOKING, HotelSource.HILTON, HotelSource.GOOGLE}
