from datetime import date, datetime, timezone

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.notify import compose, send_pending
from vacation_planner.report import NewDeal
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


def with_deal(config_dir, env=ENV):
    cfg = load_config(config_dir, env=env)
    db = Storage(":memory:")
    run = db.start_run(NOW, 1)
    req = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)
    db.save_result(run, SearchResult(req, Provider.SERPAPI, [Offer(Provider.SERPAPI, 6000, "EUR", 2000, ["LH"], 1, 800, "", "", "low", None, None, "https://g/1", {})]), NOW)
    detect_for_run(db, run, cfg, NOW)
    return cfg, db


def test_compose_mentions_route_price_and_link(config_dir):
    cfg, db = with_deal(config_dir)
    d = db.pending_deals()[0]
    subject, text, html = compose([NewDeal(d, db.search_by_id(d.search_id), db.offer_by_id(d.offer_id))], cfg, "https://x/report")
    assert "1 new flight deal" in subject
    assert "HAM → BKK" in text and "6,000 €" in text and "https://g/1" in text and "https://x/report" in text
    assert "Herbstferien 2026" in html and "<a href=\"https://g/1\"" in html


def test_send_pending_sends_and_marks(config_dir):
    cfg, db = with_deal(config_dir)
    FakeSMTP.instances.clear()
    n = send_pending(db, cfg, NOW, smtp_factory=FakeSMTP)
    assert n == 1 and db.pending_deals() == []
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.tls, smtp.logged_in) == ("smtp.test", 2525, True, ("u", "p"))
    msg = smtp.sent[0]
    assert msg["To"] == "me@test" and msg["From"] == "planner@test"


def test_send_failure_keeps_deals_pending(config_dir):
    cfg, db = with_deal(config_dir)
    assert send_pending(db, cfg, NOW, smtp_factory=FailingSMTP) == 0
    assert len(db.pending_deals()) == 1


def test_missing_smtp_config_is_noop(config_dir):
    cfg, db = with_deal(config_dir, env={})
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances == [] and len(db.pending_deals()) == 1


def test_mode_never_and_always(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("mode: deals_only", "mode: never"))
    cfg, db = with_deal(config_dir)
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0 and FakeSMTP.instances == []

    st.write_text(st.read_text().replace("mode: never", "mode: always"))
    cfg = load_config(config_dir, env=ENV)
    assert send_pending(Storage(":memory:"), cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert "No new deals" in FakeSMTP.instances[0].sent[0]["Subject"]


class FakeSMTPSSL(FakeSMTP):
    instances = []

    def __init__(self, host, port):
        super().__init__(host, port)
        FakeSMTPSSL.instances.append(self)

    def starttls(self):
        raise AssertionError("starttls() must not be called on an implicit-TLS (465) connection")


def test_port_465_uses_implicit_tls(config_dir):
    cfg, db = with_deal(config_dir, env=dict(ENV, SMTP_PORT="465"))
    FakeSMTP.instances.clear()
    FakeSMTPSSL.instances.clear()
    n = send_pending(db, cfg, NOW, smtp_factory=FakeSMTP, smtp_ssl_factory=FakeSMTPSSL)
    assert n == 1 and db.pending_deals() == []
    assert FakeSMTP.instances == [FakeSMTPSSL.instances[0]]   # only the SSL factory was used
    smtp = FakeSMTPSSL.instances[0]
    assert (smtp.host, smtp.port, smtp.tls, smtp.logged_in) == ("smtp.test", 465, False, ("u", "p"))
    assert smtp.sent


def test_port_587_still_starts_tls(config_dir):
    cfg, db = with_deal(config_dir)
    FakeSMTPSSL.instances.clear()
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP, smtp_ssl_factory=FakeSMTPSSL) == 1
    assert FakeSMTPSSL.instances == [] and FakeSMTP.instances[0].tls is True
