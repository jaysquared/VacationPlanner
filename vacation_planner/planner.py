from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .calendar import Window, free_window, pax_for
from .config import Config
from .models import Nights, PlannedSearch, Provider, SearchRequest, Slot, seat_for
from .storage import Storage


def candidate_pairs(window: Window, nights: Nights) -> list[tuple[date, date]]:
    pairs = []
    out = window.start
    while out <= window.end:
        for n in range(nights.min, nights.max + 1):
            ret = out + timedelta(days=n)
            if ret > window.end:
                break
            pairs.append((out, ret))
        out += timedelta(days=1)
    return pairs


def provider_shares(config: Config, storage: Storage, today: date) -> list[tuple[Provider, int | None]]:
    """Per-run search allowance per provider, in configured order. None means unlimited.

    The cron fires on Mondays, so a month can have five runs: the per-run share alone
    (monthly_budget / runs_per_month) would overshoot a free tier. Cap it by what this
    calendar month has actually spent (ok and error rows both cost a credit).
    """
    runs = config.settings.budget.runs_per_month
    month_start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    shares: list[tuple[Provider, int | None]] = []
    for e in config.settings.providers.order:
        if e.monthly_budget is None:
            shares.append((e.name, None))
            continue
        used = storage.searches_by_provider_since(e.name, month_start)
        shares.append((e.name, max(0, min(max(1, e.monthly_budget // runs), e.monthly_budget - used))))
    return shares


def _slot_nights(slot: Slot, cfg: Config) -> Nights:
    return slot.nights or Nights(cfg.settings.nights.min, cfg.settings.nights.max)


def _route_requests(cfg: Config, storage: Storage, slot: Slot, origin: str, dest_code: str, today: date) -> list[SearchRequest]:
    dest = cfg.destination(dest_code)
    bd = cfg.settings.bridge_days
    window = free_window(slot, bd.before, bd.after)
    pairs = [(out, ret) for out, ret in candidate_pairs(window, _slot_nights(slot, cfg)) if out > today]
    reqs = []
    for out, ret in pairs:
        adults, children, _infants = pax_for(cfg.travellers, out)
        reqs.append(SearchRequest(slot.id, origin, dest_code, out, ret, seat_for(dest.cabin), adults, children))
    never = datetime.min.replace(tzinfo=timezone.utc)
    reqs.sort(key=lambda r: (storage.last_observed(r) or never, r.outbound_date, r.return_date))
    return reqs[: cfg.settings.budget.max_pairs_per_route_per_run]


def plan(config: Config, storage: Storage, today: date) -> list[PlannedSearch]:
    s = config.settings
    horizon = today + timedelta(days=s.deals.lookahead_days)
    requests: list[SearchRequest] = []
    for slot in config.slots:
        if not slot.targets or slot.start <= today or slot.start > horizon:
            continue
        for origin in s.origins:
            for dest_code in slot.targets:
                requests.extend(_route_requests(config, storage, slot, origin, dest_code, today))

    shares = provider_shares(config, storage, today)
    out: list[PlannedSearch] = []
    for req in requests[: s.budget.max_searches_per_run]:
        for i, (provider, left) in enumerate(shares):
            if left is None:
                out.append(PlannedSearch(req, provider))
                break
            if left > 0:
                shares[i] = (provider, left - 1)
                out.append(PlannedSearch(req, provider))
                break
        else:
            break   # every provider is out of budget: the plan ends here
    return out
