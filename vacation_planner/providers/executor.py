from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from ..config import ProviderSettings
from ..models import PlannedSearch, Provider, SearchRequest, SearchResult
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
    primary, backup = providers.primary, providers.backup
    primary_dead = False

    def attempt(provider: Provider, req: SearchRequest) -> SearchResult:
        return clients[provider].search(req, raw_dir)

    for ps in planned:
        req = ps.request
        provider = ps.provider
        if provider == primary and primary_dead:
            if backup is None:
                storage.record_search(run_id, req, primary, "skipped", now(), error="primary quota exhausted")
                summary.skipped += 1
                continue
            provider = backup
            summary.fallbacks += 1

        error: str | None = None
        try:
            storage.save_result(run_id, attempt(provider, req), now())
            summary.ok += 1
            continue
        except QuotaExhausted as e:
            error = str(e)
            if provider == primary:
                primary_dead = True
        except ProviderError as e:
            error = str(e)
        except Exception as e:
            # A client bug (layout change, bad cast) must never take the whole run down:
            # record this search as an error and keep going. Spec section 7.
            storage.record_search(run_id, req, provider, "error", now(),
                                  error=f"unexpected {type(e).__name__}: {e}")
            summary.errors += 1
            continue

        if provider == primary and backup is not None:
            summary.fallbacks += 1
            try:
                storage.save_result(run_id, attempt(backup, req), now())
                summary.ok += 1
                continue
            except ProviderError as e:
                error = f"{error}; backup: {e}"
            provider = backup

        storage.record_search(run_id, req, provider, "error", now(), error=error)
        summary.errors += 1

    if not planned:
        summary.status = "empty"
    elif summary.ok == len(planned):
        summary.status = "ok"
    else:
        summary.status = "partial"
    storage.finish_run(run_id, summary.ok, summary.status, now())
    return summary
