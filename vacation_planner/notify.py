"""The weekly digest email.

One mail per run, whatever it found: what is new, what everything costs right now,
how that compares with last week and with the cheapest price ever seen, what the run
cost in API credits, and which slots were not searched. `compose_digest` builds the
subject and both bodies; `send_pending` sends them and marks the deals notified.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape
from typing import TYPE_CHECKING, Callable

from .calendar import Window
from .config import Config
from .explain import explain
from .models import Cabin, Destination, Provider, Slot
from .report import NewDeal, ReportData, RouteSummary, build_report, change, money, new_deal
from .storage import SearchRow, Storage

if TYPE_CHECKING:   # only for the annotation; the provider layer is heavy to import
    from .providers.executor import ExecutionSummary

log = logging.getLogger(__name__)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_FULL = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
#: Airport codes worth spelling out in a sentence; anything else stays a code.
CITIES = {"HAM": "Hamburg", "FRA": "Frankfurt", "BER": "Berlin", "HAJ": "Hannover",
          "CPH": "Copenhagen", "AMS": "Amsterdam", "MUC": "Munich", "DUS": "Düsseldorf"}

FOOTER_NOTE = ("Prices are totals for 2 adults + 1 child. The layover rule is checked on the "
               "outbound legs only — verify the return on Google Flights.")
#: Shown when anything in the digest came from the fake provider (`scan --fake`).
TEST_DATA_WARNING = "TEST DATA — fake provider"


# ---- small formatting helpers ----

def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def _dates(start: date, end: date) -> str:
    """'19–29 Dec' inside one month, '19 Dec – 3 Jan' across two."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{_day(start)} – {_day(end)}"


def _stops(n: int) -> str:
    return "nonstop" if n == 0 else f"{n} stop" if n == 1 else f"{n} stops"


def _flight(offer) -> str:
    return f"{' + '.join(offer.airlines) or 'airline unknown'} · {_stops(offer.stops)}"


def short_slot_name(slot: Slot) -> str:
    """'weihnachten-2026' -> 'Weihnachten'; for the subject line, where space is short."""
    return slot.id.split("-")[0].capitalize() or slot.name


def city(code: str) -> str:
    return CITIES.get(code, code)


def _destination(config: Config, code: str) -> Destination:
    return config.destinations.get(code) or Destination(code, code, Cabin.ANY)


def _route_for(data: ReportData, search: SearchRow) -> RouteSummary | None:
    for slot, _window, routes in data.slots:
        if slot.id != search.slot_id:
            continue
        for r in routes:
            if r.destination.code == search.destination and r.seat is search.seat:
                return r
    return None


def _uses_fake_data(data: ReportData, deals: list[NewDeal]) -> bool:
    """True when any price in this digest came from the fake provider, not a real search."""
    if Provider.FAKE in data.providers:
        return True
    offers = [d.offer for d in deals]
    for _slot, _window, routes in data.slots:
        for r in routes:
            offers.append(r.best.offer)
            offers += [obs.offer for _origin, obs, _saving in r.alternates]
    return any(o.provider is Provider.FAKE for o in offers)


# ---- the digest, as plain data both renderers walk ----

@dataclass
class _Deal:
    title: str
    price: str
    why: str
    url: str
    #: other date pairs on the same route that are deals too ("also 19–31 Dec 7,493 €")
    also: list[str] = field(default_factory=list)


@dataclass
class _Row:
    destination: str
    origin: str
    dates: str
    total: str
    per_person: str
    change: str
    change_class: str
    lowest: str
    #: "low · SWISS + Bangkok Airways · 2 stops", the second line under the destination
    details: str
    url: str
    alternates: str


@dataclass
class _Block:
    heading: str
    rows: list[_Row]


@dataclass
class _Digest:
    subject: str
    date: str
    status: str
    budget: str
    deals: list[_Deal]
    blocks: list[_Block]
    not_searched: list[str]
    report_url: str | None
    test_data: bool = False


def _status_line(data: ReportData, config: Config) -> str:
    if not data.counts:
        return "No searches recorded yet."
    total = sum(data.counts.values())
    ok, failed = data.counts.get("ok", 0), data.counts.get("error", 0)
    skipped = data.counts.get("skipped", 0)
    line = f"{total} searches · {ok} ok · {failed} failed"
    if skipped:
        line += f" · {skipped} skipped"
    order = [e.name for e in config.settings.providers.order]
    used = sorted(data.providers.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    if used:
        line += " · providers: " + ", ".join(f"{p.value} {n}" for p, n in used)
    return line


def _budget_line(data: ReportData) -> str:
    if not data.usage:
        return ""
    return "API budget used this month: " + " · ".join(
        f"{p.value} {used} / {budget}" for p, used, budget in data.usage)


def _deal_card(group: list[NewDeal], data: ReportData, config: Config) -> _Deal:
    """One card per route: the cheapest fare, with the other date pairs as "also" lines."""
    best, *rest = sorted(group, key=lambda n: n.offer.price_total)
    s, o = best.search, best.offer
    destination = _destination(config, s.destination)
    title = (f"{destination.name} ({s.destination}) from {city(s.origin)} · "
             f"{_dates(s.outbound_date, s.return_date)} · {s.seat.value.capitalize()}")
    price = f"{money(o.price_total)} total · {money(o.per_person)} per person · {_flight(o)}"
    why = explain(best.reasons, o, _route_for(data, s), destination, best.median)
    also = [f"also {_dates(n.search.outbound_date, n.search.return_date)} {money(n.offer.price_total)}"
            for n in rest]
    return _Deal(title, price, why, o.google_url, also)


def _grouped(deals: list[NewDeal]) -> list[list[NewDeal]]:
    """Deals bundled per (slot, origin, destination, seat), best-ranked route first."""
    groups: dict[tuple, list[NewDeal]] = {}
    for d in deals:
        key = (d.search.slot_id, d.search.origin, d.search.destination, d.search.seat)
        groups.setdefault(key, []).append(d)
    return list(groups.values())


def _alternate(origin: str, price: float, saving: float | None) -> str:
    """'FRA 9,000 € (−25 %)', as the report writes it; no percentage when it is a wash."""
    pct = round((saving or 0) * 100)
    if not pct:
        return f"{origin} {money(price)}"
    return f"{origin} {money(price)} ({'−' if pct > 0 else '+'}{abs(pct)} %)"


def _row(r: RouteSummary) -> _Row:
    price = r.best.offer.price_total
    label, cls = change(r.previous, price)
    alternates = " · ".join(_alternate(origin, obs.offer.price_total, saving)
                            for origin, obs, saving in r.alternates)
    level = r.best.offer.price_level
    return _Row(
        destination=f"{r.destination.name} ({r.destination.code})",
        origin=r.best.search.origin,
        dates=_dates(r.best.search.outbound_date, r.best.search.return_date),
        total=money(price),
        per_person=money(r.best.offer.per_person),
        change=label, change_class=cls,
        # "lowest ever" that is this week's price says nothing; the price itself is right there
        lowest=money(r.lowest_ever) if r.lowest_ever and r.lowest_ever != price else "–",
        details=f"{level} · {_flight(r.best.offer)}" if level else _flight(r.best.offer),
        url=r.best.offer.google_url,
        alternates=alternates)


def _block(slot: Slot, window: Window, routes: list[RouteSummary]) -> _Block:
    heading = f"{slot.name} · free {_dates(window.start, window.end)}"
    return _Block(heading, [_row(r) for r in routes])


def _subject(data: ReportData, deals: list[NewDeal]) -> str:
    n = len(deals)
    head = f"{n} new deal{'' if n == 1 else 's'}" if n else "weekly update"
    subject = f"Vacation Planner · {head}"
    for slot, _window, routes in data.slots:
        if routes:
            r = routes[0]   # build_report sorts a slot's rows by total
            return (f"{subject} · cheapest {short_slot_name(slot)}: "
                    f"{r.destination.name} {money(r.best.offer.price_total)}")
    return subject


#: How many destination codes a "searched but no result" line spells out before it counts.
MAX_CODES = 8


def _codes(codes: list[str]) -> str:
    if len(codes) <= MAX_CODES:
        return ", ".join(codes)
    return ", ".join(codes[:MAX_CODES]) + f" and {len(codes) - MAX_CODES} more"


def _not_searched(data: ReportData, config: Config) -> list[str]:
    """What this run did not cover, with the far future collapsed into one line."""
    horizon = data.generated_at.date() + timedelta(days=config.settings.deals.lookahead_days)
    lines: list[str] = []
    later: list[Slot] = []
    for slot, _window, _routes in data.slots:
        searched = data.searched.get(slot.id, set())
        if not slot.targets:
            if slot.start <= horizon:
                lines.append(f"{slot.name} — no targets configured")
            else:
                later.append(slot)
            continue
        missing = [t for t in slot.targets if t not in searched]
        if not missing:
            continue
        if not searched:
            if slot.start <= horizon:
                lines.append(f"{slot.name} — not searched in this run")
        else:
            lines.append(f"{slot.name} — searched but no result: {_codes(missing)}")
    if later:
        lines.append(f"{len(later)} later holidays have no targets configured "
                     f"({later[0].name} – {later[-1].name})")
    return lines


def _prepare(data: ReportData, deals: list[NewDeal], config: Config, report_url: str | None) -> _Digest:
    blocks = [_block(slot, window, routes) for slot, window, routes in data.slots if routes]
    # Best first, and among equals the one that costs the least per person -- the deal
    # ordering belongs to the digest, so every caller gets the same mail.
    ranked = sorted(deals, key=lambda n: (-n.deal.score, n.offer.per_person))
    d = data.generated_at
    return _Digest(
        subject=_subject(data, deals),
        date=f"{d.day} {MONTHS_FULL[d.month - 1]} {d.year}",
        status=_status_line(data, config),
        budget=_budget_line(data),
        deals=[_deal_card(group, data, config) for group in _grouped(ranked)],
        blocks=blocks, not_searched=_not_searched(data, config), report_url=report_url,
        test_data=_uses_fake_data(data, deals))


# ---- HTML ----

WRAP = ("max-width:640px;margin:0 auto;padding:20px 16px;background:#ffffff;color:#1a1a1a;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        "font-size:15px;line-height:1.45;")
H1 = "margin:0 0 2px;font-size:22px;font-weight:600;"
H2 = "margin:26px 0 8px;font-size:16px;font-weight:600;border-bottom:1px solid #e3e3e3;padding-bottom:4px;"
MUTED = "margin:0 0 2px;color:#666666;font-size:13px;"
CARD = ("border:1px solid #e3e3e3;border-left:3px solid #0a7d32;border-radius:4px;"
        "padding:10px 12px;margin:0 0 10px;")
TH = "text-align:left;padding:4px 6px;border-bottom:1px solid #e3e3e3;color:#666666;font-weight:600;"
TD = "padding:4px 6px;border-bottom:1px solid #f0f0f0;vertical-align:top;"
NUM = "text-align:right;white-space:nowrap;"
WARNING = "margin:0 0 2px;color:#b42318;font-weight:700;font-size:13px;"
COLOURS = {"down": "color:#0a7d32;", "up": "color:#b42318;", "": ""}


def _e(s: str) -> str:
    return escape(str(s))


def _link(url: str, label: str) -> str:
    return f'<a href="{escape(url, quote=True)}" style="color:#0a52a8;">{_e(label)}</a>'


#: The table has to fit 640 px, so detail lives on a second line inside its cell.
HTML_COLUMNS = (("Destination", False), ("From", False), ("Dates", False), ("Price", True),
                ("vs last week", True), ("Lowest seen", True), ("", False))
SUB = "color:#666666;font-size:12px;"


def _sub(text: str) -> str:
    return f'<br><span style="{SUB}">{_e(text)}</span>' if text else ""


def _html_rows(block: _Block) -> str:
    head = "".join(f'<th style="{TH}{NUM if right else ""}">{name}</th>' for name, right in HTML_COLUMNS)
    body = []
    for r in block.rows:
        body.append(
            f'<tr><td style="{TD}">{_e(r.destination)}{_sub(r.details)}</td>'
            f'<td style="{TD}">{_e(r.origin)}{_sub(r.alternates)}</td>'
            f'<td style="{TD}white-space:nowrap;">{_e(r.dates)}</td>'
            f'<td style="{TD}{NUM}">{_e(r.total)}{_sub(r.per_person + " p.p.")}</td>'
            f'<td style="{TD}{NUM}{COLOURS.get(r.change_class, "")}">{_e(r.change)}</td>'
            f'<td style="{TD}{NUM}">{_e(r.lowest)}</td>'
            f'<td style="{TD}">{_link(r.url, "Book")}</td></tr>')
    return (f'<table style="width:100%;border-collapse:collapse;font-size:13px;"><tr>{head}</tr>'
            + "".join(body) + "</table>")


#: Outlook and friends are happier with a whole document than with a bare fragment.
DOCUMENT_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<title>Vacation Planner</title></head>'
                 '<body style="margin:0;padding:0;background:#f4f4f5;">')


def _html(d: _Digest) -> str:
    parts = [DOCUMENT_HEAD, f'<div style="{WRAP}">',
             f'<h1 style="{H1}">Vacation Planner</h1>',
             f'<p style="{MUTED}">{_e(d.date)}</p>',
             f'<p style="{MUTED}">{_e(d.status)}</p>']
    if d.test_data:
        parts.append(f'<p style="{WARNING}">{_e(TEST_DATA_WARNING)}</p>')
    if d.budget:
        parts.append(f'<p style="{MUTED}">{_e(d.budget)}</p>')

    parts.append(f'<h2 style="{H2}">New deals</h2>')
    if d.deals:
        for deal in d.deals:
            parts.append(
                f'<div style="{CARD}"><div style="font-weight:600;">{_e(deal.title)}</div>'
                f'<div>{_e(deal.price)}</div>'
                + (f'<div style="color:#666666;font-size:13px;">{_e(deal.why)}</div>' if deal.why else "")
                + "".join(f'<div style="{SUB}">{_e(line)}</div>' for line in deal.also)
                + f'<div style="margin-top:4px;">{_link(deal.url, "Book on Google Flights")}</div></div>')
    else:
        parts.append(f'<p style="{MUTED}">No new deals this week.</p>')

    for block in d.blocks:
        parts.append(f'<h2 style="{H2}">{_e(block.heading)}</h2>')
        parts.append(_html_rows(block))

    if d.not_searched:
        parts.append(f'<h2 style="{H2}">Not searched this run</h2><ul style="margin:0;padding-left:18px;color:#666666;font-size:13px;">')
        parts += [f"<li>{_e(line)}</li>" for line in d.not_searched]
        parts.append("</ul>")

    parts.append('<hr style="border:none;border-top:1px solid #e3e3e3;margin:24px 0 10px;">')
    if d.report_url:
        parts.append(f'<p style="{MUTED}">Full report: {_link(d.report_url, d.report_url)}</p>')
    parts.append(f'<p style="{MUTED}">{_e(FOOTER_NOTE)}</p></div></body></html>')
    return "".join(parts)


# ---- plain text ----

TEXT_COLUMNS = (("destination", "Destination", False), ("origin", "From", False),
                ("dates", "Dates", False), ("total", "Total", True),
                ("per_person", "Per person", True), ("change", "vs last week", True),
                ("lowest", "Lowest seen", True))


def _text_row(values: list[tuple[str, int, bool]]) -> str:
    return "  ".join(v.rjust(w) if right else v.ljust(w) for v, w, right in values).rstrip()


def _text_table(block: _Block) -> list[str]:
    # Each block sizes its own columns: a narrow week should not inherit a wide one's padding.
    widths = [max([len(head)] + [len(getattr(r, f)) for r in block.rows])
              for f, head, _right in TEXT_COLUMNS]
    header = _text_row([(head, w, right) for (_f, head, right), w in zip(TEXT_COLUMNS, widths)])
    lines = [header, "-" * len(header)]
    for r in block.rows:
        lines.append(_text_row([(getattr(r, f), w, right)
                                for (f, _h, right), w in zip(TEXT_COLUMNS, widths)]))
        if r.details:
            lines.append(f"    {r.details}")
        if r.alternates:
            lines.append(f"    also {r.alternates}")
        lines.append(f"    book: {r.url}")
    return lines


def _heading(text: str) -> list[str]:
    return [text, "=" * len(text), ""]


def _text(d: _Digest) -> str:
    lines = ["Vacation Planner", d.date, d.status]
    if d.test_data:
        lines.append(f"*** {TEST_DATA_WARNING} ***")
    if d.budget:
        lines.append(d.budget)

    lines += [""] + _heading("New deals")
    if d.deals:
        for deal in d.deals:
            lines.append(deal.title)
            lines.append(f"  {deal.price}")
            if deal.why:
                lines.append(f"  {deal.why}")
            lines += [f"  {line}" for line in deal.also]
            lines.append(f"  book: {deal.url}")
            lines.append("")
    else:
        lines += ["No new deals this week.", ""]

    for block in d.blocks:
        lines += _heading(block.heading) + _text_table(block) + [""]

    if d.not_searched:
        lines += _heading("Not searched this run") + [f"  {line}" for line in d.not_searched] + [""]

    if d.report_url:
        lines.append(f"Full report: {d.report_url}")
    lines.append(FOOTER_NOTE)
    return "\n".join(lines)


@dataclass
class SendResult:
    """Whether a mail went out, and how many deals it reported."""

    sent: bool = False
    deals: int = 0


def compose_digest(data: ReportData, deals: list[NewDeal], config: Config,
                   report_url: str | None) -> tuple[str, str, str]:
    """(subject, text body, html body) for one weekly digest."""
    d = _prepare(data, deals, config, report_url)
    return d.subject, _text(d), _html(d)


#: The digest is the only format; `compose` stays as the historical name.
compose = compose_digest


def send_pending(storage: Storage, config: Config, now: datetime, report_url: str | None = None,
                 summary: ExecutionSummary | None = None,
                 smtp_factory: Callable[[str, int], smtplib.SMTP] | None = None,
                 smtp_ssl_factory: Callable[[str, int], smtplib.SMTP] | None = None) -> "SendResult":
    """Send the digest per `email.mode` and report whether it went out, with how many deals.

    `summary` is the just-finished run's `ExecutionSummary`; without it the run's
    numbers come from storage, which is what `notify` on its own has.
    """
    mode = config.settings.email.mode
    if mode == "never":
        return SendResult()
    pending = storage.pending_deals()
    if mode == "deals_only" and not pending:
        return SendResult()
    sec = config.secrets
    if not (sec.smtp_host and sec.mail_from and sec.mail_to):
        log.warning("email not configured (SMTP_HOST, MAIL_FROM, MAIL_TO); %d deals stay pending", len(pending))
        return SendResult()

    data = build_report(storage, config, now, storage.last_run_id())
    if summary is not None:
        # A skipped search leaves no row behind, so only the summary can report it;
        # storage counts what was written.
        data.counts = {"ok": summary.ok, "error": summary.errors, "skipped": summary.skipped}
    deals = [new_deal(storage, config, d) for d in pending]   # the digest ranks them
    subject, text, html = compose_digest(data, deals, config, report_url)

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sec.mail_from, ", ".join(sec.mail_to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    log.info("sending %r to %s via %s:%s", subject, ", ".join(sec.mail_to), sec.smtp_host, sec.smtp_port)
    implicit_tls = sec.smtp_port == 465   # 465 is TLS from the first byte; 587 upgrades with STARTTLS
    # Resolved here, not in the signature's defaults, so patching `smtplib` really does
    # cut the connection off (a default bound at import time would sail past the patch).
    factory = (smtp_ssl_factory or smtplib.SMTP_SSL) if implicit_tls else (smtp_factory or smtplib.SMTP)
    try:
        with factory(sec.smtp_host, sec.smtp_port) as smtp:
            if not implicit_tls:
                smtp.starttls()
            if sec.smtp_user:
                smtp.login(sec.smtp_user, sec.smtp_password or "")
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("email failed: %s", e)
        return SendResult()
    if deals:
        storage.mark_notified([d.deal.id for d in deals], now)
    return SendResult(sent=True, deals=len(deals))
