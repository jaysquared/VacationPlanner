from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from ..config import ProviderSettings
from ..models import PlannedSearch, Provider, SearchResult
from ..storage import Storage
from .base import FlightClient, ProviderError, QuotaExhausted


@dataclass
class ExecutionSummary:
    run_id: int
    planned: int
    ok: int = 0
    errors: int = 0
    skipped: int = 0
    fallbacks: int = 0
    status: str = "running"


def execute(planned: list[PlannedSearch], clients: Mapping[Provider, FlightClient], storage: Storage,
            providers: ProviderSettings, now: Callable[[], datetime], raw_dir: Path | None = None) -> ExecutionSummary:
    run_id = storage.start_run(now(), len(planned))
    summary = ExecutionSummary(run_id=run_id, planned=len(planned))
    order = [e.name for e in providers.order]
    dead: set[Provider] = set()   # quota or auth failure: unusable for the rest of this run

    for ps in planned:
        req = ps.request
        # The planned provider first, then everything below it in the configured order:
        # falling back *up* the list would spend a scarcer budget than the plan allowed.
        chain = order[order.index(ps.provider):] if ps.provider in order else [ps.provider]
        attempted = False
        result: SearchResult | None = None

        for provider in chain:
            if provider in dead or provider not in clients:
                continue
            attempted = True
            try:
                result = clients[provider].search(req, raw_dir)
            except QuotaExhausted as e:
                dead.add(provider)
                storage.record_search(run_id, req, provider, "error", now(), error=str(e))
                continue
            except ProviderError as e:
                storage.record_search(run_id, req, provider, "error", now(), error=str(e))
                continue
            except Exception as e:
                # A client bug (layout change, bad cast) must never take the whole run down:
                # record this attempt as an error and try the next provider. Spec section 7.
                storage.record_search(run_id, req, provider, "error", now(),
                                      error=f"unexpected {type(e).__name__}: {e}")
                continue
            # Every attempt above is stored on its own, so the monthly counters stay accurate.
            storage.save_result(run_id, result, now())
            summary.ok += 1
            if provider is not ps.provider:
                summary.fallbacks += 1
            break

        if result is not None:
            continue
        if not attempted:
            storage.record_search(run_id, req, ps.provider, "skipped", now(), error="no provider available")
            summary.skipped += 1
        else:
            summary.errors += 1   # the error rows are already stored, one per attempt

    if not planned:
        summary.status = "empty"
    elif summary.ok == len(planned):
        summary.status = "ok"
    else:
        summary.status = "partial"
    storage.finish_run(run_id, summary.ok, summary.status, now())
    return summary
