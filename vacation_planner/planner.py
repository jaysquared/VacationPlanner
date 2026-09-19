from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .calendar import Window, free_window, pax_for
from .config import BudgetSettings, Config
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


def serpapi_share(budget: BudgetSettings) -> int:
    return max(1, budget.serpapi_per_month // budget.runs_per_month)


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

    # The cron fires on Mondays, so a month can have five runs: the per-run share alone
    # (serpapi_per_month / runs_per_month) would overshoot the free tier. Cap by what this
    # calendar month has actually spent.
    month_start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    used = storage.searches_by_provider_since(Provider.SERPAPI, month_start)
    primary_n = max(0, min(serpapi_share(s.budget), s.budget.serpapi_per_month - used))
    backup = s.providers.backup
    limit = s.budget.max_searches_per_run if backup else primary_n
    out = []
    for i, req in enumerate(requests[:limit]):
        provider = s.providers.primary if i < primary_n else backup
        out.append(PlannedSearch(req, provider))
    return out
