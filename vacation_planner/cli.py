from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .calendar import free_window
from .config import Config, ConfigError, load_config
from .deals import DetectedDeal, detect_for_run
from .models import Provider
from .planner import plan as make_plan
from .providers.base import FlightClient
from .providers.executor import ExecutionSummary, execute
from .providers.fake import FakeFlightClient
from .report import render
from .storage import Storage
from . import notify as _notify

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("vacation_planner")


@dataclass
class Ctx:
    config: Config
    db_path: Path
    today: date
    now: datetime


@app.callback()
def main(ctx: typer.Context,
         config_dir: Path = typer.Option(Path("config"), "--config-dir"),
         db: Path = typer.Option(Path("data/planner.sqlite"), "--db"),
         today: Optional[str] = typer.Option(None, "--today", help="Override today's date (YYYY-MM-DD)"),
         verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        cfg = load_config(config_dir)
    except ConfigError as e:
        raise typer.BadParameter(str(e))
    now = datetime.now(timezone.utc)
    t = date.fromisoformat(today) if today else now.date()
    if today:
        now = datetime.combine(t, now.time(), tzinfo=timezone.utc)
    ctx.obj = Ctx(cfg, db, t, now)


def _storage(c: Ctx) -> Storage:
    c.db_path.parent.mkdir(parents=True, exist_ok=True)
    return Storage(c.db_path)


def build_clients(config: Config, fake: bool) -> dict[Provider, FlightClient]:
    ps = config.settings.providers
    wanted = [ps.primary] + ([ps.backup] if ps.backup else [])
    if fake:
        clients = {}
        for p in wanted:
            c = FakeFlightClient()
            c.provider = p
            clients[p] = c
        return clients
    clients: dict[Provider, FlightClient] = {}
    for p in wanted:
        if p is Provider.SERPAPI:
            if not config.secrets.serpapi_key:
                raise typer.BadParameter("SERPAPI_KEY is not set (put it in .env or the environment)")
            from .providers.serpapi import SerpApiClient
            clients[p] = SerpApiClient(config.secrets.serpapi_key, config.settings)
        elif p is Provider.FAST_FLIGHTS:
            from .providers.fast_flights import FastFlightsClient
            clients[p] = FastFlightsClient(config.settings)
        else:
            raise typer.BadParameter(f"unsupported provider {p.value}")
    return clients


def do_scan(config: Config, storage: Storage, today: date, now: datetime, limit: int | None,
            fake: bool, raw_dir: Path | None) -> tuple[ExecutionSummary, list[DetectedDeal]]:
    planned = make_plan(config, storage, today)
    if limit is not None:
        planned = planned[:limit]
    clients = build_clients(config, fake)
    summary = execute(planned, clients, storage, config.settings.providers, lambda: now, raw_dir)
    deals = detect_for_run(storage, summary.run_id, config, now)
    return summary, deals


def _print_summary(summary: ExecutionSummary, deals: list[DetectedDeal]) -> None:
    typer.echo(f"run {summary.run_id}: searches: {summary.ok} ok, {summary.errors} error, {summary.skipped} skipped, "
               f"{summary.fallbacks} fallback ({summary.status}); deals: {len(deals)} "
               f"({sum(d.notifiable for d in deals)} notifiable)")
    for d in deals:
        typer.echo(f"  {d.search.slot_id} {d.search.origin}->{d.search.destination} {d.search.outbound_date}..{d.search.return_date} "
                   f"{d.offer.price_total:,.0f} EUR [{', '.join(r.value for r in d.reasons)}]")


@app.command()
def holidays(ctx: typer.Context):
    """List slots with their school-free windows and targets."""
    c: Ctx = ctx.obj
    bd = c.config.settings.bridge_days
    for s in c.config.slots:
        w = free_window(s, bd.before, bd.after)
        flag = "past" if s.end < c.today else ""
        typer.echo(f"{s.id:18} {s.name:30} {s.start}..{s.end}  free {w.start}..{w.end} ({w.days}d)  targets: {', '.join(s.targets) or '-'} {flag}")


@app.command("plan")
def plan_cmd(ctx: typer.Context):
    """Show what the next run would search. No API calls."""
    c: Ctx = ctx.obj
    storage = _storage(c)
    planned = make_plan(c.config, storage, c.today)
    for p in planned:
        r = p.request
        typer.echo(f"{p.provider.value:12} {r.slot_id:18} {r.origin}->{r.destination} {r.outbound_date}..{r.return_date} ({r.nights}n) {r.seat.value} {r.adults}A{r.children}C")
    by = {}
    for p in planned:
        by[p.provider.value] = by.get(p.provider.value, 0) + 1
    typer.echo(f"{len(planned)} searches: " + ", ".join(f"{k}={v}" for k, v in by.items()))


@app.command()
def scan(ctx: typer.Context, limit: Optional[int] = typer.Option(None, "--limit"),
         fake: bool = typer.Option(False, "--fake", help="Use the fake provider (no network)")):
    """Execute the planned searches and detect deals."""
    c: Ctx = ctx.obj
    storage = _storage(c)
    summary, deals = do_scan(c.config, storage, c.today, c.now, limit, fake, c.config.root / "data" / "raw")
    _print_summary(summary, deals)


@app.command("report")
def report_cmd(ctx: typer.Context):
    """Render the HTML report from stored data."""
    c: Ctx = ctx.obj
    storage = _storage(c)
    out = Path(c.config.settings.report.output_dir)
    if not out.is_absolute():
        out = c.config.root / out
    written = render(storage, c.config, out, c.now, last_run_id=storage.last_run_id())
    typer.echo(f"wrote {len(written)} files to {out}")


@app.command("notify")
def notify_cmd(ctx: typer.Context, report_url: Optional[str] = typer.Option(None, "--report-url")):
    """Email pending deals."""
    c: Ctx = ctx.obj
    n = _notify.send_pending(_storage(c), c.config, c.now, report_url)
    typer.echo(f"notified {n} deals")


@app.command()
def run(ctx: typer.Context, limit: Optional[int] = typer.Option(None, "--limit"),
        fake: bool = typer.Option(False, "--fake"), report_url: Optional[str] = typer.Option(None, "--report-url")):
    """scan + report + notify (what CI runs)."""
    c: Ctx = ctx.obj
    storage = _storage(c)
    summary, deals = do_scan(c.config, storage, c.today, c.now, limit, fake, c.config.root / "data" / "raw")
    _print_summary(summary, deals)
    out = Path(c.config.settings.report.output_dir)
    if not out.is_absolute():
        out = c.config.root / out
    written = render(storage, c.config, out, c.now, last_run_id=summary.run_id)
    typer.echo(f"wrote {len(written)} files to {out}")
    n = _notify.send_pending(storage, c.config, c.now, report_url)
    typer.echo(f"notified {n} deals")
