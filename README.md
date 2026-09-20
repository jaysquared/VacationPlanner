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
    uv run vacation-planner notify     # send the weekly digest email
    uv run vacation-planner run        # scan + report + notify (what CI runs)

## The weekly digest email

Every run sends one mail — plain text and HTML, English, prices as `6,900 €` —
with the same five sections in both bodies:

1. **Header** — the date, the run in one line (`111 searches · 108 ok · 3 failed
   · providers: serpapi 62, searchapi 25, fast_flights 21`) and how much of each
   API budget this calendar month has used.
2. **New deals** — one card per route that has not been mailed yet, the
   strongest first (ties go to the cheaper trip per person):
   `Phuket (HKT) from Hamburg · 19–29 Dec · Business`, the price line
   (`6,900 € total · 2,300 € per person · SWISS + Bangkok Airways · 2 stops`),
   why it counts as a deal in plain words (`21 % below the usual price for this
   route (median 8,900 €); lowest price seen so far for this trip`) and a Google
   Flights link. Other dates on the same route hang under the card as
   `also 19–31 Dec 7,493 €`. `No new deals this week.` when nothing is new.
3. **One table per searched holiday** — every destination with data, cheapest
   first, in six columns that fit a phone: destination (with `level · airlines ·
   stops` underneath), origin (with `FRA 9,000 € (−25 %)` underneath), dates,
   price (with the per-person price underneath), **vs last week** (`▼ 12 %`
   against the cheapest price of the last run that saw the route) and **lowest
   seen** (the cheapest price ever recorded for it — `–` while this week's
   price *is* the lowest on record, since the number is already in the price
   column), plus a Book link. Each table scrolls sideways on a narrow phone
   instead of squeezing its columns.
4. **Not searched this run** — per holiday: `no targets configured`, `not
   searched in this run`, or `searched but no result: LGK, KUL` for the targets
   that came back empty. Holidays further out than `deals.lookahead_days`
   collapse into one line (`15 later holidays have no targets configured …`).
5. **Footer** — the link to the full report and the reminder that prices are
   totals for the whole family and that the layover rule only checks the
   outbound legs.

The subject says it all at a glance:
`Vacation Planner · 2 new deals · cheapest Weihnachten: Phuket 11,785 €`, or
`Vacation Planner · weekly update` when nothing is new. The count is the number
of cards in section 2 — one per route — not the number of stored deal rows, so
it matches what the mail actually shows.

### Email modes

```yaml
email:
  mode: digest                    # digest | deals_only | never
```

- `digest` (default) — one mail per run, even a run that found nothing.
- `deals_only` — the same digest, but only when there are new deals to report.
- `never` — no mail at all. (`always` is still accepted and means `digest`.)

Deals are marked as notified only after the mail is out, so a failed send keeps
them pending for the next run; a send failure never fails the run. SMTP comes
from the environment (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`MAIL_FROM`, `MAIL_TO`); port 465 is implicit TLS, anything else uses STARTTLS.
Every send logs the subject and the recipients at INFO, so the job log shows
exactly what left the machine.

**Synthetic prices never reach the inbox.** `run --fake` invents prices, so it
skips the mail entirely and prints `notified 0 deals (fake run, email
suppressed)`. If fake data does end up in the database anyway, every digest
built from it carries a red `TEST DATA — fake provider` line under the header.

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

When the Google page holds no matching itineraries at all — no Business fare on the route, say — the scraper's parser either has nothing to index into or hands back an empty list. Neither is counted as a failed search: `fast-flights` records an ok search with no offers and notes it at INFO, so the run keeps its `ok` status and the destination shows up under `searched but no result` in the digest instead of as an error. (This is safe only because `fast-flights` is last in `providers.order` — a returned result ends the fallback chain.)

## First live run

Both APIs were verified live on 2026-09-19 with the same search (HAM→BKK Business, 2 adults + 1 child, 17–31 Oct 2026): SerpApi, SearchApi.io and `fast-flights` all reported the same Emirates itinerary at 16,824 EUR. That price is the **total for all travellers** (5,608 EUR per person), so `providers.price_is_total` is `true` for all three. The recorded responses are the test fixtures `tests/fixtures/serpapi_ham_bkk.json` and `tests/fixtures/searchapi_ham_bkk.json`; note that the SerpApi response carried no `price_insights`, so price levels are only sometimes available.

## Config

- `config/holidays.yaml` — the slots and, per slot, which destinations to search (`targets`). Only listed targets are ever searched.
- `config/destinations.yaml` — the catalogue; `cabin: business` for long haul, `cabin: any` for Europe.
- `config/settings.yaml` — origins, trip length, budget, providers, deal thresholds.

### Origins per destination

`settings.yaml` lists the default `origins` (`[HAM]`). A destination may name its
own, and then only those are searched for it:

```yaml
destinations:
  - { code: TFS, name: Tenerife South, cabin: any }                                  # HAM only
  - { code: HKT, name: Phuket, cabin: business, max_price_per_person: 2000, origins: [HAM, FRA] }
```

Each (origin, destination) pair is a route of its own, so
`budget.max_pairs_per_route_per_run` applies per origin.

### Layovers

Up to `max_stops` stops per direction, and every single stop must be short and
outside the night:

```yaml
layovers:
  max_minutes: 180                        # each individual stop
  forbidden_window: ["23:00", "05:00"]    # local time; a stop touching this window is rejected
```

A stop is measured in the stopover airport's local time, from the arrival of one
leg to the departure of the next. It is rejected when it is longer than
`max_minutes` or overlaps the window on any day it spans — so 23:00–05:00 kills a
22:30–01:00 wait and a 04:30–06:00 one, while 05:00–07:00 is fine. Set
`forbidden_window: null` to check the duration only. Itineraries a provider
reports without legs are never rejected by this rule, and an itinerary whose legs
are out of order (a departure before the previous arrival) always is.

**Outbound only.** All three providers return outbound options priced for the whole
round trip; the matching return itineraries sit behind a second `departure_token`
request that this scanner never makes. So: the rule is evaluated on the outbound
legs the providers report; return-leg stops are not visible without a second paid
request and are not checked. Verify the return itinerary on the Google Flights link
before booking.

### Frankfurt has to be worth the trip

Getting to Frankfurt costs a train ride and most of a day, so a fare from an
origin other than `home` only counts as a deal if it beats the best known
Hamburg fare for the same slot, destination and cabin by **both** margins:

```yaml
alternate_origins:
  home: HAM
  min_saving_ratio: 0.20          # a FRA fare must be >= 20 % cheaper than the best HAM fare
  min_saving_total: 500           # ... and >= 500 EUR cheaper in absolute terms
```

If it does not, it is not reported at all, however low Google calls it. If there
is no Hamburg price on record yet, the fare is judged on the normal rules alone.
A deal that clears both margins carries the extra reason `cheaper_than_home`.
In the report, Hamburg and Frankfurt share one row per destination: the
alternate origin shows up as `FRA 9,000 € (−25 %)` next to the Hamburg price,
and each origin keeps its own history page.
