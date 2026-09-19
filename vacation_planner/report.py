from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median as _median

from jinja2 import Environment, PackageLoader, select_autoescape

from .calendar import Window, free_window
from .config import Config
from .models import Cabin, Destination, Provider, SeatClass, Slot
from .storage import DealRow, Observation, OfferRow, SearchRow, Storage


@dataclass
class RouteSummary:
    slot: Slot
    destination: Destination
    seat: SeatClass
    best: Observation
    median: float | None
    ratio: float | None
    is_deal: bool
    page: str


@dataclass
class NewDeal:
    deal: DealRow
    search: SearchRow
    offer: OfferRow

    @property
    def reasons(self):
        return self.deal.reasons


@dataclass
class ReportData:
    generated_at: datetime
    new_deals: list[NewDeal]
    slots: list[tuple[Slot, Window, list[RouteSummary]]]
    serpapi_used_this_month: int
    serpapi_budget: int


def route_page_name(slot_id: str, origin: str, destination: str, seat: SeatClass) -> str:
    return f"routes/{slot_id}-{origin}-{destination}-{seat.value}.html"


def money(v: float) -> str:
    return f"{v:,.0f} €"


def _route_median(storage: Storage, slot_id: str, origin: str, dest: str, seat: SeatClass,
                  min_points: int) -> tuple[list[Observation], float | None]:
    obs = storage.route_observations(slot_id, origin, dest, seat)
    prices = [o.offer.price_total for o in obs]
    return obs, (float(_median(prices)) if len(prices) >= min_points else None)


def build_report(storage: Storage, config: Config, now: datetime, last_run_id: int | None) -> ReportData:
    deal_offer_ids: set[int] = set()
    new_deals: list[NewDeal] = []
    if last_run_id is not None:
        for d in storage.deals_in_run(last_run_id):
            deal_offer_ids.add(d.offer_id)
            new_deals.append(NewDeal(d, storage.search_by_id(d.search_id), storage.offer_by_id(d.offer_id)))
        new_deals.sort(key=lambda n: -n.deal.score)

    bd = config.settings.bridge_days
    slots = []
    for slot in config.slots:
        if slot.end < now.date():
            continue
        window = free_window(slot, bd.before, bd.after)
        by_route: dict[tuple[str, str, SeatClass], list[Observation]] = {}
        for o in storage.latest_per_pair(slot.id):
            by_route.setdefault((o.search.origin, o.search.destination, o.search.seat), []).append(o)
        routes = []
        for (origin, dest, seat), obs in by_route.items():
            best = min(obs, key=lambda o: o.offer.price_total)
            _, med = _route_median(storage, slot.id, origin, dest, seat, config.settings.deals.min_history_points)
            destination = config.destinations.get(dest) or Destination(dest, dest, Cabin.ANY)  # removed from catalogue but still in history
            routes.append(RouteSummary(
                slot=slot, destination=destination, seat=seat, best=best, median=med,
                ratio=(best.offer.price_total / med) if med else None,
                is_deal=best.offer.id in deal_offer_ids,
                page=route_page_name(slot.id, origin, dest, seat)))
        routes.sort(key=lambda r: r.best.offer.price_total)
        slots.append((slot, window, routes))

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = storage.searches_by_provider_since(Provider.SERPAPI, month_start)
    return ReportData(now, new_deals, slots, used, config.settings.budget.serpapi_per_month)


def _env() -> Environment:
    env = Environment(loader=PackageLoader("vacation_planner", "templates"), autoescape=select_autoescape(["html"]))
    env.globals["money"] = money
    return env


def render(storage: Storage, config: Config, out_dir: Path, now: datetime, last_run_id: int | None = None) -> list[Path]:
    data = build_report(storage, config, now, last_run_id)
    env = _env()
    names = {s.id: s.name for s in config.slots}
    env.globals["slot_name"] = lambda sid: names.get(sid, sid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "routes").mkdir(exist_ok=True)
    written = []
    index = out_dir / "index.html"
    index.write_text(env.get_template("index.html").render(data=data))
    written.append(index)
    tpl = env.get_template("route.html")
    for slot, _window, routes in data.slots:
        for r in routes:
            obs, med = _route_median(storage, slot.id, r.best.search.origin, r.destination.code, r.seat,
                                     config.settings.deals.min_history_points)
            p = out_dir / r.page
            p.write_text(tpl.render(data=data, slot=slot, origin=r.best.search.origin, destination=r.destination,
                                    seat=r.seat, observations=obs, median=med))
            written.append(p)
    return written
