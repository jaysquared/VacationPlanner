from datetime import date, datetime, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.report import build_report, render, route_page_name
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)


def req(dest="BKK", out=date(2026, 12, 19), ret=date(2026, 12, 31), seat=SeatClass.BUSINESS, origin="HAM"):
    return SearchRequest("weihnachten-2026", origin, dest, out, ret, seat, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH", "TG"], 1, 875, "2026-12-19 10:35", "2026-12-20 06:10",
                 level, 7200, 9800, "https://www.google.com/travel/flights?x", {})


def seeded(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 3)
    for p in (9000, 8800, 9100):
        db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(p)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000, "low")]), NOW)
    db.save_result(run, SearchResult(req(out=date(2026, 12, 20), ret=date(2027, 1, 1)), Provider.SERPAPI, [offer(7600)]), NOW)
    db.finish_run(run, 2, "ok", NOW)
    detect_for_run(db, run, cfg, NOW)
    return cfg, db, run


def test_route_page_name():
    assert route_page_name("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == "routes/herbst-2026-HAM-BKK-business.html"


def test_build_report_summarises_best_and_median(config_dir):
    cfg, db, run = seeded(config_dir)
    data = build_report(db, cfg, NOW, last_run_id=run)
    # both date pairs of this run beat the earlier run's 9,000 / 8,800 / 9,100
    assert len(data.new_deals) == 2
    weihnachten = next(routes for slot, window, routes in data.slots if slot.id == "weihnachten-2026")
    r = weihnachten[0]
    assert r.destination.code == "BKK" and r.best.offer.price_total == 7000
    assert r.median == 8800 and round(r.ratio, 3) == 0.795 and r.is_deal is True   # median of 9000, 8800, 9100, 7000, 7600
    assert data.usage == [(Provider.SERPAPI, 5, 250), (Provider.SEARCHAPI, 0, 100)]
    # past slots are hidden, future slots without targets are shown
    ids = [slot.id for slot, _, _ in data.slots]
    assert "herbst-2027" in ids and ids == sorted(ids, key=lambda i: next(s.start for s in cfg.slots if s.id == i))


def test_render_writes_index_and_route_pages(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)
    written = render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "Weihnachtsferien 2026/27" in index and "7,000 €" in index and "2,333 €" in index
    assert "routes/weihnachten-2026-HAM-BKK-business.html" in index
    assert "No targets configured." in index          # herbst-2026 has targets: []
    assert "Not searched yet." in index                # pfingsten-2027 has targets but no data
    assert "google low" in index and "below median" in index and "new low" in index   # reason badges
    assert 'class="level-low"' in index
    assert "<script" not in index
    assert "serpapi 5 / 250" in index and "searchapi 0 / 100" in index   # budget footer
    route = (tmp_path / "routes" / "weihnachten-2026-HAM-BKK-business.html").read_text()
    assert route.count("<tr") >= 6  # header + 5 observations
    assert all(p.exists() for p in written)


def with_frankfurt(config_dir):
    cfg, db, run = seeded(config_dir)          # HAM best is 7,000
    fra = db.start_run(NOW, 1)
    db.save_result(fra, SearchResult(req(origin="FRA"), Provider.SERPAPI, [offer(5250)]), NOW)
    db.save_result(fra, SearchResult(req(origin="FRA", out=date(2026, 12, 20), ret=date(2027, 1, 1)),
                                     Provider.SERPAPI, [offer(6000)]), NOW)
    return cfg, db, run


def test_alternate_origins_share_one_row_per_destination(config_dir):
    cfg, db, run = with_frankfurt(config_dir)
    data = build_report(db, cfg, NOW, last_run_id=run)
    routes = next(routes for slot, _w, routes in data.slots if slot.id == "weihnachten-2026")
    assert len(routes) == 1                                   # HAM and FRA share the (BKK, business) row
    r = routes[0]
    assert r.best.search.origin == "HAM" and r.best.offer.price_total == 7000
    assert [(origin, obs.offer.price_total, round(saving, 4)) for origin, obs, saving in r.alternates] \
        == [("FRA", 5250, 0.25)]


def test_render_shows_the_frankfurt_price_and_its_saving(config_dir, tmp_path):
    cfg, db, run = with_frankfurt(config_dir)
    render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "FRA 5,250 € (−25 %)" in index
    assert index.count("routes/weihnachten-2026-HAM-BKK-business.html") == 1   # no duplicate HAM row
    assert "routes/weihnachten-2026-FRA-BKK-business.html" in index
    assert (tmp_path / "routes" / "weihnachten-2026-FRA-BKK-business.html").exists()


def test_a_dearer_alternate_origin_is_shown_with_a_plus(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)          # HAM best is 7,000
    fra = db.start_run(NOW, 1)
    db.save_result(fra, SearchResult(req(origin="FRA"), Provider.SERPAPI, [offer(7700)]), NOW)
    data = build_report(db, cfg, NOW, last_run_id=run)
    routes = next(routes for slot, _w, routes in data.slots if slot.id == "weihnachten-2026")
    assert routes[0].best.search.origin == "HAM"
    assert round(routes[0].alternates[0][2], 2) == -0.10
    render(db, cfg, tmp_path, NOW, last_run_id=run)
    assert "FRA 7,700 € (+10 %)" in (tmp_path / "index.html").read_text()


def test_an_equally_priced_alternate_origin_shows_no_percentage(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)          # HAM best is 7,000
    fra = db.start_run(NOW, 1)
    db.save_result(fra, SearchResult(req(origin="FRA"), Provider.SERPAPI, [offer(7000)]), NOW)
    render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "FRA 7,000 €<" in index and "−0 %" not in index


def test_render_with_empty_db(config_dir, tmp_path):
    cfg = load_config(config_dir, env={})
    render(Storage(":memory:"), cfg, tmp_path, NOW)
    assert "No deals" in (tmp_path / "index.html").read_text()


def test_route_median_honours_min_history_points(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("min_history_points: 3", "min_history_points: 6"))
    cfg, db, run = seeded(config_dir)            # 5 observations on the BKK route
    data = build_report(db, cfg, NOW, last_run_id=run)
    weihnachten = next(routes for slot, window, routes in data.slots if slot.id == "weihnachten-2026")
    assert weihnachten[0].median is None and weihnachten[0].ratio is None


def test_build_report_adds_previous_and_lowest_ever(config_dir):
    cfg, db, run = seeded(config_dir)
    data = build_report(db, cfg, NOW, last_run_id=run)
    r = next(routes for slot, _w, routes in data.slots if slot.id == "weihnachten-2026")[0]
    assert r.previous == 8800          # cheapest of the earlier run (9000, 8800, 9100)
    assert r.lowest_ever == 7000       # min over every observation of the route
    assert data.run is not None and data.run.id == run and data.run.status == "ok"
    assert data.counts == {"ok": 2} and data.providers == {Provider.SERPAPI: 2}


def test_build_report_without_an_earlier_run_has_no_previous(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    run = db.start_run(NOW, 1)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000)]), NOW)
    data = build_report(db, cfg, NOW, last_run_id=run)
    r = next(routes for slot, _w, routes in data.slots if slot.id == "weihnachten-2026")[0]
    assert r.previous is None and r.lowest_ever == 7000


def test_index_shows_the_week_over_week_and_lowest_seen_columns(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)
    render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "vs last week" in index and "Lowest seen" in index
    assert "▼ 20 %" in index                       # 8,800 → 7,000
    assert '<td class="num down">▼ 20 %</td>' in index
    assert "7,000 €" in index


def test_index_marks_a_rise_and_an_unknown_previous_price(config_dir, tmp_path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 1)
    db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(7000)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7350)]), NOW)
    db.save_result(run, SearchResult(req(dest="MLE"), Provider.SERPAPI, [offer(5000)]), NOW)
    render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert '<td class="num up">▲ 5 %</td>' in index       # 7,000 → 7,350
    assert '<td class="num">–</td>' in index              # MLE has no earlier run
