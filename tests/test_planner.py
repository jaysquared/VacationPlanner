import re
from datetime import date, datetime, timezone

from vacation_planner.calendar import Window
from vacation_planner.config import load_config
from vacation_planner.models import Nights, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.planner import candidate_pairs, plan, provider_shares
from vacation_planner.storage import Storage

TODAY = date(2026, 9, 21)


def test_candidate_pairs_respect_window_and_nights():
    pairs = candidate_pairs(Window(date(2026, 10, 17), date(2026, 11, 1)), Nights(14, 14))
    assert pairs == [(date(2026, 10, 17), date(2026, 10, 31)), (date(2026, 10, 18), date(2026, 11, 1))]


def test_candidate_pairs_empty_when_window_too_short():
    assert candidate_pairs(Window(date(2027, 1, 29), date(2027, 1, 31)), Nights(7, 14)) == []


def load_config_dir(config_dir=None):
    from tests.conftest import REPO_CONFIG
    return load_config(config_dir or REPO_CONFIG, env={})


def set_order(config_dir, order_yaml: str) -> None:
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"  order:\n(?:    - .*\n)+", f"  order:\n{order_yaml}", st.read_text()))


def test_provider_shares_from_repo_config():
    cfg = load_config_dir()
    assert provider_shares(cfg, Storage(":memory:"), TODAY) == [
        (Provider.SERPAPI, 62), (Provider.SEARCHAPI, 25), (Provider.FAST_FLIGHTS, None)]


def test_provider_shares_are_capped_by_the_remaining_month():
    cfg = load_config_dir()
    db = Storage(":memory:")
    seed(db, Provider.SERPAPI, 240)
    assert provider_shares(cfg, db, TODAY)[0] == (Provider.SERPAPI, 10)
    seed(db, Provider.SERPAPI, 10)
    assert provider_shares(cfg, db, TODAY)[0] == (Provider.SERPAPI, 0)


def test_plan_orders_by_slot_and_assigns_providers(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, TODAY)
    # weihnachten-2026 first (17 long-haul business targets), only slots with targets, no past slots
    first = ps[0].request
    assert first.slot_id == "weihnachten-2026"
    assert first.destination == "HKT" and first.seat is SeatClass.BUSINESS
    assert first.adults == 2 and first.children == 1
    assert first.origin == "HAM"
    # free window Sat 19 Dec 2026 .. Sun 3 Jan 2027, slot nights 10-14
    assert first.outbound_date == date(2026, 12, 19) and 10 <= first.nights <= 14
    assert first.return_date <= date(2027, 1, 3)
    slots = [p.request.slot_id for p in ps]
    assert "pfingsten-2027" in slots and "herbst-2026" not in slots  # herbst-2026 has no targets
    # per-route cap 3
    assert sum(1 for p in ps if p.request.destination == "HKT") == 3
    # 20 routes x 3 pairs, all inside the SerpApi per-run share of 62
    assert len(ps) == 60 and all(p.provider is Provider.SERPAPI for p in ps)
    # TFS in pfingsten is economy (cabin any)
    tfs = next(p for p in ps if p.request.destination == "TFS")
    assert tfs.request.seat is SeatClass.ECONOMY and 7 <= tfs.request.nights <= 9


def test_plan_walks_down_the_provider_order_as_shares_run_out(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("monthly_budget: 250", "monthly_budget: 8")
                                .replace("monthly_budget: 100", "monthly_budget: 4"))
    cfg = load_config_dir(config_dir)
    ps = plan(cfg, Storage(":memory:"), TODAY)
    assert len(ps) == 60
    assert [p.provider for p in ps[:3]] == [Provider.SERPAPI, Provider.SERPAPI, Provider.SEARCHAPI]
    assert all(p.provider is Provider.FAST_FLIGHTS for p in ps[3:])


def test_plan_skips_a_provider_whose_month_is_spent(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_pairs_per_route_per_run: 3", "max_pairs_per_route_per_run: 6"))
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    seed(db, Provider.SERPAPI, 250)
    ps = plan(cfg, db, TODAY)
    assert len(ps) == 120   # 20 routes x 6 pairs, exactly the max_searches_per_run cap
    assert all(p.provider is Provider.SEARCHAPI for p in ps[:25])
    assert all(p.provider is Provider.FAST_FLIGHTS for p in ps[25:])


def test_plan_is_empty_when_every_budgeted_provider_is_spent(config_dir):
    set_order(config_dir, "    - { name: serpapi, monthly_budget: 250 }\n"
                          "    - { name: searchapi, monthly_budget: 100 }\n")
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    seed(db, Provider.SERPAPI, 250)
    seed(db, Provider.SEARCHAPI, 100)
    assert plan(cfg, db, TODAY) == []


def test_plan_stops_at_a_spent_budget_when_nothing_unlimited_follows(config_dir):
    set_order(config_dir, "    - { name: serpapi, monthly_budget: 250 }\n")
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    seed(db, Provider.SERPAPI, 245)
    ps = plan(cfg, db, TODAY)
    assert len(ps) == 5 and all(p.provider is Provider.SERPAPI for p in ps)


def test_plan_respects_max_searches_per_run(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_searches_per_run: 120", "max_searches_per_run: 7"))
    cfg = load_config_dir(config_dir)
    ps = plan(cfg, Storage(":memory:"), TODAY)
    assert len(ps) == 7


def test_plan_prefers_unseen_pairs(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    first = plan(cfg, db, TODAY)
    run = db.start_run(datetime(2026, 9, 21, tzinfo=timezone.utc), 1)
    seen = first[0].request
    db.save_result(run, SearchResult(seen, Provider.SERPAPI, []), datetime(2026, 9, 21, tzinfo=timezone.utc))
    second = plan(cfg, db, TODAY)
    hkt = [p.request for p in second if p.request.destination == "HKT"]
    assert hkt and seen not in hkt  # unseen pairs crowd the observed one out of the per-route cap


def test_plan_skips_started_slots_and_far_future(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, date(2026, 12, 22))  # weihnachten-2026 already started
    assert ps and all(p.request.slot_id != "weihnachten-2026" for p in ps)
    ps = plan(cfg, db, date(2025, 1, 1))  # everything > 330 days away
    assert ps == []


def test_plan_never_searches_past_outbound_dates(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    today = date(2026, 12, 19)  # weihnachten-2026 free window starts here; slot.start (21 Dec) is still eligible
    ps = plan(cfg, db, today)
    weihnachten = [p.request for p in ps if p.request.slot_id == "weihnachten-2026"]
    assert weihnachten
    assert all(p.outbound_date > today for p in weihnachten)


SEED_AT = datetime(2026, 9, 10, tzinfo=timezone.utc)


def seed(db: Storage, provider: Provider, n: int) -> None:
    """n searches already spent this month by `provider`, on a route nothing plans."""
    run = db.start_run(SEED_AT, n)
    r = SearchRequest("seed", "XXX", "YYY", date(2026, 12, 1), date(2026, 12, 8), SeatClass.ECONOMY, 2, 1)
    for _ in range(n):
        db.record_search(run, r, provider, "ok", SEED_AT)
