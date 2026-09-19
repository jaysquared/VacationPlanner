from __future__ import annotations

from dataclasses import dataclass, field
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
    """One (destination, seat) row: the home origin, plus what the alternates cost."""

    slot: Slot
    destination: Destination
    seat: SeatClass
    best: Observation
    median: float | None
    ratio: float | None
    is_deal: bool
    page: str
    #: (origin, its best observation, saving against `best` as a ratio) per alternate origin
    alternates: list[tuple[str, Observation, float | None]] = field(default_factory=list)


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
    usage: list[tuple[Provider, int, int]]   # provider, searches this month, monthly budget


def route_page_name(slot_id: str, origin: str, destination: str, seat: SeatClass) -> str:
    return f"routes/{slot_id}-{origin}-{destination}-{seat.value}.html"


def money(v: float) -> str:
    return f"{v:,.0f} €"


def _saving(reference: float, price: float) -> float | None:
    """How much cheaper `price` is than `reference`, as a ratio (0.25 = 25 % cheaper)."""
    return (reference - price) / reference if reference else None


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
    home = config.settings.alternate_origins.home
    slots = []
    for slot in config.slots:
        if slot.end < now.date():
            continue
        window = free_window(slot, bd.before, bd.after)
        by_route: dict[tuple[str, SeatClass], dict[str, list[Observation]]] = {}
        for o in storage.latest_per_pair(slot.id):
            by_route.setdefault((o.search.destination, o.search.seat), {}) \
                    .setdefault(o.search.origin, []).append(o)
        routes = []
        for (dest, seat), by_origin in by_route.items():
            # One row per (destination, seat): the home airport, with the alternate
            # origins beside it so their saving is visible without a second row.
            cheapest = {origin: min(obs, key=lambda o: o.offer.price_total) for origin, obs in by_origin.items()}
            best = cheapest.get(home) or min(cheapest.values(), key=lambda o: o.offer.price_total)
            alternates = [(origin, obs, _saving(best.offer.price_total, obs.offer.price_total))
                          for origin, obs in sorted(cheapest.items(), key=lambda kv: kv[1].offer.price_total)
                          if origin != best.search.origin]
            _, med = _route_median(storage, slot.id, best.search.origin, dest, seat,
                                   config.settings.deals.min_history_points)
            destination = config.destinations.get(dest) or Destination(dest, dest, Cabin.ANY)  # removed from catalogue but still in history
            routes.append(RouteSummary(
                slot=slot, destination=destination, seat=seat, best=best, median=med,
                ratio=(best.offer.price_total / med) if med else None,
                is_deal=any(o.offer.id in deal_offer_ids for o in cheapest.values()),
                page=route_page_name(slot.id, best.search.origin, dest, seat),
                alternates=alternates))
        routes.sort(key=lambda r: r.best.offer.price_total)
        slots.append((slot, window, routes))

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = [(e.name, storage.searches_by_provider_since(e.name, month_start), e.monthly_budget)
             for e in config.settings.providers.budgeted()]
    return ReportData(now, new_deals, slots, usage)


def _env() -> Environment:
    env = Environment(loader=PackageLoader("vacation_planner", "templates"), autoescape=select_autoescape(["html"]))
    env.globals["money"] = money
    return env


def render(storage: Storage, config: Config, out_dir: Path, now: datetime, last_run_id: int | None = None) -> list[Path]:
    data = build_report(storage, config, now, last_run_id)
    env = _env()
    names = {s.id: s.name for s in config.slots}
    env.globals["slot_name"] = lambda sid: names.get(sid, sid)
    env.globals["route_page_name"] = route_page_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "routes").mkdir(exist_ok=True)
    written = []
    index = out_dir / "index.html"
    index.write_text(env.get_template("index.html").render(data=data))
    written.append(index)
    tpl = env.get_template("route.html")
    for slot, _window, routes in data.slots:
        for r in routes:
            # One page per origin: the row's home origin and each alternate.
            for origin, page in [(r.best.search.origin, r.page)] + [
                    (o, route_page_name(slot.id, o, r.destination.code, r.seat)) for o, _obs, _s in r.alternates]:
                obs, med = _route_median(storage, slot.id, origin, r.destination.code, r.seat,
                                         config.settings.deals.min_history_points)
                p = out_dir / page
                p.write_text(tpl.render(data=data, slot=slot, origin=origin, destination=r.destination,
                                        seat=r.seat, observations=obs, median=med))
                written.append(p)
    return written
