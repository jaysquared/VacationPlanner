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

An ordered list of providers behind one `FlightClient` protocol. All read
Google Flights, so their prices are comparable and share one price history.
Each provider has an optional monthly budget; providers without a budget are
unlimited (rate-limited by a pause instead). Default order:

1. **SerpApi** (`engine=google_flights`), free plan ~250 searches/month.
2. **SearchApi.io** (`engine=google_flights`), free plan 100 searches
   (confirm with the account whether that is monthly or one-time and set the
   budget accordingly).
3. **`fast-flights`** (unofficial, keyless Google Flights client), unlimited,
   5 s pause between calls, consent cookie `SOCS=CAI` so it works from EU IPs.

### 2.1 SerpApi (verified live 2026-09-19)

Params: `departure_id`, `arrival_id`, `outbound_date`, `return_date`,
`type=1`, `travel_class` (1 economy, 3 business), `adults`, `children`,
`currency=EUR`, `hl=en`, `gl=de`, `stops` (1 nonstop, 2 one-stop-or-fewer,
3 two-or-fewer), `exclude_airlines`, `api_key`.
Response: `best_flights`/`other_flights` itineraries with `flights[]` legs
(`departure_airport.time` as `YYYY-MM-DD HH:MM`, `flight_number`, `airline`),
`total_duration`, `price`; `price_insights` (`price_level`,
`typical_price_range` as `[low, high]`) is **sometimes absent**;
`search_metadata.google_flights_url`. Errors: HTTP 401/403 with
`{"error": ...}` for bad keys (never retried), 429 or "out of searches" for
quota.

### 2.2 SearchApi.io (verified live 2026-09-19)

URL `https://www.searchapi.io/api/v1/search`. Params: `engine=google_flights`,
`departure_id`, `arrival_id`, `outbound_date`, `return_date`,
`flight_type=round_trip`, `travel_class` (`economy` | `business`), `adults`,
`children`, `currency=EUR`, `hl=en`, `gl=de`, `stops` (`nonstop` |
`one_stop_or_fewer` | `two_stops_or_fewer` | `any`), `exclude_airlines`,
`api_key`. Response: same itinerary shape as SerpApi except legs carry
`departure_airport.date` and `.time` separately, `price_insights.typical_price_range`
is `{low_price, high_price}`, and the Google link is
`search_metadata.request_url`. Errors: HTTP 401 `{"error": "Invalid API key."}`;
quota assumed 402/429 or an error mentioning credits.

### 2.3 fast-flights

As before (see 5.4): no airline-exclusion parameter, so the client filters
after fetching and refuses to return results when the response lacks airline
metadata while exclusions are configured. No price insights.

### 2.4 Price semantics (verified)

Both APIs and fast-flights return the **total for all travellers**: for
HAM–BKK Business, 2 adults + 1 child, 17–31 Oct 2026, all three reported the
same Emirates itinerary at 16,824 EUR (5,608 EUR per person). `price_is_total`
is therefore `true` for every provider.

### 2.5 Provider allocation

The planner computes, for each budgeted provider, its per-run share
`max(1, monthly_budget // runs_per_month)` capped by the budget remaining this
month (`monthly_budget − searches used this month`, counting ok and error
rows). It walks the planned searches in slot order and assigns each to the
first provider in the list with share left; unlimited providers absorb the
rest, up to `max_searches_per_run`. If no provider can take a search, the
plan ends there.

The executor tries the planned provider, then each later provider in the
list. A quota or auth failure marks that provider dead for the rest of the
run. Every attempt is recorded as a search row (ok or error) so monthly usage
counters stay accurate.

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
  - code: HKT
    name: Phuket
    cabin: business
    max_price_per_person: 2000
    origins: [HAM, FRA]      # optional; defaults to settings.origins
```

`origins` overrides `settings.origins` for that destination only. Each
(origin, destination) pair is a separate route, so the per-route pair cap
applies per origin.

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
  order:
    - { name: serpapi, monthly_budget: 250 }
    - { name: searchapi, monthly_budget: 100 }
    - { name: fast_flights, pause_seconds: 5 }     # no budget = unlimited
  price_is_total: { serpapi: true, searchapi: true, fast_flights: true }
budget:
  runs_per_month: 4               # Mondays: 4-5 runs/month; shares are capped by real usage
  max_searches_per_run: 120       # hard cap across all providers
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

API keys (`SERPAPI_KEY`, `SEARCHAPI_KEY`), recipients and SMTP credentials are
env vars, never config, so the repo contains no personal data even if it is
public. A listed provider whose key is missing is skipped with a warning.

## 4. Domain model

Plain dataclasses / pydantic models shared by all stages:

- `Slot` — id, name, start, end, targets, nights override.
  Derived: `free_window` = (start extended back to the preceding Saturday if
  start is a Monday, end extended forward to the following Sunday if end is a
  Friday), then widened by bridge days.
- `Destination` — code, name, cabin, max price, optional origins.
- `SearchRequest` — origin, destination, outbound_date, return_date, cabin,
  adults, children, slot_id. Hashable; equality is the dedup key.
- `Offer` — search reference, price_total, currency, per_person, airlines,
  stops, duration_minutes, departs_at, arrives_at, price_level,
  typical_low, typical_high, google_url, raw flight JSON, `legs`.
- `Leg` — one flight of an itinerary: origin, destination, departs_at,
  arrives_at (local times, "YYYY-MM-DD HH:MM", as the provider reports them).
  The gaps between consecutive legs are the layovers (5.4a).
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
2. For each (slot, target) pick the origins: the destination's own `origins`
   if it has them, otherwise `settings.origins`. For each (slot, origin,
   target) route, enumerate all (outbound, return) pairs inside the free
   window that satisfy the slot's night range.
3. Rank pairs per route: pairs with no data first, then oldest observation
   first. Take at most `max_pairs_per_route_per_run`.
4. Concatenate routes in slot order and cut at `max_searches_per_run`.
   Assign each search to the first provider in `providers.order` with
   per-run share left, where a budgeted provider's share is
   `max(1, monthly_budget // runs_per_month)` capped by
   `monthly_budget − searches recorded this calendar month` (a month with
   five Mondays must not spend five shares). Unlimited providers absorb the
   rest. If no provider can take a search the plan ends there.

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
  its entry's `pause_seconds` before each call. Raises `ProviderError` on
  parse failure or empty response.
- `providers/searchapi.py`: the same for SearchApi.io (2.2), reusing the
  SerpApi airline-code helper; 401/403 raise `AuthError`.
- `providers/executor.py`: walks the plan, tries the providers per 2.5,
  and writes each result through storage in its own transaction.

### 5.4a Layover rule

`settings.layovers` sets `max_minutes` (per individual stop) and an optional
`forbidden_window` of local times, e.g. `["23:00", "05:00"]`.
`itinerary.layovers(legs)` turns consecutive legs into `Layover(airport,
starts_at, ends_at, minutes)`; `itinerary.passes_layover_rule(legs,
settings)` is false as soon as one layover is longer than `max_minutes` or
its half-open interval `[starts_at, ends_at)` overlaps the window on any day
it spans (the window wraps midnight when its start is later than its end).
Every client applies it through `providers.base.filter_layovers` directly
after the excluded-airline filter; an offer whose legs the provider did not
report is kept.

### 5.5 `storage` — SQLite

File `data/planner.sqlite`, committed to the repo. Schema managed by numbered
migration scripts applied on startup.

- `runs(id, started_at, finished_at, planned, executed, status)`
- `searches(id, run_id, slot_id, origin, destination, outbound_date,
  return_date, cabin, adults, children, provider, requested_at, status,
  error)`
- `offers(id, search_id, provider, price_total, currency, per_person, airlines_json,
  stops, duration_minutes, departs_at, arrives_at, price_level, typical_low,
  typical_high, google_url, flight_json, legs_json)` — `legs_json` added by
  migration `002_offer_legs.sql` (default `'[]'`), so the layover rule can be
  re-checked from stored data
- `deals(id, offer_id, slot_id, reasons_json, score, detected_at,
  notified_at)`
- View: `cheapest_per_search`, plus the `route_observations` query method
  (cheapest per search for one slot, route and cabin, newest first).

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
  `runs_per_month` sets each provider's per-run share and the planner caps
  it by the searches actually recorded this month),
  `workflow_dispatch` with optional `limit`.
- Steps: checkout, set up Python 3.12, install with `uv sync`,
  `vacation-planner run`, commit `data/planner.sqlite` and `docs/site/` with
  message `scan: <date> (<n> searches, <m> new deals)`, upload `data/raw/`
  as an artifact with 30-day retention, deploy Pages from `docs/site`.
- `concurrency: scan` with `cancel-in-progress: false` so runs never overlap.
- Secrets: `SERPAPI_KEY`, `SEARCHAPI_KEY`, `SMTP_*`, `MAIL_FROM`, `MAIL_TO`.

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
- Provider quota exhausted, auth error, or failure: the search goes to the
  next provider in `providers.order`; a quota/auth failure marks that
  provider dead for the rest of the run. If every remaining provider fails
  or is dead, the search is stored as `error`/`skipped`, run status
  `partial`, report and notify still execute.
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
- `providers/searchapi`: parsing of the recorded response (date+time legs,
  dict price range, `request_url`), param mapping, auth/quota/retry paths.
- `providers/executor`: ordered fallback across three providers, dead
  providers skipped, quota mid-run, every attempt recorded.
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
