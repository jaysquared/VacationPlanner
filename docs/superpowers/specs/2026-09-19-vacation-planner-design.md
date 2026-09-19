# Vacation Planner — Design Spec

Date: 2026-09-19
Status: approved 2026-09-19

## 1. Purpose

A scheduled scanner that finds good flight deals for a Hamburg family
(2 adults, 1 child born 2019-04-21) during Hamburg school holidays. It runs
unattended on GitHub Actions, keeps a price history, flags deals, publishes an
HTML report via GitHub Pages and emails a summary when new deals appear.

Hard rules:

- Business Class only for destinations outside Europe. Within Europe any cabin.
  The cabin requirement is set per destination in config, so grey-zone places
  (Canary Islands, Egypt, Turkey, Dubai) are the user's call, not the code's.
- Certain airlines are excluded (seeded with Air India, `AI`).
- Travel only inside school-free windows. Bridge days are configurable and
  default to zero.
- Lookups are minimised: each holiday slot lists exactly which destinations to
  search. There is no destinations × holidays cross product.

Out of scope for this version: hotels, a web UI, multi-city trips, one-way
trips, price alerts from other sources. The module boundaries below are chosen
so these can be added without restructuring.

## 2. Data sources

Two providers behind one `FlightClient` protocol. Both read Google Flights,
so their prices are comparable and share one price history.

### 2.1 Primary: SerpApi (`engine=google_flights`)

Facts the design relies on (confirmed against the live API during
implementation, with the response recorded as a test fixture):

- One request = one origin, one destination, one outbound date, one return
  date, one cabin class, passenger counts, currency. Parameters used:
  `departure_id`, `arrival_id`, `outbound_date`, `return_date`,
  `travel_class` (1 economy, 2 premium economy, 3 business, 4 first),
  `adults`, `children`, `currency=EUR`, `hl=en`, `gl=de`,
  `exclude_airlines`, `stops`, `type=1` (round trip).
- The first response lists outbound options (`best_flights`, `other_flights`)
  each with a round-trip `price`, plus `price_insights` with `lowest_price`,
  `price_level` (`low` / `typical` / `high`) and `typical_price_range`, plus
  `search_metadata.google_flights_url`.
- Return-leg details require a second request with a `departure_token`.
  We never make that request; the round-trip price and the Google Flights link
  are sufficient for deal detection and booking.
- Whether `price` is per person or total for all passengers is verified in
  implementation. Offers store the raw price and the passenger counts so the
  per-person figure is derived, never guessed.

Budget: free tier is 100 searches/month. The first paid plan is 5,000/month at
roughly USD 900/year and is not planned. The planner takes the monthly budget
from config and never exceeds the per-run share of it.

### 2.2 Backup: `fast-flights` (unofficial Google Flights, no key)

PyPI package `fast-flights` (3.1.0, August 2026). Supports seat class incl.
business, adults/children, round trip, max stops, currency. It has no
airline-exclusion parameter, so the client drops itineraries containing an
excluded airline after fetching. It returns a price level (low/typical/high)
and per-itinerary price, airline, stops, duration and times, but no typical
price range and no deep link; the client builds the Google Flights URL from
the query itself. Being unofficial it can break without notice; that is
acceptable for a backup and is why it is not the primary.

### 2.3 Provider allocation

The planner produces an ordered list of searches. The executor sends the
first `serpapi_per_run` of them to SerpApi and the remainder, up to
`max_searches_per_run`, to the backup. A SerpApi failure (error after
retries, or quota exhausted) re-runs that search on the backup. Every offer
records its `provider`. The backup client waits a configurable pause between
requests and never runs in parallel.

Amadeus Self-Service was considered and rejected: it was decommissioned on
2026-07-17.

## 3. Configuration

All config lives in `config/*.yaml`, loaded into pydantic models. Validation
errors name the file, the key and the problem. Secrets (SerpApi key, SMTP)
come from environment variables, locally via `.env`.

### 3.1 `travellers.yaml`

```yaml
adults: 2
children:
  - birthdate: 2019-04-21
```

Child age is computed against the outbound date of each search. Google
Flights counts ages 2–11 as `children`, 12+ as adults, under 2 as infants.
The mapping is a pure function so it is trivially testable.

### 3.2 `destinations.yaml`

The catalogue. A destination must exist here before a holiday slot can target
it.

```yaml
destinations:
  - code: BKK
    name: Bangkok
    cabin: business          # business | any
    max_price_per_person: 2200   # EUR, optional
  - code: PMI
    name: Palma de Mallorca
    cabin: any
```

Starter set (user prunes/extends): Europe `LIS PMI ATH FNC TFS LCA`; long haul
`BKK HKT DXB MLE CPT MRU JFK MIA CUN NRT SIN DPS`.

### 3.3 `holidays.yaml`

The driving list. Seeded from the official Hamburg Ferienordnung 2024/25–
2029/30 (hamburg.de PDF, dates are first and last holiday day). Each slot
lists its targets; a slot with no targets is shown in the report but never
searched.

```yaml
source: https://www.hamburg.de/resource/blob/134372/5bc131bdd36a604f67b361d21f7df37e/ferienordnung-hamburg-2024-2030-data.pdf
holidays:
  - id: herbst-2026
    name: Herbstferien 2026
    start: 2026-10-19
    end: 2026-10-30
    targets: [BKK]
  - id: weihnachten-2026
    name: Weihnachtsferien 2026/27
    start: 2026-12-21
    end: 2027-01-01
    targets: [MLE]
    nights: { min: 10, max: 14 }     # optional override of settings
  - id: fruehjahr-2027
    name: Frühjahrsferien 2027
    start: 2027-03-01
    end: 2027-03-12
    targets: []
  - id: pfingsten-2027
    name: Himmelfahrt/Pfingsten 2027
    start: 2027-05-07
    end: 2027-05-14
    targets: [PMI, LIS]
  - id: sommer-2027
    name: Sommerferien 2027
    start: 2027-07-01
    end: 2027-08-11
    targets: [JFK]
    nights: { min: 14, max: 21 }
```

Seed data covers 2026/27 through 2029/30 from the PDF, with targets only on
the 2026/27 slots and at most two per slot to start small. The Halbjahrespause
(single Friday) is deliberately not seeded: with the enclosing weekend it is
three days, always below the minimum trip length.

Official dates, for reference:

| School year | Herbst | Weihnachten | Frühjahr | Himmelfahrt/Pfingsten | Sommer |
|---|---|---|---|---|---|
| 2026/27 | 19.10.–30.10.2026 | 21.12.2026–01.01.2027 | 01.03.–12.03.2027 | 07.05.–14.05.2027 | 01.07.–11.08.2027 |
| 2027/28 | 11.10.–22.10.2027 | 20.12.–31.12.2027 | 06.03.–17.03.2028 | 22.05.–26.05.2028 | 03.07.–11.08.2028 |
| 2028/29 | 02.10.–13.10.2028 (+ Brückentag 30.10.) | 18.12.–29.12.2028 | 05.03.–16.03.2029 | 11.05.–18.05.2029 | 02.07.–10.08.2029 |
| 2029/30 | 01.10.–12.10.2029 | 21.12.2029–04.01.2030 | 04.03.–15.03.2030 | 20.05.–24.05.2030 (+ Brückentag 31.05.) | 04.07.–14.08.2030 |

### 3.4 `settings.yaml`

```yaml
origins: [HAM]                  # list so FRA/CPH can be added later
nights: { min: 7, max: 14 }     # default trip length, per-slot override allowed
bridge_days: { before: 0, after: 0 }
max_stops: 1                    # 0 nonstop, 1 one stop, 2 two stops (Google param semantics mapped in client)
excluded_airlines: [AI]
providers:
  primary: serpapi
  backup: fast_flights            # or null to disable
  backup_pause_seconds: 5
budget:
  serpapi_per_month: 100
  runs_per_month: 4               # weekly (Mondays: 4-5 runs/month), matches the CI cron
  max_searches_per_run: 60        # hard cap incl. backup
  max_pairs_per_route_per_run: 3
deals:
  median_ratio: 0.85            # price <= 85% of historical median
  min_history_points: 3         # below this, fall back to route-level median across slots
  renotify_drop_ratio: 0.95     # re-report only if price falls to <= 95% of last reported
  lookahead_days: 330           # ignore slots starting later than this
report:
  output_dir: docs/site
email:
  mode: deals_only              # deals_only | always | never
```

Recipients and SMTP credentials are env vars (`MAIL_TO`, `SMTP_*`), never
config, so the repo contains no personal data even if it is public.

## 4. Domain model

Plain dataclasses / pydantic models shared by all stages:

- `Slot` — id, name, start, end, targets, nights override.
  Derived: `free_window` = (start extended back to the preceding Saturday if
  start is a Monday, end extended forward to the following Sunday if end is a
  Friday), then widened by bridge days.
- `Destination` — code, name, cabin, max price.
- `SearchRequest` — origin, destination, outbound_date, return_date, cabin,
  adults, children, slot_id. Hashable; equality is the dedup key.
- `Offer` — search reference, price_total, currency, per_person, airlines,
  stops, duration_minutes, departs_at, arrives_at, price_level,
  typical_low, typical_high, google_url, raw flight JSON.
- `Deal` — offer reference, slot_id, reasons (list of enum), score,
  detected_at, notified_at.

## 5. Pipeline stages

Package `vacation_planner/`. Each stage is a module with a small public
function and no knowledge of the others' internals.

### 5.1 `config` — load and validate

Reads the four YAML files and env vars. Cross-checks that every slot target
exists in the catalogue. Exposes one `Config` object.

### 5.2 `calendar` — school-free windows

Pure functions: `free_window(slot, bridge_days)`, `child_age_on(birthdate,
date)`, `pax_for(travellers, date)` → (adults, children, infants).

### 5.3 `planner` — decide what to search

Input: config, today's date, storage (for freshness). Output: ordered list of
`SearchRequest` within the per-run budget.

1. Select slots that have targets and whose start is after today and within
   `lookahead_days`. Order by start date ascending.
2. For each (slot, origin, target) route, enumerate all (outbound, return)
   pairs inside the free window that satisfy the slot's night range.
3. Rank pairs per route: pairs with no data first, then oldest observation
   first. Take at most `max_pairs_per_route_per_run`.
4. Concatenate routes in slot order and cut at `max_searches_per_run`.
   The first `serpapi_per_month / runs_per_month` (rounded down, minimum 1)
   are assigned to the primary provider, the rest to the backup. That share is
   additionally capped at `serpapi_per_month` minus the primary searches already
   recorded this calendar month, because a month with five Mondays would
   otherwise spend five shares. With no backup configured the cut is at the
   (capped) primary share, so a spent month plans nothing.

The `plan` CLI command prints this list and its count without spending
anything.

### 5.4 `providers` — fetch

`FlightClient` protocol: `search(request) -> SearchResult`. Two
implementations plus a fake for tests.

- `providers/serpapi.py`: maps the request to SerpApi params, calls the API,
  saves the raw JSON to the run's raw directory, parses `best_flights` and
  `other_flights` into `Offer` objects. Retries transient errors three times
  with exponential backoff. Raises `QuotaExhausted` on the API's quota error.
- `providers/fast_flights.py`: builds the equivalent `fast-flights` query,
  filters out itineraries with excluded airlines, maps results to `Offer`
  with `typical_low/high` empty and a constructed Google Flights URL. Sleeps
  `backup_pause_seconds` before each call. Raises `ProviderError` on parse
  failure or empty response.
- `providers/executor.py`: walks the plan, assigns providers per 2.3, handles
  fallback, and writes each result through storage in its own transaction.

### 5.5 `storage` — SQLite

File `data/planner.sqlite`, committed to the repo. Schema managed by numbered
migration scripts applied on startup.

- `runs(id, started_at, finished_at, planned, executed, status)`
- `searches(id, run_id, slot_id, origin, destination, outbound_date,
  return_date, cabin, adults, children, provider, requested_at, status,
  error)`
- `offers(id, search_id, provider, price_total, currency, per_person, airlines_json,
  stops, duration_minutes, departs_at, arrives_at, price_level, typical_low,
  typical_high, google_url, flight_json)`
- `deals(id, offer_id, slot_id, reasons_json, score, detected_at,
  notified_at)`
- Views: `cheapest_per_search`, `route_history` (cheapest per search grouped
  by slot, route, cabin, requested_at).

One transaction per search so a crash leaves consistent data.

### 5.6 `deals` — detect

For each new search's cheapest offer, compute:

- `BELOW_MEDIAN`: price ≤ `median_ratio` × median of prior cheapest prices
  for the same (slot, route, cabin). If fewer than `min_history_points`,
  use the median across all slots for (route, cabin). If still too few, skip
  this rule.
- `GOOGLE_LOW`: `price_level == "low"`.
- `UNDER_MAX`: per_person ≤ destination's `max_price_per_person`.
- `NEW_LOW`: lowest price ever recorded for (slot, route, cabin).

An offer with at least one reason becomes a `Deal`. Score = number of
reasons, tie-broken by ratio to median. Notification dedup: a deal is marked
notifiable only if no prior notified deal exists for (slot, route, cabin)
or the price is ≤ `renotify_drop_ratio` × the last notified price.

### 5.7 `report` — HTML

Jinja2 templates → `docs/site/index.html` plus one page per route
`docs/site/routes/<slot>-<origin>-<dest>.html`. Static, no JS dependencies
needed for v1.

Index page:
1. New deals since last run (empty state text if none).
2. One section per upcoming slot: table of targets with best current price,
   per-person price, airline(s), stops, outbound/return dates, ratio to
   median, price level, Google Flights link, deal badge.
3. Footer: run time, searches executed, budget used this month.

Route page: table of every observation (date searched, dates flown, price,
airline, level) newest first.

### 5.8 `notify` — email

SMTP via env (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`MAIL_FROM`, `MAIL_TO`). Sends a plain-text plus HTML email listing notifiable deals and
linking to the Pages report. Mode `deals_only` sends nothing when there are
none. Failures are logged and never fail the run. Marks deals `notified_at`
only after a successful send.

### 5.9 `cli`

Entry point `vacation-planner` (typer):

- `plan` — print planned searches for a run, no API calls.
- `scan` — plan, execute, store, detect deals. `--limit N` caps searches.
- `report` — render HTML from storage.
- `notify` — send pending deal notifications.
- `run` — scan, report, notify in sequence (what CI calls).
- `holidays` — list slots with their free windows and target counts.

## 6. Runtime

GitHub Actions workflow `scan.yml`:

- Triggers: cron Monday 05:00 UTC (4-5 runs/month depending on the month;
  `runs_per_month` sets the per-run share and the planner caps it by the
  primary searches actually recorded this month),
  `workflow_dispatch` with optional `limit`.
- Steps: checkout, set up Python 3.12, install with `uv sync`,
  `vacation-planner run`, commit `data/planner.sqlite` and `docs/site/` with
  message `scan: <date> (<n> searches, <m> new deals)`, upload `data/raw/`
  as an artifact with 30-day retention, deploy Pages from `docs/site`.
- `concurrency: scan` with `cancel-in-progress: false` so runs never overlap.
- Secrets: `SERPAPI_KEY`, `SMTP_*`, `MAIL_FROM`, `MAIL_TO`.

`data/raw/` is git-ignored. Locally `vacation-planner run` behaves the same
with `.env`.

Repo visibility: GitHub Pages on a free personal account requires a public
repo. The committed data is only flight prices, slot names and airport codes,
which is acceptable to publish. If the repo must stay private, Pages needs
GitHub Pro, or the report is downloaded as a workflow artifact instead; the
pipeline does not change either way.

## 7. Error handling

- Config errors: fail before any API call with file/key/problem.
- API transient error: three retries, then the search is stored with
  `status=error` and the run continues.
- Primary quota exhausted or failed: the search goes to the backup. If no
  backup is configured or the backup also fails, the search is stored as
  `skipped`/`error`, run status `partial`, report and notify still execute.
- Parse error on a response: the raw JSON is kept, the search is marked
  `error`, the run continues. A test fixture is added when this happens.
- Email failure: logged, run exits 0, deals stay un-notified and are picked
  up next run.
- The CI commit step only runs if there are changes.

## 8. Testing

pytest, no live API in tests.

- `calendar`: free-window extension for Monday/Friday and mid-week
  boundaries, bridge days, child age on birthday edge, pax mapping at 2/12.
- `planner`: pair enumeration for short and six-week windows, per-route cap,
  freshness ordering with a fake storage, budget cut, lookahead filter, slots
  without targets ignored. Uses an injected `today`.
- `providers/serpapi`: parsing of a recorded fixture (best + other flights,
  price insights, missing insights), param mapping incl. excluded airlines
  and travel class, retry and quota paths with a stubbed HTTP layer.
- `providers/fast_flights`: mapping from a recorded result object, excluded
  airline filtering, URL construction, pause behaviour with a fake sleep.
- `providers/executor`: primary/backup split at the budget boundary,
  fallback on primary failure, no backup configured, quota exhausted mid-run.
- `storage`: migrations apply cleanly on an empty DB and are idempotent;
  round-trip of run/search/offer/deal.
- `deals`: each rule in isolation with fixture history, fallback to route
  median, dedup/renotify threshold.
- `report`: renders index and route page from a seeded DB without error and
  contains expected strings.
- `notify`: message composition; SMTP stubbed.
- End-to-end: `run` with a fake client over a seeded config produces a DB,
  HTML and one notification.

## 9. Project layout

```
config/            travellers.yaml destinations.yaml holidays.yaml settings.yaml
data/              planner.sqlite (committed), raw/ (ignored)
docs/site/         generated report (committed, served by Pages)
docs/superpowers/  specs and plans
vacation_planner/  config.py calendar.py planner.py storage.py deals.py
                   report.py notify.py cli.py templates/ migrations/
                   providers/ (base.py serpapi.py fast_flights.py executor.py)
tests/             mirrors the package, fixtures/ holds recorded responses
.github/workflows/scan.yml
pyproject.toml     (uv, typer, pydantic, pyyaml, jinja2, httpx, python-dotenv,
                    fast-flights)
```

## 10. Future extensions (not in this spec)

- Hotels: a `HotelClient` protocol and `hotel_offers` table, planned per slot
  and target the same way.
- Web UI: FastAPI reading `planner.sqlite`; the report templates become the
  first views.
- Additional origins with a per-origin surcharge for ground travel.
- Price-history sparklines on the index page.
