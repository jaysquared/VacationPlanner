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


def test_evaluate_google_low_and_under_max():
    assert evaluate(900, 300, "low", [], [], 300, S)[0] == [DealReason.GOOGLE_LOW, DealReason.UNDER_MAX]
    assert evaluate(900, 301, "typical", [], [], 300, S)[0] == []


def req(dest="BKK", slot="weihnachten-2026", origin="HAM"):
    return SearchRequest(slot, origin, dest, date(2026, 12, 19), date(2026, 12, 31), SeatClass.BUSINESS, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH"], 1, 800, "", "", level, None, None, "https://g", {})


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
