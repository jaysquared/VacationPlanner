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
    row = db.searches_in_run(s.run_id, "error")[0]
    assert row.provider is Provider.FAST_FLIGHTS and "; backup: " in row.error
    assert len(db.searches_in_run(s.run_id, "error")) == 1


def test_primary_quota_then_backup_error_records_once_and_keeps_primary_dead():
    db = Storage(":memory:")
    p, b = Primary(quota_after=0), Backup(fail={("HAM", "DXB")})
    planned = [PlannedSearch(req(d), Provider.SERPAPI) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    # BKK ok on primary; DXB: primary quota -> backup fails -> one error row on backup; MLE: primary dead -> backup ok
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (2, 1, 0, 2, "partial")
    assert len(p.calls) == 2
    err = db.searches_in_run(s.run_id, "error")
    assert len(err) == 1 and err[0].destination == "DXB" and err[0].provider is Provider.FAST_FLIGHTS and "; backup: " in err[0].error
    assert db.searches_in_run(s.run_id)[-1].provider is Provider.FAST_FLIGHTS  # MLE went to backup


def test_backup_planned_quota_does_not_kill_primary():
    db = Storage(":memory:")
    p, b = Primary(), Backup(quota_after=-1)   # backup raises quota on its first call
    planned = [PlannedSearch(req("BKK"), Provider.FAST_FLIGHTS), PlannedSearch(req("DXB"), Provider.SERPAPI)]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks) == (1, 1, 0, 0)
    assert db.searches_in_run(s.run_id, "error")[0].provider is Provider.FAST_FLIGHTS
    assert db.searches_in_run(s.run_id)[0].destination == "DXB" and len(p.calls) == 1


def test_empty_plan():
    db = Storage(":memory:")
    s = execute([], {}, db, settings(), lambda: NOW)
    assert s.status == "empty" and s.planned == 0


class BrokenPrimary(Primary):
    """A client with a bug: it raises a raw exception instead of a ProviderError."""

    def search(self, req, raw_dir=None):
        raise KeyError("departure_airport")


def test_client_bug_is_recorded_as_error_and_run_still_finishes():
    db = Storage(":memory:")
    s = execute([PlannedSearch(req("BKK"), Provider.SERPAPI)],
                {Provider.SERPAPI: BrokenPrimary(), Provider.FAST_FLIGHTS: Backup()},
                db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.status) == (0, 1, "partial")
    err = db.searches_in_run(s.run_id, "error")
    assert len(err) == 1 and err[0].provider is Provider.SERPAPI
    assert err[0].error == "unexpected KeyError: 'departure_airport'"
    run = db.conn.execute("SELECT finished_at, status FROM runs WHERE id=?", (s.run_id,)).fetchone()
    assert run["finished_at"] is not None and run["status"] == "partial"
