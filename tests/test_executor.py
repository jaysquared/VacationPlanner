from datetime import date, datetime, timezone

from vacation_planner.config import ProviderEntry, ProviderSettings
from vacation_planner.models import PlannedSearch, Provider, SearchRequest, SeatClass
from vacation_planner.providers.executor import execute
from vacation_planner.providers.fake import FakeFlightClient
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

SERP, SEARCH, FAST = Provider.SERPAPI, Provider.SEARCHAPI, Provider.FAST_FLIGHTS


def req(dest):
    return SearchRequest("herbst-2026", "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


class Serp(FakeFlightClient):
    provider = SERP


class Search(FakeFlightClient):
    provider = SEARCH


class Fast(FakeFlightClient):
    provider = FAST


def settings(*names: Provider) -> ProviderSettings:
    return ProviderSettings(order=[ProviderEntry(name=n) for n in (names or (SERP, SEARCH, FAST))])


def all_clients(serp=None, search=None, fast=None):
    return {SERP: serp or Serp(), SEARCH: search or Search(), FAST: fast or Fast()}


def test_all_ok():
    db = Storage(":memory:")
    clients = all_clients()
    planned = [PlannedSearch(req("BKK"), SERP), PlannedSearch(req("DXB"), SEARCH), PlannedSearch(req("MLE"), FAST)]
    s = execute(planned, clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (3, 0, 0, 0, "ok")
    rows = db.searches_in_run(s.run_id)
    assert [(r.destination, r.provider) for r in rows] == [("BKK", SERP), ("DXB", SEARCH), ("MLE", FAST)]


def test_first_provider_error_falls_back_to_the_second():
    db = Storage(":memory:")
    clients = all_clients(serp=Serp(fail={("HAM", "BKK")}))
    s = execute([PlannedSearch(req("BKK"), SERP)], clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.fallbacks, s.status) == (1, 0, 1, "ok")
    assert db.searches_in_run(s.run_id)[0].provider is SEARCH
    # the failed attempt is recorded too: SerpApi charged for it
    err = db.searches_in_run(s.run_id, "error")
    assert len(err) == 1 and err[0].provider is SERP
    assert db.searches_by_provider_since(SERP, NOW) == 1
    assert len(clients[FAST].calls) == 0


def test_quota_marks_a_provider_dead_for_the_rest_of_the_run():
    db = Storage(":memory:")
    clients = all_clients(serp=Serp(quota_after=0))
    planned = [PlannedSearch(req(d), SERP) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, clients, db, settings(), lambda: NOW)
    # BKK ok on serpapi, DXB raises quota -> searchapi, MLE goes straight to searchapi
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (3, 0, 0, 2, "ok")
    assert len(clients[SERP].calls) == 2
    assert [r.provider for r in db.searches_in_run(s.run_id)] == [SERP, SEARCH, SEARCH]
    assert db.searches_in_run(s.run_id, "error")[0].provider is SERP


def test_falls_through_to_the_third_provider():
    db = Storage(":memory:")
    clients = all_clients(serp=Serp(fail={("HAM", "BKK")}), search=Search(fail={("HAM", "BKK")}))
    s = execute([PlannedSearch(req("BKK"), SERP)], clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.fallbacks, s.status) == (1, 0, 1, "ok")
    assert [r.provider for r in db.searches_in_run(s.run_id, "error")] == [SERP, SEARCH]
    assert db.searches_in_run(s.run_id)[0].provider is FAST


def test_every_provider_failing_is_one_error_and_a_row_per_attempt():
    db = Storage(":memory:")
    fails = {("HAM", "BKK")}
    clients = all_clients(serp=Serp(fail=fails), search=Search(fail=fails), fast=Fast(fail=fails))
    s = execute([PlannedSearch(req("BKK"), SERP)], clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.status) == (0, 1, 0, "partial")
    assert [r.provider for r in db.searches_in_run(s.run_id, "error")] == [SERP, SEARCH, FAST]


def test_a_provider_without_a_client_is_skipped_over():
    db = Storage(":memory:")
    clients = {FAST: Fast()}
    s = execute([PlannedSearch(req("BKK"), SERP)], clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.fallbacks) == (1, 0, 1)
    assert db.searches_in_run(s.run_id)[0].provider is FAST
    assert db.searches_in_run(s.run_id, "error") == []


def test_no_usable_provider_records_skipped_without_calling_anything():
    db = Storage(":memory:")
    s = execute([PlannedSearch(req("BKK"), SERP)], {}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.status) == (0, 0, 1, "partial")
    row = db.searches_in_run(s.run_id, "skipped")[0]
    assert row.provider is SERP and row.error == "no provider available"


def test_fallback_only_walks_forward_from_the_planned_provider():
    db = Storage(":memory:")
    clients = all_clients(search=Search(fail={("HAM", "BKK")}))
    s = execute([PlannedSearch(req("BKK"), SEARCH)], clients, db, settings(), lambda: NOW)
    assert (s.ok, s.fallbacks) == (1, 1)
    assert len(clients[SERP].calls) == 0            # serpapi comes earlier: never tried
    assert db.searches_in_run(s.run_id)[0].provider is FAST


def test_quota_on_a_later_provider_does_not_stop_the_earlier_one():
    db = Storage(":memory:")
    clients = all_clients(search=Search(quota_after=-1))   # quota on its first call
    planned = [PlannedSearch(req("BKK"), SEARCH), PlannedSearch(req("DXB"), SERP)]
    s = execute(planned, clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks) == (2, 0, 0, 1)
    assert [r.provider for r in db.searches_in_run(s.run_id)] == [FAST, SERP]
    assert len(clients[SERP].calls) == 1


def test_empty_plan():
    db = Storage(":memory:")
    s = execute([], {}, db, settings(), lambda: NOW)
    assert s.status == "empty" and s.planned == 0


class Broken(Serp):
    """A client with a bug: it raises a raw exception instead of a ProviderError."""

    def search(self, req, raw_dir=None):
        raise KeyError("departure_airport")


def test_client_bug_is_recorded_as_error_and_the_run_still_finishes():
    db = Storage(":memory:")
    s = execute([PlannedSearch(req("BKK"), SERP)], {SERP: Broken()}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.status) == (0, 1, "partial")
    err = db.searches_in_run(s.run_id, "error")
    assert len(err) == 1 and err[0].provider is SERP
    assert err[0].error == "unexpected KeyError: 'departure_airport'"
    run = db.conn.execute("SELECT finished_at, status FROM runs WHERE id=?", (s.run_id,)).fetchone()
    assert run["finished_at"] is not None and run["status"] == "partial"


def test_a_client_bug_still_lets_the_next_provider_answer():
    db = Storage(":memory:")
    s = execute([PlannedSearch(req("BKK"), SERP)], {SERP: Broken(), SEARCH: Search()}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.fallbacks) == (1, 0, 1)
    assert db.searches_in_run(s.run_id)[0].provider is SEARCH


def test_fallbacks_still_count_against_the_monthly_budget():
    db = Storage(":memory:")
    clients = all_clients(serp=Serp(fail={("HAM", "BKK"), ("HAM", "DXB")}))
    planned = [PlannedSearch(req(d), SERP) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.fallbacks) == (3, 0, 2)   # a fallback that worked is not an error
    assert db.searches_by_provider_since(SERP, NOW) == 3   # 2 failed attempts + 1 ok


def test_every_provider_out_of_quota_errors_once_then_skips_the_rest():
    db = Storage(":memory:")
    clients = all_clients(serp=Serp(quota_after=-1), search=Search(quota_after=-1), fast=Fast(quota_after=-1))
    planned = [PlannedSearch(req(d), SERP) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, clients, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (0, 1, 2, 0, "partial")
    err = db.searches_in_run(s.run_id, "error")
    assert [(r.destination, r.provider) for r in err] == [("BKK", SERP), ("BKK", SEARCH), ("BKK", FAST)]
    skipped = db.searches_in_run(s.run_id, "skipped")
    assert [r.destination for r in skipped] == ["DXB", "MLE"]
    assert all(r.error == "no provider available" for r in skipped)
    assert all(len(c.calls) == 1 for c in clients.values())   # nothing is called twice
