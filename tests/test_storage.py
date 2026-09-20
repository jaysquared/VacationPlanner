from datetime import date, datetime, timedelta, timezone

import pytest

from vacation_planner.models import (
    DealReason, Leg, Offer, Provider, SearchRequest, SearchResult, SeatClass,
)
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)


def req(**over) -> SearchRequest:
    base = dict(slot_id="herbst-2026", origin="HAM", destination="BKK",
                outbound_date=date(2026, 10, 17), return_date=date(2026, 10, 31),
                seat=SeatClass.BUSINESS, adults=2, children=1)
    base.update(over)
    return SearchRequest(**base)


LEGS = [Leg("HAM", "FRA", "2026-10-17 10:00", "2026-10-17 11:10"),
        Leg("FRA", "BKK", "2026-10-17 13:55", "2026-10-18 06:00")]


def offer(price: float, level=None, airlines=("LH",), provider=Provider.SERPAPI, legs=LEGS) -> Offer:
    return Offer(provider=provider, price_total=price, currency="EUR", per_person=price / 3,
                 airlines=list(airlines), stops=1, duration_minutes=800,
                 departs_at="2026-10-17T10:00", arrives_at="2026-10-18T06:00",
                 price_level=level, typical_low=None, typical_high=None,
                 google_url="https://g/x", raw={"k": 1}, legs=list(legs))


@pytest.fixture
def db() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


def test_migrations_are_idempotent(tmp_path):
    p = tmp_path / "x.sqlite"
    Storage(p).close()
    Storage(p).close()  # second open must not fail on existing tables


def test_migration_upgrades_a_database_created_by_an_earlier_version(tmp_path):
    import sqlite3
    from importlib import resources

    path = tmp_path / "v1.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.executescript(resources.files("vacation_planner.migrations").joinpath("001_initial.sql").read_text())
    conn.close()

    db = Storage(path)   # applies 002 on top of the v1 database
    run = db.start_run(NOW, planned=1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000)]), NOW)
    assert db.cheapest_offer(sid).legs == LEGS
    assert db.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] == 2
    db.close()


def test_save_result_round_trip(db: Storage):
    run = db.start_run(NOW, planned=1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000, "low"), offer(5400)]), NOW)
    rows = db.searches_in_run(run)
    assert [r.id for r in rows] == [sid]
    assert rows[0].seat is SeatClass.BUSINESS and rows[0].provider is Provider.SERPAPI
    cheapest = db.cheapest_offer(sid)
    assert cheapest.price_total == 5400 and cheapest.airlines == ["LH"]
    assert cheapest.legs == LEGS and db.offer_by_id(cheapest.id).legs == LEGS
    db.finish_run(run, executed=1, status="ok", now=NOW)
    assert db.last_run_id() == run


def test_last_observed_and_provider_count(db: Storage):
    run = db.start_run(NOW, 2)
    assert db.last_observed(req()) is None
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(1)]), NOW)
    db.record_search(run, req(destination="DXB"), Provider.FAST_FLIGHTS, "error", NOW, error="boom")
    assert db.last_observed(req()) == NOW
    assert db.searches_by_provider_since(Provider.SERPAPI, datetime(2026, 9, 1, tzinfo=timezone.utc)) == 1
    assert db.searches_by_provider_since(Provider.FAST_FLIGHTS, datetime(2026, 9, 1, tzinfo=timezone.utc)) == 1
    assert db.searches_in_run(run, status="error")[0].error == "boom"


def test_prior_prices_come_from_earlier_runs_and_exclude_other_slots(db: Storage):
    old = db.start_run(NOW, 2)
    db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), NOW)
    db.save_result(old, SearchResult(req(slot_id="sommer-2027"), Provider.SERPAPI, [offer(7000)]), NOW)
    run = db.start_run(NOW, 2)
    # Both of these belong to the run being judged: another date pair of the same route,
    # searched minutes earlier, is not history.
    db.save_result(run, SearchResult(req(), Provider.FAST_FLIGHTS, [offer(5000)]), NOW)
    db.save_result(run, SearchResult(req(outbound_date=date(2026, 10, 18)), Provider.SERPAPI, [offer(4000)]), NOW)
    assert db.prior_cheapest_prices("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS, before_run_id=run) == [6000]
    assert sorted(db.prior_route_prices("HAM", "BKK", SeatClass.BUSINESS, before_run_id=run)) == [6000, 7000]


def test_best_price_for_uses_the_newest_observation_per_pair(db: Storage):
    earlier = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
    run = db.start_run(earlier, 4)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), earlier)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000)]), NOW)   # newer, dearer
    db.save_result(run, SearchResult(req(outbound_date=date(2026, 10, 18), return_date=date(2026, 11, 1)),
                                     Provider.SERPAPI, [offer(6500)]), NOW)
    db.record_search(run, req(origin="FRA"), Provider.SERPAPI, "error", NOW, error="boom")
    assert db.best_price_for("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == 6500
    assert db.best_price_for("herbst-2026", "FRA", "BKK", SeatClass.BUSINESS) is None
    assert db.best_price_for("sommer-2027", "HAM", "BKK", SeatClass.BUSINESS) is None
    assert db.best_price_for("herbst-2026", "HAM", "BKK", SeatClass.ECONOMY) is None


def test_deals_pending_and_notified(db: Storage):
    run = db.start_run(NOW, 1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000)]), NOW)
    oid = db.cheapest_offer(sid).id
    d1 = db.insert_deal(oid, sid, "herbst-2026", [DealReason.NEW_LOW], 1.0, NOW, notifiable=True)
    d2 = db.insert_deal(oid, sid, "herbst-2026", [DealReason.GOOGLE_LOW], 1.0, NOW, notifiable=False)
    assert [d.id for d in db.pending_deals()] == [d1]
    assert {d.id for d in db.deals_in_run(run)} == {d1, d2}
    assert db.last_notified_price("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) is None
    db.mark_notified([d1], NOW)
    assert db.pending_deals() == []
    assert db.last_notified_price("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == 5000


def test_report_queries(db: Storage):
    earlier = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
    run = db.start_run(earlier, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), earlier)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5500)]), NOW)
    db.save_result(run, SearchResult(req(destination="DXB", seat=SeatClass.BUSINESS), Provider.SERPAPI, [offer(3000)]), NOW)
    best = db.latest_per_pair("herbst-2026")
    assert {(o.search.destination, o.offer.price_total) for o in best} == {("BKK", 5500), ("DXB", 3000)}
    hist = db.route_observations("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS)
    assert [o.offer.price_total for o in hist] == [5500, 6000]


def test_offers_without_legs_round_trip_as_an_empty_list(db: Storage):
    run = db.start_run(NOW, planned=1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000, legs=[])]), NOW)
    assert db.cheapest_offer(sid).legs == []


def test_save_result_is_atomic(db: Storage, monkeypatch):
    run = db.start_run(NOW, planned=1)

    def boom(self, search_id, offers):
        raise RuntimeError("boom")

    monkeypatch.setattr(Storage, "_insert_offers", boom)
    with pytest.raises(RuntimeError):
        db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000)]), NOW)
    assert db.searches_in_run(run, "ok") == []
    assert db.last_observed(req()) is None


def test_timestamps_normalized_to_utc(db: Storage):
    run = db.start_run(NOW, planned=1)
    local_now = datetime(2026, 9, 21, 7, 0, tzinfo=timezone(timedelta(hours=2)))  # == 05:00 UTC
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000)]), local_now)
    assert db.last_observed(req()) == local_now
    stored = db.conn.execute("SELECT requested_at FROM searches LIMIT 1").fetchone()["requested_at"]
    assert stored.endswith("+00:00")



def test_previous_best_price_uses_the_latest_earlier_run(db: Storage):
    r1 = db.start_run(NOW, 2)
    db.save_result(r1, SearchResult(req(), Provider.SERPAPI, [offer(12000)]), NOW)
    db.save_result(r1, SearchResult(req(outbound_date=date(2026, 10, 18), return_date=date(2026, 11, 1)),
                                    Provider.SERPAPI, [offer(11000)]), NOW)
    r2 = db.start_run(NOW, 1)
    db.record_search(r2, req(), Provider.SERPAPI, "error", NOW, error="boom")   # no price: run skipped
    r3 = db.start_run(NOW, 1)
    db.save_result(r3, SearchResult(req(), Provider.SERPAPI, [offer(9000)]), NOW)

    args = ("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS)
    assert db.previous_best_price(*args, before_run_id=r3) == 11000   # cheapest of run 1
    assert db.previous_best_price(*args, before_run_id=r1) is None    # nothing earlier
    assert db.previous_best_price(*args, before_run_id=None) is None
    assert db.previous_best_price("herbst-2026", "FRA", "BKK", SeatClass.BUSINESS, before_run_id=r3) is None


def test_run_info_and_counts(db: Storage):
    run = db.start_run(NOW, planned=3)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), NOW)
    db.save_result(run, SearchResult(req(destination="DXB"), Provider.FAST_FLIGHTS, [offer(3000)]), NOW)
    db.record_search(run, req(destination="MLE"), Provider.SEARCHAPI, "error", NOW, error="boom")
    db.record_search(run, req(destination="CMB"), Provider.SEARCHAPI, "skipped", NOW)
    db.finish_run(run, executed=3, status="ok", now=NOW)

    info = db.run_info(run)
    assert (info.id, info.planned, info.executed, info.status) == (run, 3, 3, "ok")
    assert info.started_at == NOW and info.finished_at == NOW
    assert db.run_info(run + 99) is None
    assert db.search_counts(run) == {"ok": 2, "error": 1, "skipped": 1}
    assert db.provider_counts(run) == {Provider.SERPAPI: 1, Provider.FAST_FLIGHTS: 1}
    assert db.search_counts(run + 99) == {} and db.provider_counts(run + 99) == {}
