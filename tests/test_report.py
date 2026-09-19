from datetime import date, datetime, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.report import build_report, render, route_page_name
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)


def req(dest="BKK", out=date(2026, 10, 17), ret=date(2026, 10, 31), seat=SeatClass.BUSINESS):
    return SearchRequest("herbst-2026", "HAM", dest, out, ret, seat, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH", "TG"], 1, 875, "2026-10-17 10:35", "2026-10-18 06:10",
                 level, 7200, 9800, "https://www.google.com/travel/flights?x", {})


def seeded(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 3)
    for p in (9000, 8800, 9100):
        db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(p)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000, "low")]), NOW)
    db.save_result(run, SearchResult(req(out=date(2026, 10, 18), ret=date(2026, 11, 1)), Provider.SERPAPI, [offer(7600)]), NOW)
    db.finish_run(run, 2, "ok", NOW)
    detect_for_run(db, run, cfg, NOW)
    return cfg, db, run


def test_route_page_name():
    assert route_page_name("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == "routes/herbst-2026-HAM-BKK-business.html"


def test_build_report_summarises_best_and_median(config_dir):
    cfg, db, run = seeded(config_dir)
    data = build_report(db, cfg, NOW, last_run_id=run)
    assert len(data.new_deals) == 1
    herbst = next(routes for slot, window, routes in data.slots if slot.id == "herbst-2026")
    r = herbst[0]
    assert r.destination.code == "BKK" and r.best.offer.price_total == 7000
    assert r.median == 8800 and round(r.ratio, 3) == 0.795 and r.is_deal is True   # median of 9000, 8800, 9100, 7000, 7600
    assert data.serpapi_used_this_month == 5 and data.serpapi_budget == 100
    # past slots are hidden, future slots without targets are shown
    ids = [slot.id for slot, _, _ in data.slots]
    assert "herbst-2027" in ids and ids == sorted(ids, key=lambda i: next(s.start for s in cfg.slots if s.id == i))


def test_render_writes_index_and_route_pages(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)
    written = render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "Herbstferien 2026" in index and "7,000 €" in index and "2,333 €" in index
    assert "routes/herbst-2026-HAM-BKK-business.html" in index
    route = (tmp_path / "routes" / "herbst-2026-HAM-BKK-business.html").read_text()
    assert route.count("<tr") >= 6  # header + 5 observations
    assert all(p.exists() for p in written)


def test_render_with_empty_db(config_dir, tmp_path):
    cfg = load_config(config_dir, env={})
    render(Storage(":memory:"), cfg, tmp_path, NOW)
    assert "No deals" in (tmp_path / "index.html").read_text()
