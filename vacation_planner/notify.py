from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from html import escape
from typing import Callable

from .config import Config
from .report import NewDeal, money
from .storage import Storage

log = logging.getLogger(__name__)


def compose(deals: list[NewDeal], config: Config, report_url: str | None) -> tuple[str, str, str]:
    names = {s.id: s.name for s in config.slots}
    if not deals:
        subject = "Vacation Planner: No new deals"
    else:
        subject = f"Vacation Planner: {len(deals)} new flight deal{'s' if len(deals) != 1 else ''}"

    lines, rows = [], []
    for d in deals:
        s, o = d.search, d.offer
        why = ", ".join(r.value.replace("_", " ") for r in d.reasons)
        lines.append(
            f"- {names.get(s.slot_id, s.slot_id)}: {s.origin} → {s.destination} ({s.seat.value}), "
            f"{s.outbound_date} – {s.return_date}: {money(o.price_total)} total, {money(o.per_person)} p.p., "
            f"{', '.join(o.airlines)}, {o.stops} stop(s). {why}. {o.google_url}")
        rows.append(
            f"<tr><td>{escape(names.get(s.slot_id, s.slot_id))}</td><td>{s.origin} → {s.destination} ({s.seat.value})</td>"
            f"<td>{s.outbound_date} – {s.return_date}</td><td align='right'>{money(o.price_total)}</td>"
            f"<td align='right'>{money(o.per_person)}</td><td>{escape(', '.join(o.airlines))}, {o.stops} stop(s)</td>"
            f"<td>{escape(why)}</td><td><a href=\"{escape(o.google_url, quote=True)}\">Google Flights</a></td></tr>")

    text = "\n".join(lines) if lines else "No new deals this run."
    if report_url:
        text += f"\n\nFull report: {report_url}"
    html = "<p>No new deals this run.</p>" if not rows else (
        "<table cellpadding='6' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
        "<tr><th>Slot</th><th>Route</th><th>Dates</th><th>Total</th><th>Per person</th><th>Flight</th><th>Why</th><th></th></tr>"
        + "".join(rows) + "</table>")
    if report_url:
        html += f"<p><a href=\"{escape(report_url, quote=True)}\">Full report</a></p>"
    return subject, text, html


def send_pending(storage: Storage, config: Config, now: datetime, report_url: str | None = None,
                 smtp_factory: Callable[[str, int], smtplib.SMTP] = smtplib.SMTP,
                 smtp_ssl_factory: Callable[[str, int], smtplib.SMTP] = smtplib.SMTP_SSL) -> int:
    mode = config.settings.email.mode
    if mode == "never":
        return 0
    pending = storage.pending_deals()
    if mode == "deals_only" and not pending:
        return 0
    sec = config.secrets
    if not (sec.smtp_host and sec.mail_from and sec.mail_to):
        log.warning("email not configured (SMTP_HOST, MAIL_FROM, MAIL_TO); %d deals stay pending", len(pending))
        return 0

    deals = [NewDeal(d, storage.search_by_id(d.search_id), storage.offer_by_id(d.offer_id)) for d in pending]
    deals.sort(key=lambda n: -n.deal.score)
    subject, text, html = compose(deals, config, report_url)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sec.mail_from, ", ".join(sec.mail_to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    implicit_tls = sec.smtp_port == 465   # 465 is TLS from the first byte; 587 upgrades with STARTTLS
    factory = smtp_ssl_factory if implicit_tls else smtp_factory
    try:
        with factory(sec.smtp_host, sec.smtp_port) as smtp:
            if not implicit_tls:
                smtp.starttls()
            if sec.smtp_user:
                smtp.login(sec.smtp_user, sec.smtp_password or "")
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("email failed: %s", e)
        return 0
    storage.mark_notified([d.deal.id for d in deals], now)
    return len(deals)
