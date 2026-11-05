from datetime import datetime, timedelta, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.hotels.cards import build_hotel_cards
from vacation_planner.models import HotelRate, HotelResult, HotelSource, HotelStay
from vacation_planner.notify import compose_digest
from vacation_planner.report import build_report, render
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)
GARDEN = "Junior Suite mit eigenem Garten"


def ikos_stay(source=HotelSource.BOOKING, mode="rates"):
    from datetime import date
    return HotelStay("ikos-pfingsten-2027", source, "u", mode, date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def seeded(config_dir, totals=(7100.0, 6898.0)):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    for i, total in enumerate(totals):
        t = NOW - timedelta(days=7 * (len(totals) - 1 - i))
        db.save_hotel_result(t.isoformat(), HotelResult(ikos_stay(), "ok", [
            HotelRate("booking", "cash", total=total, room=GARDEN, rate_name="All-Inclusive", refundable=True,
                      url="https://www.booking.com/x"),
            HotelRate("booking", "cash", total=6503.58, room="Junior Suite mit Gartenblick", refundable=True)]), t)
        db.save_hotel_result(t.isoformat(), HotelResult(ikos_stay(HotelSource.GOOGLE, "rooms"), "no_rates", [],
                                                        "Google priced other dates"), t)
    return cfg, db


def test_card_shows_the_watched_room_per_channel_with_last_weeks_change(config_dir: Path):
    cfg, db = seeded(config_dir)
    card = next(c for c in build_hotel_cards(db, cfg, NOW.date()) if c.watch_id == "ikos-pfingsten-2027")
    assert card.name == "Ikos Porto Petro" and card.dates == "7–14 May 2027"
    assert card.verdict.state == "fine"
    assert [(r.label, r.price, r.change, r.change_class) for r in card.rows] == [("Booking.com", "6,898 €", "▼ 3 %", "down")]
    assert card.rows[0].detail == f"{GARDEN} · free cancellation"
    assert card.problems == ["Google Hotels: Google priced other dates"]


def test_watches_never_checked_have_no_card(config_dir: Path):
    cfg, db = seeded(config_dir)
    assert [c.watch_id for c in build_hotel_cards(db, cfg, NOW.date())] == ["ikos-pfingsten-2027"]


def test_report_index_has_a_hotels_section(config_dir: Path, tmp_path: Path):
    cfg, db = seeded(config_dir)
    render(db, cfg, tmp_path, NOW)
    html = (tmp_path / "index.html").read_text()
    assert "<h2>Hotels</h2>" in html and "Ikos Porto Petro" in html and "Keep your booking" in html


def test_digest_carries_hotel_verdicts_and_puts_alerts_first(config_dir: Path):
    cfg, db = seeded(config_dir, totals=(7100.0, 5000.0))
    db.insert_hotel_alert("ikos-pfingsten-2027", NOW.isoformat(), "cheaper_room", 5000, 5076,
                          "Rebook: Junior Suite mit eigenem Garten 5,000 € on booking (you pay 5,640 €, alert at ≤ 5,076 €)", NOW)
    data = build_report(db, cfg, NOW, None)
    subject, text, html = compose_digest(data, [], cfg, None, hotel_alerts=db.pending_hotel_alerts())
    assert subject.startswith("Vacation Planner · 1 hotel alert")
    assert text.index("Hotel alerts") < text.index("New deals")
    assert "Rebook: Junior Suite mit eigenem Garten 5,000 €" in text and "Ikos Porto Petro · 7–14 May 2027" in text
    assert "Hotel alerts" in html and "Hotels" in html


def test_quiet_digest_still_lists_the_hotel_verdict(config_dir: Path):
    cfg, db = seeded(config_dir)
    subject, text, _ = compose_digest(build_report(db, cfg, NOW, None), [], cfg, None)
    assert "Hotel alerts" not in text
    assert "Keep your booking: cheapest Junior Suite mit eigenem Garten 6,898 € on booking" in text
