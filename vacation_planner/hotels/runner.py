"""Run every active hotel watch: query each source, store the batch, judge it, store alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from ..config import Config, guests_on
from ..models import HotelSource, HotelStay, HotelWatch, PointsRule
from ..providers.base import ProviderError
from ..storage import Storage
from .clients import ApifyActor, BookingClient, GoogleHotelsClient, HiltonClient, HotelClient
from .rules import Verdict, evaluate, judge

log = logging.getLogger(__name__)

#: Which env var a missing client needs, for the "skipped" note.
NEEDS = {HotelSource.BOOKING: "APIFY_TOKEN", HotelSource.HILTON: "APIFY_TOKEN", HotelSource.GOOGLE: "SERPAPI_KEY"}


@dataclass
class HotelSummary:
    batch: str
    ok: int = 0
    no_rates: int = 0
    errors: int = 0
    skipped: int = 0
    verdicts: list[Verdict] = field(default_factory=list)
    alerts: int = 0


def _stays(watch: HotelWatch, config: Config) -> list[HotelStay]:
    adults, ages = guests_on(config.travellers, watch.check_in)
    if watch.adults_only:
        ages = ()

    def stay(source: HotelSource, mode: str, a: int = adults, ch: tuple[int, ...] = ages) -> HotelStay:
        return HotelStay(watch.id, source, watch.sources[source], mode, watch.check_in, watch.check_out, a, ch)

    out: list[HotelStay] = []
    if HotelSource.BOOKING in watch.sources:
        out.append(stay(HotelSource.BOOKING, "rates"))
    if HotelSource.HILTON in watch.sources:
        out += [stay(HotelSource.HILTON, "rates"), stay(HotelSource.HILTON, "parity")]
        if isinstance(watch.rule, PointsRule):
            out.append(stay(HotelSource.HILTON, "award", watch.rule.points_adults, ()))
    if HotelSource.GOOGLE in watch.sources:
        out.append(stay(HotelSource.GOOGLE, "rooms"))
    return out


def planned_stays(config: Config, today: date) -> list[HotelStay]:
    return [s for w in config.hotels if today <= w.until for s in _stays(w, config)]


def build_hotel_clients(config: Config) -> dict[HotelSource, HotelClient]:
    clients: dict[HotelSource, HotelClient] = {}
    if config.secrets.apify_token:
        actor = ApifyActor(config.secrets.apify_token)
        clients[HotelSource.BOOKING] = BookingClient(actor)
        clients[HotelSource.HILTON] = HiltonClient(actor)
    if config.secrets.serpapi_key:
        clients[HotelSource.GOOGLE] = GoogleHotelsClient(config.secrets.serpapi_key)
    return clients


def _maybe_alert(storage: Storage, config: Config, batch: str, v: Verdict, now: datetime) -> bool:
    if v.state != "alert" or v.reason is None:
        return False
    last = storage.last_hotel_alert(v.watch_id, v.reason)
    if last is not None:
        if v.value is None or last.value is None:
            return False   # "no standard reward" says the same thing every week
        if v.value > last.value * config.settings.deals.renotify_drop_ratio:
            return False
    storage.insert_hotel_alert(v.watch_id, batch, v.reason, v.value, v.threshold, v.headline, now)
    return True


def check_hotels(config: Config, storage: Storage, clients: Mapping[HotelSource, HotelClient],
                 today: date, now: datetime) -> HotelSummary:
    batch = now.isoformat()
    summary = HotelSummary(batch)
    for watch in config.hotels:
        if today > watch.until:
            summary.verdicts.append(evaluate(watch, [], config.point_value_eur, today))
            continue
        for stay in _stays(watch, config):
            client = clients.get(stay.source)
            if client is None:
                storage.record_hotel_check(batch, stay, "skipped", now, error=f"{NEEDS[stay.source]} not set")
                summary.skipped += 1
                continue
            try:
                result = client.check(stay)
            except ProviderError as e:
                storage.record_hotel_check(batch, stay, "error", now, error=str(e))
                summary.errors += 1
                continue
            except Exception as e:   # a parser bug must not take the run down
                log.exception("hotel check %s %s %s failed", stay.watch_id, stay.source.value, stay.mode)
                storage.record_hotel_check(batch, stay, "error", now, error=f"unexpected {type(e).__name__}: {e}")
                summary.errors += 1
                continue
            storage.save_hotel_result(batch, result, now)
            if result.status == "ok":
                summary.ok += 1
            else:
                summary.no_rates += 1
        rates = [r.rate for r in storage.hotel_rates(watch.id, batch)]
        statuses = [c.status for c in storage.hotel_checks(watch.id, batch)]
        verdict = judge(watch, rates, statuses, config.point_value_eur, today)
        summary.verdicts.append(verdict)
        summary.alerts += _maybe_alert(storage, config, batch, verdict, now)
    return summary
