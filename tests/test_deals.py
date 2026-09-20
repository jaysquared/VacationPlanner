from datetime import date, datetime, timezone

from vacation_planner.config import DealSettings, load_config
from vacation_planner.deals import detect_for_run, evaluate
from vacation_planner.models import DealReason, Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.storage import Storage

S = DealSettings(median_ratio=0.85, min_history_points=3, renotify_drop_ratio=0.95, lookahead_days=330)
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def test_evaluate_below_median_uses_slot_history_first():
    reasons, median = evaluate(800, 800 / 3, None, [1000, 1000, 1000], [500, 500, 500], None, S)
    assert reasons == [DealReason.BELOW_MEDIAN, DealReason.NEW_LOW] and median == 1000


def test_evaluate_falls_back_to_route_history():
    reasons, median = evaluate(800, 1, None, [1000], [1000, 1000, 1000], None, S)
    assert DealReason.BELOW_MEDIAN in reasons and median == 1000
    reasons, median = evaluate(800, 1, None, [1000], [1000], None, S)
    assert DealReason.BELOW_MEDIAN not in reasons and median is None


def test_evaluate_new_low_needs_min_history_points():
    """One or two observations are not a record to beat; they are the start of one."""
    assert evaluate(1800, 1, None, [2000, 1900], [], None, S)[0] == []
    assert evaluate(1800, 1, None, [2000, 1900, 1950], [], None, S)[0] == [DealReason.NEW_LOW]


def test_evaluate_google_low_and_under_max():
    assert evaluate(900, 300, "low", [], [], 300, S)[0] == [DealReason.GOOGLE_LOW, DealReason.UNDER_MAX]
    assert evaluate(900, 301, "typical", [], [], 300, S)[0] == []


def req(dest="BKK", slot="weihnachten-2026", origin="HAM",
        out=date(2026, 12, 19), ret=date(2026, 12, 31)):
    return SearchRequest(slot, origin, dest, out, ret, SeatClass.BUSINESS, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH"], 1, 800, "", "", level, None, None, "https://g", {})


def alternate_origin_run(config_dir, ham: float | None, fra: float):
    """Seed one HAM observation (unless None) and detect on a later FRA search.

    Returns (detected deals, storage, run id) so callers can also check what was stored.
    """
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    if ham is not None:
        home = db.start_run(NOW, 1)
        db.save_result(home, SearchResult(req(), Provider.SERPAPI, [offer(ham)]), NOW)
    run = db.start_run(NOW, 1)
    db.save_result(run, SearchResult(req(origin="FRA"), Provider.SERPAPI, [offer(fra, "low")]), NOW)
    return detect_for_run(db, run, cfg, NOW), db, run


def test_frankfurt_must_beat_hamburg_by_the_margin(config_dir):
    deals, _db, _run = alternate_origin_run(config_dir, ham=12000, fra=9000)   # 25 %, 3,000 EUR
    assert len(deals) == 1 and deals[0].search.origin == "FRA"
    assert deals[0].reasons == [DealReason.GOOGLE_LOW, DealReason.CHEAPER_THAN_HOME]


def test_frankfurt_too_close_to_hamburg_is_no_deal_at_all(config_dir):
    deals, db, run = alternate_origin_run(config_dir, ham=12000, fra=11000)   # 8 %, google low ignored
    assert deals == []
    assert db.deals_in_run(run) == []   # nothing recorded, not merely nothing returned


def test_frankfurt_needs_both_the_ratio_and_the_absolute_saving(config_dir):
    assert len(alternate_origin_run(config_dir, ham=12000, fra=9600)[0]) == 1   # exactly 20 %, 2,400 EUR
    assert alternate_origin_run(config_dir, ham=2000, fra=1600)[0] == []        # 20 % but only 400 EUR


def test_frankfurt_without_hamburg_history_is_evaluated_normally(config_dir):
    deals, _db, _run = alternate_origin_run(config_dir, ham=None, fra=9000)
    assert len(deals) == 1 and deals[0].reasons == [DealReason.GOOGLE_LOW]


def test_the_home_origin_is_never_margin_checked(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    run = db.start_run(NOW, 1)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(11000, "low")]), NOW)
    assert [d.reasons for d in detect_for_run(db, run, cfg, NOW)] == [[DealReason.GOOGLE_LOW]]


def test_detect_for_run_records_deals_and_renotify_rule(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 3)
    for p in (9000, 9000, 9000):
        db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(p)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000, "low")]), NOW)  # 78% of median; 2,333 pp is over the 2,000 max
    db.save_result(run, SearchResult(req("DXB"), Provider.SERPAPI, [offer(9000, "typical")]), NOW)  # DXB is not in the catalogue -> no max
    deals = detect_for_run(db, run, cfg, NOW)
    assert len(deals) == 1
    d = deals[0]
    assert d.search.destination == "BKK" and d.median == 9000
    assert set(d.reasons) == {DealReason.BELOW_MEDIAN, DealReason.GOOGLE_LOW, DealReason.NEW_LOW}
    assert d.notifiable is True and db.pending_deals()[0].id == d.deal_id
    db.mark_notified([d.deal_id], NOW)

    run2 = db.start_run(NOW, 1)
    db.save_result(run2, SearchResult(req(), Provider.SERPAPI, [offer(6900, "low")]), NOW)   # only 1.4% lower
    d2 = detect_for_run(db, run2, cfg, NOW)[0]
    assert d2.notifiable is False and db.pending_deals() == []

    run3 = db.start_run(NOW, 1)
    db.save_result(run3, SearchResult(req(), Provider.SERPAPI, [offer(6000, "low")]), NOW)   # >5% lower
    assert detect_for_run(db, run3, cfg, NOW)[0].notifiable is True


def seed_history(db, prices, slot="weihnachten-2026"):
    """One earlier run holding `prices` for the (slot, HAM, BKK, business) route."""
    old = db.start_run(NOW, len(prices))
    for p in prices:
        db.save_result(old, SearchResult(req(slot=slot), Provider.SERPAPI, [offer(p)]), NOW)
    return old


def test_a_cheaper_pair_in_the_same_run_is_not_history(config_dir):
    """Another date pair searched minutes earlier is this week's price, not last week's."""
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    seed_history(db, (10000, 10000, 10000))
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(8000)]), NOW)
    db.save_result(run, SearchResult(req(out=date(2026, 12, 20), ret=date(2027, 1, 1)),
                                     Provider.SERPAPI, [offer(9500)]), NOW)
    deals = {d.search.outbound_date: d for d in detect_for_run(db, run, cfg, NOW)}
    assert deals[date(2026, 12, 19)].reasons == [DealReason.BELOW_MEDIAN, DealReason.NEW_LOW]
    # the 8,000 EUR pair above it does not rob this one of its new low
    assert deals[date(2026, 12, 20)].reasons == [DealReason.NEW_LOW]
    assert deals[date(2026, 12, 20)].median == 10000


def test_below_median_ignores_the_prices_of_the_same_run(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    seed_history(db, (10000, 10000, 10000))
    run = db.start_run(NOW, 4)
    for day in (20, 21, 22):   # three cheap pairs that would drag a same-run median down
        db.save_result(run, SearchResult(req(out=date(2026, 12, day), ret=date(2027, 1, 1)),
                                         Provider.SERPAPI, [offer(5000)]), NOW)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(8400)]), NOW)
    last = [d for d in detect_for_run(db, run, cfg, NOW) if d.search.outbound_date == date(2026, 12, 19)][0]
    assert last.median == 10000 and DealReason.BELOW_MEDIAN in last.reasons


def test_a_single_earlier_observation_is_no_new_low(config_dir):
    """Run 2 of a fresh database: one prior price is not enough to call anything a record."""
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    seed_history(db, (10000,))
    run = db.start_run(NOW, 1)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(8000)]), NOW)
    assert detect_for_run(db, run, cfg, NOW) == []

    run2 = db.start_run(NOW, 1)                     # a third and fourth observation later on
    db.save_result(run2, SearchResult(req(), Provider.SERPAPI, [offer(9000)]), NOW)
    run3 = db.start_run(NOW, 1)
    db.save_result(run3, SearchResult(req(), Provider.SERPAPI, [offer(7500)]), NOW)
    assert DealReason.NEW_LOW in detect_for_run(db, run3, cfg, NOW)[0].reasons
