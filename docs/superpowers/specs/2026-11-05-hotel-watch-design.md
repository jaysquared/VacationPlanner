# Hotel Watch — Design Spec

Date: 2026-11-05
Status: approved 2026-11-05

## 1. Purpose

Extend the weekly scanner from flights to a small set of **hotel stays the
family has already chosen**, and tell them when acting on a price would save
money. Two situations exist today:

| Watch | Stay | What the family holds | Alert when |
|---|---|---|---|
| `ikos-pfingsten-2027` | Ikos Porto Petro, Mallorca, 2027-05-07 → 05-14 (7 nights) | Booked: Junior-Suite with private garden, 5,640 EUR, free cancellation until 2027-04-07 | The same room type, refundable, costs ≤ 5,076 EUR (10 % below what they pay) — rebooking saves money |
| `waldorf-herbst-2027` | Waldorf Astoria Seychelles Platte Island, 2027-10-10 → 10-15 (5 nights) | Plan: book a Standard Room Reward for 2 adults with Hilton points, then upgrade on site for the son (+350 EUR cash) | The cheapest refundable cash rate for 3 guests drops below the points booking's cost (points × point value + 350 EUR) |
| `mango-herbst-2027` | Mango House Seychelles (LXR), 2027-10-15 → 10-20 (5 nights) | Same points plan | Same rule as the Waldorf, with its own surcharge |

This is not a hotel *discovery* tool: the family names the hotels, the scanner
watches them. A Michelin-Key shortlist for Mauritius was considered and dropped
when the trip moved to the Seychelles.

## 2. Sources

Each source sits behind one `HotelClient` protocol (mirrors `FlightClient`):
`check(stay) -> HotelResult` with a list of `HotelRate` rows. All were tested
live on 2026-11-03/05 against these exact stays; the raw responses become test
fixtures.

| Source | Reaches | Gives | Tested result |
|---|---|---|---|
| **Booking.com** — Apify actor `voyager/booking-scraper` (maintained by Apify), input: the hotel's Booking URL with `group_adults`, `group_children`, `age` | Any hotel on Booking.com | Every room type × rate, total incl. taxes, free-cancellation flag and deadline, child counted | Ikos: "Junior Suite mit eigenem Garten" 6,898 EUR, child free |
| **Hilton** — Apify actor `mu0i/hotel-rates-award-points`, chain `hilton` | Hilton-brand hotels (Waldorf `SEZPIWA`, Mango House `SEZITOL`) | `rates`: every room × rate plan in cash (public + Honors). `award`: points per night / total per room, Standard vs Premium. `parity`: the same stay on Booking.com, Expedia, Hotels.com, Agoda, Trip.com | Waldorf 2 adults: Standard Room Reward 170,000/night; 3 guests: Honors Book Early 7,402 EUR direct, Agoda 6,470 EUR (non-refundable) |
| **Google Hotels** — SerpApi `engine=google_hotels` with a `property_token` | Any hotel Google lists | Cheapest rate per site, rooms per site for some sites | Ikos: Booking.com "Junior Suite mit Gartenblick" 6,136 EUR; 3 sites |

Known limits, each handled in code:

- **Google prices at most 8 nights per query** and only ~330 days ahead. Beyond
  that it silently prices tomorrow instead (`checkInDate`/`ifDefaultDate=default`
  in the booking links). The client compares the dates in the response with the
  request and records `no_rates` on a mismatch. Config validation rejects a
  Google source on a stay longer than 8 nights. All three stays are ≤ 7 nights.
- **Apify actors are third-party code.** Each client validates that the
  response's check-in/check-out and guest count match the request; a mismatch
  or an empty result is `no_rates`, a failed run is `error`. Neither produces a
  price.
- The Hilton `parity` rows carry no room type; they are used only for the
  "cheapest cash for 3" comparison, never for the Ikos same-room rule.

Rejected after testing: trivago scrapers on Apify (listing returns only the
first page, a single-hotel link returns no price), Xotelo (no child support),
Makcorps (350 USD/month).

Which sources a watch uses:

| Watch | Booking.com | Hilton rates + award + parity | Google Hotels |
|---|---|---|---|
| Ikos | yes | — | yes |
| Waldorf | — (parity covers it) | yes | yes |
| Mango House | — (parity covers it) | yes | yes |

## 3. Configuration — `config/hotels.yaml`

```yaml
hotel_watches:
  - id: ikos-pfingsten-2027
    name: Ikos Porto Petro
    slot: pfingsten-2027
    check_in: 2027-05-07
    check_out: 2027-05-14
    room: ["Junior Suite mit eigenem Garten", "Junior Suite with Private Garden"]
    sources:
      booking: https://www.booking.com/hotel/es/ikos-porto-petro.de.html
      google: ChkI2u_o2PSToOZmGg0vZy8xMXQwbTM2Y3piEAE
    rule:
      type: booked
      paid_total: 5640
      min_saving_ratio: 0.10
      until: 2027-04-07            # free-cancellation deadline; the watch ends here

  - id: waldorf-herbst-2027
    name: Waldorf Astoria Seychelles Platte Island
    slot: herbst-2027
    check_in: 2027-10-10
    check_out: 2027-10-15
    sources:
      hilton: SEZPIWA
      google: ChkInpaOvMuKrLtZGg0vZy8xMWw3M3ZodDQ1EAE
    rule:
      type: points
      points_adults: 2             # the award is booked for 2 adults
      surcharge: 350               # cash upgrade for the son on site
      until: 2027-10-09

  - id: mango-herbst-2027
    name: Mango House Seychelles
    slot: herbst-2027
    check_in: 2027-10-15
    check_out: 2027-10-20
    sources:
      hilton: SEZITOL
      google: ChoI4ML8m8ni8aHiARoNL2cvMTFsbDlfNnhrZBAB
    rule:
      type: points
      points_adults: 2
      surcharge: 0                 # confirmed: no cost for the son
      until: 2027-10-14

hilton:
  point_value_eur: 0.0044          # Hilton's points sale, 0.44 ct/point until 2026-11-21
```

Guests default to `travellers.yaml` (2 adults + the son, aged on the check-in
date — 8 on both trips). `points_adults` overrides the guests for the award
lookup only. `room` lists substrings; a rate matches if its room name contains
any of them (case-insensitive). Unknown slots, `check_out <= check_in`, an
unknown rule type, or a Google source on > 8 nights are config errors.

## 4. Rules

**`booked`** — per run, among this run's rates whose room matches and that are
refundable, take the cheapest total. Alert when
`total <= paid_total * (1 - min_saving_ratio)` (Ikos: ≤ 5,076 EUR). A
non-refundable rate that would pass is shown in the report but does not alert:
it is not a like-for-like swap for a refundable booking.

**`points`** — `points_cost = award_points_total * point_value_eur + surcharge`,
using the cheapest **Standard** Room Reward for `points_adults`. `cash` = the
cheapest refundable total for the full family across Hilton direct, parity
channels and Google. Alert when `cash < points_cost`. If no Standard Room
Reward is offered this run, the report says so ("only Premium rewards") — that
is the other thing the family needs to know before booking; it alerts once.

Both: alerts stop after `until`; the watch is shown as ended. After an alert,
re-alert only when the price falls a further 5 % (same `renotify_drop_ratio`
as flights). Only this run's rates count, as for flights.

With today's figures nothing alerts: Ikos 6,898 EUR > 5,076 EUR; Waldorf cash
7,402 EUR (refundable) vs points 850,000 × 0.0044 + 350 = 4,090 EUR; Mango House
3,850 EUR vs 600,000 × 0.0044 = 2,640 EUR.

## 5. Storage

Migration `003_hotels.sql`, same database:

- `hotel_checks` — one row per (run, watch, source, mode): dates, adults, child
  ages, status `ok | no_rates | error`, error text, requested_at.
- `hotel_rates` — per check: channel (`direct`, `booking`, `agoda`, …), room
  name, rate name, kind `cash | points`, total (EUR) or points total, refundable,
  cancellation deadline, link.
- `hotel_alerts` — watch, rate, reason, value, detected_at, notified_at.

## 6. Run, report, email

- `vacation-planner run` checks every active watch after the flight scan; a
  hotel failure never fails the flight part. A new `hotels` command runs only
  the hotel checks; `plan` lists them with their sources.
- Cost per weekly run: ~3 SerpApi credits (one Google query per watch) and
  roughly 0.10–0.30 USD of Apify credit, inside the free 5 USD/month. The
  planner reserves the hotel queries first, so the flight searches get
  SerpApi's remaining per-run share.
- Report: a "Hotels" section on the index — one card per watch with a row
  per source (room, total, change vs last week) and a verdict line, e.g.
  "Rebook: Booking.com 4,980 EUR ≤ 5,076 EUR" or "Points still better: cash
  7,402 EUR vs points 4,600 EUR".
- Email: hotel alerts appear above the flight deals and in the subject; the
  digest always carries the hotel verdict lines.
- CI: `APIFY_TOKEN` is passed to the run step (already a repo secret).

## 7. Errors

- No `APIFY_TOKEN`: Booking and Hilton sources are skipped with a warning line
  in the digest; Google still runs.
- A source error: that check is `error`, others continue; the digest names the
  failed source. Error texts pass through `redact` (tokens never reach the DB
  or logs).
- `no_rates` (dates not open yet, wrong dates returned, room not offered):
  shown as such, never as a price.

## 8. Testing

Recorded responses from the 2026-11 probes, redacted, under
`tests/fixtures/hotels/`: Booking Ikos with child, Hilton rates/award (2 and 3
guests)/parity for the Waldorf, Hilton award/rates for Mango House, Google
Ikos, and Google's wrong-date fallback. Tests cover: config validation,
each client's parsing and date guard, both rules with boundary prices, the
`until` cut-off, re-alert damping, storage round trip, report and digest
rendering, and that a hotel failure leaves the flight run `ok`.
