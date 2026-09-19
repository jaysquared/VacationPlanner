# Vacation Planner

Weekly flight deal scanner for Hamburg school holidays. See
`docs/superpowers/specs/2026-09-19-vacation-planner-design.md`.

## Setup

    uv sync
    cp .env.example .env   # fill in keys

## Commands

    uv run vacation-planner holidays   # list slots and free windows
    uv run vacation-planner plan       # what the next run would search (no API calls)
    uv run vacation-planner scan       # execute searches, store results
    uv run vacation-planner report     # render docs/site
    uv run vacation-planner notify     # email pending deals
    uv run vacation-planner run        # scan + report + notify (what CI runs)

## GitHub Actions

1. Push the repo to GitHub (public, so Pages is free).
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Secrets and variables → Actions: add `SERPAPI_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`.
4. Actions → scan → Run workflow (optionally with a limit) for a first run. The report appears at `https://<owner>.github.io/<repo>/`.

Runs happen Mondays 05:00 UTC. Change the cron and `budget.runs_per_month` together.

## First live run

The SerpApi fixture in `tests/fixtures/serpapi_ham_bkk.json` is synthetic until the first real search is recorded (see `docs/superpowers/plans/2026-09-19-vacation-planner.md` Task 16 for the steps). `fast-flights` sends a `SOCS=CAI` consent cookie with its requests so it works from EU networks, where Google would otherwise return a consent interstitial instead of the flights page.

## Config

- `config/holidays.yaml` — the slots and, per slot, which destinations to search (`targets`). Only listed targets are ever searched.
- `config/destinations.yaml` — the catalogue; `cabin: business` for long haul, `cabin: any` for Europe.
- `config/settings.yaml` — origins, trip length, budget, providers, deal thresholds.
