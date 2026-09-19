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
3. Settings → Secrets and variables → Actions: add `SERPAPI_KEY`, `SEARCHAPI_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`.
4. Actions → scan → Run workflow (optionally with a limit) for a first run. The report appears at `https://<owner>.github.io/<repo>/`.

Runs happen Mondays 05:00 UTC. Change the cron and `budget.runs_per_month` together.

## Providers

Searches go to the providers listed in `config/settings.yaml` under `providers.order`, best first. Each search takes the first provider that still has budget this run; if a provider fails, the search falls through to the next one in the list, and a quota or auth failure drops that provider for the rest of the run.

| Provider | Key | Budget |
| --- | --- | --- |
| SerpApi (`engine=google_flights`) | `SERPAPI_KEY` | free plan ~250 searches/month |
| SearchApi.io (`engine=google_flights`) | `SEARCHAPI_KEY` | free plan 100 searches — **check the account dashboard whether that allowance renews monthly or is a one-time credit**, and set `monthly_budget` accordingly (the config assumes monthly) |
| `fast-flights` (keyless scraper) | — | unlimited, 5 s pause between calls |

A listed provider whose key is missing is skipped with a warning; if none is usable the run stops and names the env vars. The per-run share is `monthly_budget / budget.runs_per_month`, capped by `monthly_budget` minus the searches already recorded this calendar month (ok and error rows both cost a credit), so a month with five Mondays cannot overspend a free tier.

`fast-flights` sends a `SOCS=CAI` consent cookie with its requests so it works from EU networks, where Google would otherwise return a consent interstitial instead of the flights page.

## First live run

Both APIs were verified live on 2026-09-19 with the same search (HAM→BKK Business, 2 adults + 1 child, 17–31 Oct 2026): SerpApi, SearchApi.io and `fast-flights` all reported the same Emirates itinerary at 16,824 EUR. That price is the **total for all travellers** (5,608 EUR per person), so `providers.price_is_total` is `true` for all three. The recorded responses are the test fixtures `tests/fixtures/serpapi_ham_bkk.json` and `tests/fixtures/searchapi_ham_bkk.json`; note that the SerpApi response carried no `price_insights`, so price levels are only sometimes available.

## Config

- `config/holidays.yaml` — the slots and, per slot, which destinations to search (`targets`). Only listed targets are ever searched.
- `config/destinations.yaml` — the catalogue; `cabin: business` for long haul, `cabin: any` for Europe.
- `config/settings.yaml` — origins, trip length, budget, providers, deal thresholds.
