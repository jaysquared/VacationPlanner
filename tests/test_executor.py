from datetime import date, datetime, timezone

from vacation_planner.config import ProviderSettings
from vacation_planner.models import PlannedSearch, Provider, SearchRequest, SeatClass
from vacation_planner.providers.executor import execute
from vacation_planner.providers.fake import FakeFlightClient
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def req(dest):
    return SearchRequest("herbst-2026", "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


class Primary(FakeFlightClient):
    provider = Provider.SERPAPI


class Backup(FakeFlightClient):
    provider = Provider.FAST_FLIGHTS


def settings(backup=Provider.FAST_FLIGHTS):
    return ProviderSettings(primary=Provider.SERPAPI, backup=backup)


def test_all_ok():
    db = Storage(":memory:")
    p, b = Primary(), Backup()
    planned = [PlannedSearch(req("BKK"), Provider.SERPAPI), PlannedSearch(req("DXB"), Provider.FAST_FLIGHTS)]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (2, 0, 0, 0, "ok")
    rows = db.searches_in_run(s.run_id)
    assert [(r.destination, r.provider) for r in rows] == [("BKK", Provider.SERPAPI), ("DXB", Provider.FAST_FLIGHTS)]


def test_primary_error_falls_back():
    db = Storage(":memory:")
    p, b = Primary(fail={("HAM", "BKK")}), Backup()
    s = execute([PlannedSearch(req("BKK"), Provider.SERPAPI)], {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.fallbacks, s.status) == (1, 1, "ok")
    assert db.searches_in_run(s.run_id)[0].provider is Provider.FAST_FLIGHTS


def test_quota_marks_primary_dead_for_rest_of_run():
    db = Storage(":memory:")
    p, b = Primary(quota_after=0), Backup()
    planned = [PlannedSearch(req(d), Provider.SERPAPI) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    # call 1 (BKK) ok, call 2 (DXB) raises quota -> fallback, MLE goes straight to backup
    assert (s.ok, s.fallbacks) == (3, 2)
    assert len(p.calls) == 2
    assert all(r.provider is Provider.FAST_FLIGHTS for r in db.searches_in_run(s.run_id)[1:])


def test_no_backup_records_error_and_skipped():
    db = Storage(":memory:")
    p = Primary(quota_after=0)
    planned = [PlannedSearch(req(d), Provider.SERPAPI) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, {Provider.SERPAPI: p}, db, settings(backup=None), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.status) == (1, 1, 1, "partial")
    assert db.searches_in_run(s.run_id, "error")[0].error.startswith("fake quota")
    assert db.searches_in_run(s.run_id, "skipped")[0].destination == "MLE"


def test_backup_failure_is_error():
    db = Storage(":memory:")
    p, b = Primary(fail={("HAM", "BKK")}), Backup(fail={("HAM", "BKK")})
    s = execute([PlannedSearch(req("BKK"), Provider.SERPAPI)], {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.status) == (0, 1, "partial")


def test_empty_plan():
    db = Storage(":memory:")
    s = execute([], {}, db, settings(), lambda: NOW)
    assert s.status == "empty" and s.planned == 0
