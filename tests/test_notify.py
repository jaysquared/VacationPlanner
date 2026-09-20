import logging
import re
from datetime import date, datetime, timezone

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.notify import compose, compose_digest, send_pending
from vacation_planner.providers.executor import ExecutionSummary
from vacation_planner.report import NewDeal, build_report
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)
ENV = {"SMTP_HOST": "smtp.test", "SMTP_PORT": "2525", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
       "MAIL_FROM": "planner@test", "MAIL_TO": "me@test"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.sent, self.logged_in, self.tls = host, port, [], None, False
        FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): self.tls = True
    def login(self, u, p): self.logged_in = (u, p)
    def send_message(self, msg): self.sent.append(msg)


class FailingSMTP(FakeSMTP):
    def send_message(self, msg): raise OSError("smtp down")


def offer(price, level=None, airlines=("SWISS", "Bangkok Airways"), stops=2, url="https://g/1",
          provider=Provider.SERPAPI):
    return Offer(provider, price, "EUR", price / 3, list(airlines), stops, 900,
                 "2026-12-19 10:35", "2026-12-20 06:10", level, None, None, url, {})


def req(dest="HKT", origin="HAM", out=date(2026, 12, 19), ret=date(2026, 12, 29),
        slot="weihnachten-2026"):
    return SearchRequest(slot, origin, dest, out, ret, SeatClass.BUSINESS, 2, 1)


def seeded(config_dir, env=ENV, fra_level=None):
    """Two runs on HAM→HKT (12,000 then 9,000 'low'), one MLE route seen once, FRA at 7,000."""
    cfg = load_config(config_dir, env=env)
    db = Storage(":memory:")
    r1 = db.start_run(NOW, 1)
    db.save_result(r1, SearchResult(req(), Provider.SERPAPI, [offer(12000)]), NOW)
    db.finish_run(r1, 1, "ok", NOW)
    r2 = db.start_run(NOW, 4)
    db.save_result(r2, SearchResult(req(), Provider.SERPAPI, [offer(9000, "low")]), NOW)
    db.save_result(r2, SearchResult(req(dest="MLE"), Provider.SEARCHAPI, [offer(13000)]), NOW)
    db.save_result(r2, SearchResult(req(origin="FRA"), Provider.SERPAPI, [offer(7000, fra_level)]), NOW)
    db.record_search(r2, req(dest="CMB"), Provider.FAST_FLIGHTS, "error", NOW, error="boom")
    db.finish_run(r2, 4, "ok", NOW)
    detect_for_run(db, r2, cfg, NOW)
    return cfg, db, r2


def digest(cfg, db, report_url="https://x/report"):
    data = build_report(db, cfg, NOW, db.last_run_id())
    deals = [NewDeal(d, db.search_by_id(d.search_id), db.offer_by_id(d.offer_id))
             for d in db.pending_deals()]
    return compose_digest(data, deals, cfg, report_url)


def test_subject_names_the_deals_and_the_nearest_cheapest_trip(config_dir):
    cfg, db, _run = seeded(config_dir)
    subject, _text, _html = digest(cfg, db)
    # HAM→HKT is google-low and a new low; FRA has no reason of its own, so it is no deal
    assert subject == "Vacation Planner · 1 new deal · cheapest Weihnachten: Phuket 9,000 €"


def test_digest_html_has_every_section(config_dir):
    cfg, db, _run = seeded(config_dir)
    _subject, _text, html = digest(cfg, db)
    # 1. header, run status and budget
    assert "4 searches · 3 ok · 1 failed" in html
    assert "providers: serpapi 2, searchapi 1" in html
    assert "API budget used this month: serpapi 3 / 250 · searchapi 1 / 100" in html
    # 2. new deals, in plain words
    assert "Phuket (HKT) from Hamburg · 19–29 Dec · Business" in html
    assert "9,000 € total · 3,000 € per person · SWISS + Bangkok Airways · 2 stops" in html
    assert "lowest price seen so far for this trip" in html
    assert "Google rates this fare as low for these dates" in html
    assert "Book on Google Flights" in html and 'href="https://g/1"' in html
    # 3. one table per searched slot
    assert "Weihnachtsferien 2026/27 · free 19 Dec – 3 Jan" in html
    assert "vs last week" in html and "Lowest seen" in html
    assert "▼ 25 %" in html          # 12,000 → 9,000
    assert "FRA 7,000 € (−22 %)" in html
    assert html.index("Phuket") < html.index("Malé")   # rows sorted by total
    # 4. what was not searched
    assert "Not searched this run" in html
    assert "Himmelfahrt/Pfingsten 2027" in html
    assert "Herbstferien 2026 — no targets configured" in html
    # 5. footer
    assert "https://x/report" in html
    assert "Prices are totals for 2 adults + 1 child." in html
    assert "layover rule is checked on the outbound legs only" in html
    assert "<script" not in html and "<img" not in html
    assert "TEST DATA" not in html   # real provider data carries no warning


def test_digest_text_has_the_same_content_without_tags(config_dir):
    cfg, db, _run = seeded(config_dir)
    _subject, text, _html = digest(cfg, db)
    for expected in ["4 searches · 3 ok · 1 failed",
                     "API budget used this month: serpapi 3 / 250 · searchapi 1 / 100",
                     "Phuket (HKT) from Hamburg · 19–29 Dec · Business",
                     "lowest price seen so far for this trip",
                     "Weihnachtsferien 2026/27 · free 19 Dec – 3 Jan",
                     "▼ 25 %", "Not searched this run",
                     "Full report: https://x/report",
                     "layover rule is checked on the outbound legs only"]:
        assert expected in text
    assert "<" not in text and "&amp;" not in text


def test_a_frankfurt_deal_is_explained_against_the_hamburg_fare(config_dir):
    cfg, db, _run = seeded(config_dir, fra_level="low")
    subject, text, html = digest(cfg, db)
    assert subject.startswith("Vacation Planner · 2 new deals · ")
    assert "Phuket (HKT) from Frankfurt · 19–29 Dec · Business" in html
    assert "cheaper than the best Hamburg fare by 22 %" in html   # 9,000 → 7,000
    assert "cheaper than the best Hamburg fare by 22 %" in text


def test_digest_without_data_still_renders(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    subject, text, html = compose_digest(build_report(db, cfg, NOW, None), [], cfg, None)
    assert subject == "Vacation Planner · weekly update"
    assert "No new deals this week." in html and "No new deals this week." in text
    assert "vs last week" not in html                      # no slot tables at all
    assert "Not searched this run" in html
    assert "Weihnachtsferien 2026/27 — not searched in this run" in html
    assert "Herbstferien 2026 — no targets configured" in html


def test_compose_is_an_alias_of_compose_digest():
    assert compose is compose_digest


def test_send_pending_sends_the_digest_and_marks_the_deals(config_dir):
    cfg, db, _run = seeded(config_dir)
    FakeSMTP.instances.clear()
    n = send_pending(db, cfg, NOW, "https://x/report", smtp_factory=FakeSMTP)
    assert n == 1 and db.pending_deals() == []
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.tls, smtp.logged_in) == ("smtp.test", 2525, True, ("u", "p"))
    msg = smtp.sent[0]
    assert msg["To"] == "me@test" and msg["From"] == "planner@test"
    assert "1 new deal" in msg["Subject"]
    body = msg.get_body("html").get_content()
    assert "Phuket (HKT) from Hamburg" in body and "https://x/report" in body


def test_send_pending_uses_the_execution_summary_when_given(config_dir):
    cfg, db, run = seeded(config_dir)
    FakeSMTP.instances.clear()
    summary = ExecutionSummary(run_id=run, planned=9, ok=7, errors=1, skipped=1, fallbacks=0, status="ok")
    send_pending(db, cfg, NOW, None, summary, smtp_factory=FakeSMTP)
    body = FakeSMTP.instances[0].sent[0].get_body("html").get_content()
    assert "9 searches · 7 ok · 1 failed · 1 skipped" in body


def test_send_failure_keeps_deals_pending(config_dir):
    cfg, db, _run = seeded(config_dir)
    assert send_pending(db, cfg, NOW, smtp_factory=FailingSMTP) == 0
    assert len(db.pending_deals()) == 1


def test_missing_smtp_config_is_noop(config_dir):
    cfg, db, _run = seeded(config_dir, env={})
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances == [] and len(db.pending_deals()) == 1


def set_mode(config_dir, mode):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"mode: \w+", f"mode: {mode}", st.read_text()))


def test_mode_digest_mails_even_without_deals(config_dir):
    set_mode(config_dir, "digest")
    cfg = load_config(config_dir, env=ENV)
    FakeSMTP.instances.clear()
    assert send_pending(Storage(":memory:"), cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances[0].sent[0]["Subject"] == "Vacation Planner · weekly update"


def test_legacy_mode_always_behaves_like_digest(config_dir):
    set_mode(config_dir, "always")
    cfg = load_config(config_dir, env=ENV)
    FakeSMTP.instances.clear()
    send_pending(Storage(":memory:"), cfg, NOW, smtp_factory=FakeSMTP)
    assert len(FakeSMTP.instances) == 1


def test_mode_deals_only_stays_quiet_without_deals(config_dir):
    set_mode(config_dir, "deals_only")
    cfg = load_config(config_dir, env=ENV)
    FakeSMTP.instances.clear()
    assert send_pending(Storage(":memory:"), cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances == []


def test_mode_deals_only_sends_the_digest_when_there_are_deals(config_dir):
    set_mode(config_dir, "deals_only")
    cfg, db, _run = seeded(config_dir)
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 1
    assert "Weihnachtsferien" in FakeSMTP.instances[0].sent[0].get_body("html").get_content()


def test_mode_never_sends_nothing(config_dir):
    set_mode(config_dir, "never")
    cfg, db, _run = seeded(config_dir)
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances == [] and len(db.pending_deals()) == 1


class FakeSMTPSSL(FakeSMTP):
    instances = []

    def __init__(self, host, port):
        super().__init__(host, port)
        FakeSMTPSSL.instances.append(self)

    def starttls(self):
        raise AssertionError("starttls() must not be called on an implicit-TLS (465) connection")


def test_port_465_uses_implicit_tls(config_dir):
    cfg, db, _run = seeded(config_dir, env=dict(ENV, SMTP_PORT="465"))
    FakeSMTP.instances.clear()
    FakeSMTPSSL.instances.clear()
    n = send_pending(db, cfg, NOW, smtp_factory=FakeSMTP, smtp_ssl_factory=FakeSMTPSSL)
    assert n == 1 and db.pending_deals() == []
    assert FakeSMTP.instances == [FakeSMTPSSL.instances[0]]   # only the SSL factory was used
    smtp = FakeSMTPSSL.instances[0]
    assert (smtp.host, smtp.port, smtp.tls, smtp.logged_in) == ("smtp.test", 465, False, ("u", "p"))
    assert smtp.sent


def test_port_587_still_starts_tls(config_dir):
    cfg, db, _run = seeded(config_dir)
    FakeSMTPSSL.instances.clear()
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP, smtp_ssl_factory=FakeSMTPSSL) == 1
    assert FakeSMTPSSL.instances == [] and FakeSMTP.instances[0].tls is True


def test_send_pending_logs_the_recipients_and_the_subject(config_dir, caplog):
    """Every real send leaves a line in the job log naming who got what."""
    cfg, db, _run = seeded(config_dir)
    FakeSMTP.instances.clear()
    with caplog.at_level(logging.INFO, logger="vacation_planner.notify"):
        assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 1
    assert "me@test" in caplog.text and "1 new deal" in caplog.text


def test_fake_provider_data_is_flagged_as_test_data(config_dir):
    cfg = load_config(config_dir, env=ENV)
    db = Storage(":memory:")
    run = db.start_run(NOW, 1)
    db.save_result(run, SearchResult(req(), Provider.FAKE, [offer(9000, provider=Provider.FAKE)]), NOW)
    db.finish_run(run, 1, "ok", NOW)
    _subject, text, html = digest(cfg, db)
    assert "TEST DATA — fake provider" in html and "TEST DATA — fake provider" in text
    assert "color:#b42318" in html          # red, so it cannot be mistaken for a real price
