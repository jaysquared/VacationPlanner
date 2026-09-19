from datetime import date, datetime, timezone

from vacation_planner.calendar import Window
from vacation_planner.config import load_config
from vacation_planner.models import Nights, Provider, SearchResult, SeatClass
from vacation_planner.planner import candidate_pairs, plan, serpapi_share
from vacation_planner.storage import Storage

TODAY = date(2026, 9, 21)


def test_candidate_pairs_respect_window_and_nights():
    pairs = candidate_pairs(Window(date(2026, 10, 17), date(2026, 11, 1)), Nights(14, 14))
    assert pairs == [(date(2026, 10, 17), date(2026, 10, 31)), (date(2026, 10, 18), date(2026, 11, 1))]


def test_candidate_pairs_empty_when_window_too_short():
    assert candidate_pairs(Window(date(2027, 1, 29), date(2027, 1, 31)), Nights(7, 14)) == []


def test_serpapi_share():
    cfg = load_config_dir()
    assert serpapi_share(cfg.settings.budget) == 25


def load_config_dir(config_dir=None):
    from tests.conftest import REPO_CONFIG
    return load_config(config_dir or REPO_CONFIG, env={})


def test_plan_orders_by_slot_and_assigns_providers(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, TODAY)
    # herbst-2026 first (BKK business), only slots with targets, no past slots
    assert ps[0].request.slot_id == "herbst-2026"
    assert ps[0].request.destination == "BKK" and ps[0].request.seat is SeatClass.BUSINESS
    assert ps[0].request.adults == 2 and ps[0].request.children == 1
    slots = [p.request.slot_id for p in ps]
    assert "fruehjahr-2027" in slots and "herbst-2027" not in slots  # herbst-2027 has no targets
    # per-route cap 3
    assert sum(1 for p in ps if p.request.slot_id == "herbst-2026") == 3
    # first 25 serpapi, rest backup, total <= 60
    assert all(p.provider is Provider.SERPAPI for p in ps[:25])
    assert all(p.provider is Provider.FAST_FLIGHTS for p in ps[25:])
    assert len(ps) <= 60
    # PMI in pfingsten is economy (cabin any)
    pmi = next(p for p in ps if p.request.destination == "PMI")
    assert pmi.request.seat is SeatClass.ECONOMY and 7 <= pmi.request.nights <= 9


def test_plan_prefers_unseen_pairs(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    first = plan(cfg, db, TODAY)
    run = db.start_run(datetime(2026, 9, 21, tzinfo=timezone.utc), 1)
    seen = first[0].request
    db.save_result(run, SearchResult(seen, Provider.SERPAPI, []), datetime(2026, 9, 21, tzinfo=timezone.utc))
    second = plan(cfg, db, TODAY)
    herbst = [p.request for p in second if p.request.slot_id == "herbst-2026"]
    assert seen not in herbst[:2]  # two unseen pairs come before the seen one


def test_plan_skips_started_slots_and_far_future(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, date(2026, 10, 20))  # herbst-2026 already started
    assert all(p.request.slot_id != "herbst-2026" for p in ps)
    ps = plan(cfg, db, date(2025, 1, 1))  # everything > 330 days away
    assert ps == []


def test_plan_without_backup_cuts_at_serpapi_share(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("backup: fast_flights", "backup: null"))
    cfg = load_config_dir(config_dir)
    ps = plan(cfg, Storage(":memory:"), TODAY)
    assert len(ps) <= 25 and all(p.provider is Provider.SERPAPI for p in ps)
