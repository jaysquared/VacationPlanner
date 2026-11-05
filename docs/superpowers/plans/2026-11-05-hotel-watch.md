# Hotel Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watch three chosen hotel stays every Monday run and alert when rebooking (Ikos) or paying cash instead of Hilton points (Waldorf, Mango House) would save money.

**Architecture:** A `hotels` package beside the flight code: config schema → `HotelWatch` objects, three `HotelClient`s (Booking.com and Hilton through Apify actors, Google Hotels through SerpApi), a check runner that stores every query as a "batch" (one timestamp per run) in three new tables, two pure rule functions that turn a batch into a verdict, and report/digest sections that render the verdicts. Flight code is touched only at the seams: config loading, the SerpApi budget count, the CLI `run`/`plan` commands, the report index and the digest.

**Tech Stack:** Python 3.12, pydantic 2, httpx, sqlite3, jinja2, typer, pytest + pytest-httpx (all already in `pyproject.toml`; no new dependencies).

**Spec:** `docs/superpowers/specs/2026-11-05-hotel-watch-design.md`

## Global Constraints

- Guests: 2 adults + the son born 2019-04-21, his age computed **on the check-in date** (8 on both trips).
- Ikos rule: same room type, refundable, total `<= 5640 * (1 - 0.10)` = 5,076 EUR, until 2027-04-07.
- Points rule: `cash < standard_points_total * 0.0044 + surcharge`; Waldorf surcharge 350, Mango House 0; points booked for 2 adults; cash compared for the full family.
- Only refundable cash rates can alert. A rate whose refundability is unknown (`None`) never alerts. Rate names containing "advance purchase" are non-refundable whatever the source says.
- Only the latest batch's rates count for a verdict; earlier batches are history for "vs last week".
- Re-alert only when the compared value falls a further 5 % (`settings.deals.renotify_drop_ratio`, 0.95).
- Google Hotels: at most 8 nights per query; a response whose total is not ≈ nightly × nights is `no_rates`.
- A hotel failure never changes the flight run's status, never raises out of `run`, and secrets never reach the DB or logs (`providers.base.redact`).
- `--fake` runs skip hotel checks entirely (no network, no mail about hotels).
- Apify actors: `voyager/booking-scraper` and `mu0i/hotel-rates-award-points`; token env var `APIFY_TOKEN`.

## Review Focus

1. Hilton reports "Advance Purchase" rates as `refundable: true` (see `tests/fixtures/hotels/hilton_waldorf_rates.json`) — a reasonable person expects such a rate never to trigger the points alert. Pinned in Task 3.
2. Google silently prices tomorrow when the stay is outside its window (`google_waldorf_wrong_dates.json`: 1,641 EUR per night *and* total for 5 nights) — expected: `no_rates`, no price stored. Pinned in Task 3.
3. The son's age depends on the check-in date (born 21 April) — a watch checking in before 21 April must send age 7, after it age 8. Pinned in Task 1.
4. A watch past its `until` date — expected: no paid queries, verdict "watch ended", no alert. Pinned in Task 4.
5. Missing `APIFY_TOKEN` or a failing actor — expected: flight run still `ok`, Google checks still run, the digest names the skipped/failed source. Pinned in Tasks 4 and 6.

---

## File Structure

| File | Responsibility |
|---|---|
| `vacation_planner/models.py` (modify) | `HotelSource`, `BookedRule`, `PointsRule`, `HotelWatch`, `HotelStay`, `HotelRate`, `HotelResult` |
| `vacation_planner/config.py` (modify) | `hotels.yaml` schema + validation, `Config.hotels`, `Config.point_value_eur`, `Secrets.apify_token` |
| `vacation_planner/migrations/003_hotels.sql` (create) | `hotel_checks`, `hotel_rates`, `hotel_alerts` |
| `vacation_planner/storage.py` (modify) | hotel read/write methods; SerpApi budget count includes Google hotel checks |
| `vacation_planner/hotels/__init__.py` (create) | empty |
| `vacation_planner/hotels/clients.py` (create) | `HotelClient` protocol, `ApifyActor`, `BookingClient`, `HiltonClient`, `GoogleHotelsClient` |
| `vacation_planner/hotels/rules.py` (create) | `Verdict`, `evaluate_booked`, `evaluate_points`, `evaluate` |
| `vacation_planner/hotels/runner.py` (create) | `planned_stays`, `check_hotels`, `HotelSummary` |
| `vacation_planner/hotels/cards.py` (create) | `HotelCard`, `HotelRow`, `build_hotel_cards` for report and digest |
| `vacation_planner/planner.py` (modify) | `plan(..., reserved=...)` subtracts reserved SerpApi searches |
| `vacation_planner/cli.py` (modify) | `hotels` command, hotel lines in `plan`, hotel step in `run` |
| `vacation_planner/report.py`, `templates/index.html` (modify) | Hotels section |
| `vacation_planner/notify.py` (modify) | hotel alerts + verdict lines in the digest, subject, `deals_only` |
| `config/hotels.yaml`, `tests/fixtures/config/hotels.yaml` (create) | the three watches |
| `.github/workflows/scan.yml` (modify) | pass `APIFY_TOKEN` |
| `tests/fixtures/hotels/*.json` (exist) | recorded live responses, committed with this plan |
| `tests/test_hotels_*.py` (create) | one file per task |

---

### Task 1: Hotel models and config

**Files:**
- Modify: `vacation_planner/models.py` (append)
- Modify: `vacation_planner/config.py` (schemas after `_Holidays`, `Secrets`, `Config`, `load_config`)
- Create: `config/hotels.yaml`, `tests/fixtures/config/hotels.yaml` (identical content)
- Test: `tests/test_hotels_config.py`

**Interfaces:**
- Produces (models.py):
  - `class HotelSource(str, Enum): BOOKING="booking"; HILTON="hilton"; GOOGLE="google"`
  - `BookedRule(paid_total: float, min_saving_ratio: float, until: date)` (frozen)
  - `PointsRule(points_adults: int, surcharge: float, until: date)` (frozen)
  - `HotelWatch(id, name, slot_id, check_in: date, check_out: date, room: tuple[str, ...], sources: dict[HotelSource, str], rule: BookedRule | PointsRule)` with `.nights -> int`, `.until -> date`, `.matches_room(name: str | None) -> bool`
  - `HotelStay(watch_id, source: HotelSource, ref: str, mode: str, check_in, check_out, adults: int, child_ages: tuple[int, ...])` (frozen) with `.nights`; `mode` is one of `"rates" | "award" | "parity" | "rooms"`
  - `HotelRate(channel: str, kind: str, total: float | None = None, points: int | None = None, room: str | None = None, rate_name: str | None = None, refundable: bool | None = None, award_type: str | None = None, url: str | None = None)`; `kind` is `"cash" | "points"`, `award_type` `"standard" | "premium" | None`
  - `HotelResult(stay: HotelStay, status: str, rates: list[HotelRate], note: str | None = None)`; `status` is `"ok" | "no_rates"`
- Produces (config.py): `Config.hotels: list[HotelWatch]` (default `[]`), `Config.point_value_eur: float | None`, `Secrets.apify_token: str | None`, `guests_on(travellers: Travellers, day: date) -> tuple[int, tuple[int, ...]]` (adults, child ages; a child ≥ 12 counts as an adult)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hotels_config.py
from datetime import date
from pathlib import Path

import pytest

from vacation_planner.config import ConfigError, guests_on, load_config
from vacation_planner.models import BookedRule, HotelSource, PointsRule, Travellers, Child


def test_loads_the_three_watches(config_dir: Path):
    cfg = load_config(config_dir, env={"APIFY_TOKEN": "t"})
    ids = [w.id for w in cfg.hotels]
    assert ids == ["ikos-pfingsten-2027", "waldorf-herbst-2027", "mango-herbst-2027"]
    ikos, waldorf, mango = cfg.hotels
    assert ikos.nights == 7 and ikos.sources[HotelSource.BOOKING].startswith("https://www.booking.com/")
    assert ikos.rule == BookedRule(5640, 0.10, date(2027, 4, 7))
    assert waldorf.rule == PointsRule(2, 350, date(2027, 10, 9))
    assert mango.sources[HotelSource.HILTON] == "SEZITOL" and mango.rule.surcharge == 0
    assert cfg.point_value_eur == 0.0044
    assert cfg.secrets.apify_token == "t"


def test_room_match_is_a_case_insensitive_substring(config_dir: Path):
    ikos = load_config(config_dir, env={}).hotels[0]
    assert ikos.matches_room("Junior Suite mit eigenem Garten")
    assert ikos.matches_room("JUNIOR SUITE WITH PRIVATE GARDEN, all inclusive")
    assert not ikos.matches_room("Junior Suite mit Gartenblick")
    assert not ikos.matches_room(None)


def test_a_watch_without_room_list_matches_every_room(config_dir: Path):
    waldorf = load_config(config_dir, env={}).hotels[1]
    assert waldorf.room == () and waldorf.matches_room("anything") and waldorf.matches_room(None)


def test_missing_hotels_file_means_no_watches(config_dir: Path):
    (config_dir / "hotels.yaml").unlink()
    cfg = load_config(config_dir, env={})
    assert cfg.hotels == [] and cfg.point_value_eur is None


@pytest.mark.parametrize("old,new,message", [
    ("slot: pfingsten-2027", "slot: nowhere", "unknown slot nowhere"),
    ("check_out: 2027-05-14", "check_out: 2027-05-07", "check_out must be after check_in"),
    ("check_out: 2027-05-14", "check_out: 2027-05-17", "Google Hotels prices at most 8 nights"),
    ("type: booked", "type: maybe", "rule"),
])
def test_invalid_watches_are_rejected(config_dir: Path, old, new, message):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir, env={})


def test_a_points_watch_needs_a_hilton_source(config_dir: Path):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace("      hilton: SEZPIWA\n", "", 1))
    with pytest.raises(ConfigError, match="points rule needs a hilton source"):
        load_config(config_dir, env={})


def test_a_points_watch_needs_a_point_value(config_dir: Path):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace("hilton:\n  point_value_eur: 0.0044\n", ""))
    with pytest.raises(ConfigError, match="point_value_eur"):
        load_config(config_dir, env={})


def test_the_sons_age_is_taken_on_the_check_in_date():
    t = Travellers(2, (Child(date(2019, 4, 21)),))
    assert guests_on(t, date(2027, 4, 20)) == (2, (7,))
    assert guests_on(t, date(2027, 4, 21)) == (2, (8,))
    assert guests_on(t, date(2031, 5, 1)) == (3, ())      # 12 and older pays as an adult
```

- [ ] **Step 2: Create the config files** (the tests read `tests/fixtures/config/`, the CLI reads `config/`; same content)

```yaml
# config/hotels.yaml  and  tests/fixtures/config/hotels.yaml
# Hotel stays the family has chosen; the weekly run checks each one (see docs/superpowers/specs/2026-11-05-hotel-watch-design.md).
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
      until: 2027-04-07            # free-cancellation deadline

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
      surcharge: 0                 # no cost for the son
      until: 2027-10-14

hilton:
  point_value_eur: 0.0044          # Hilton's points sale, 0.44 ct/point until 2026-11-21
```

Note: `tests/fixtures/config/holidays.yaml` is a frozen snapshot that still has `pfingsten-2027` and `herbst-2027` — no change needed there.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_hotels_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'guests_on'`

- [ ] **Step 4: Add the models** (append to `vacation_planner/models.py`)

```python
class HotelSource(str, Enum):
    BOOKING = "booking"
    HILTON = "hilton"
    GOOGLE = "google"


@dataclass(frozen=True)
class BookedRule:
    """A stay already booked: alert when the same room is clearly cheaper to rebook."""

    paid_total: float
    min_saving_ratio: float
    until: date


@dataclass(frozen=True)
class PointsRule:
    """A stay planned on Hilton points: alert when cash for the family beats the points cost."""

    points_adults: int
    surcharge: float
    until: date


@dataclass
class HotelWatch:
    id: str
    name: str
    slot_id: str
    check_in: date
    check_out: date
    #: room-name substrings, any of which identifies the watched room; empty matches every room
    room: tuple[str, ...]
    sources: dict[HotelSource, str]
    rule: BookedRule | PointsRule

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def until(self) -> date:
        return self.rule.until

    def matches_room(self, name: str | None) -> bool:
        if not self.room:
            return True
        return name is not None and any(r.lower() in name.lower() for r in self.room)


@dataclass(frozen=True)
class HotelStay:
    """One query: a watch, a source, and the guests it is priced for."""

    watch_id: str
    source: HotelSource
    ref: str                  # Booking URL, Hilton hotel code or Google property token
    mode: str                 # rates | award | parity | rooms
    check_in: date
    check_out: date
    adults: int
    child_ages: tuple[int, ...]

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass
class HotelRate:
    channel: str              # direct, booking, agoda, expedia, ... or Google's site name
    kind: str                 # cash | points
    total: float | None = None
    points: int | None = None
    room: str | None = None
    rate_name: str | None = None
    #: None when the source does not say; such a rate never alerts
    refundable: bool | None = None
    award_type: str | None = None   # standard | premium, points rows only
    url: str | None = None


@dataclass
class HotelResult:
    stay: HotelStay
    status: str               # ok | no_rates
    rates: list[HotelRate]
    note: str | None = None
```

- [ ] **Step 5: Add the config schema and loader** (in `vacation_planner/config.py`)

Add to the imports line: `BookedRule, HotelSource, HotelWatch, PointsRule` from `.models`, and `from .calendar import child_age_on` — check `calendar.py` imports nothing from `config` (it imports only `models`), so there is no cycle.

After `class _Holidays`:

```python
class _HotelRule(BaseModel):
    type: Literal["booked", "points"]
    until: date
    paid_total: float | None = None
    min_saving_ratio: float = 0.10
    points_adults: int = 2
    surcharge: float = 0


class _HotelWatch(BaseModel):
    id: str
    name: str
    slot: str
    check_in: date
    check_out: date
    room: list[str] = []
    sources: dict[HotelSource, str]
    rule: _HotelRule


class _HiltonSettings(BaseModel):
    point_value_eur: float


class _Hotels(BaseModel):
    hotel_watches: list[_HotelWatch] = []
    hilton: _HiltonSettings | None = None


#: Google Hotels returns nothing for longer stays (tested 2026-11-03: 7 nights priced, 9+ empty).
GOOGLE_MAX_NIGHTS = 8
```

In `Secrets` add the field `apify_token: str | None = None` (last, with a default, so existing constructions keep working) and in `_secrets` add `apify_token=env.get("APIFY_TOKEN") or None,`.

In `Config` add, after `root: Path`:

```python
    hotels: list[HotelWatch] = field(default_factory=list)
    point_value_eur: float | None = None
```

and change the dataclass import at the top to `from dataclasses import dataclass, field`.

Add these functions above `load_config`:

```python
def guests_on(travellers: Travellers, day: date) -> tuple[int, tuple[int, ...]]:
    """(adults, child ages) on `day`; hotels price a child of 12 or more as an adult."""
    adults, ages = travellers.adults, []
    for c in travellers.children:
        age = child_age_on(c.birthdate, day)
        if age >= 12:
            adults += 1
        else:
            ages.append(age)
    return adults, tuple(ages)


def _hotel_watches(config_dir: Path, slot_ids: set[str]) -> tuple[list[HotelWatch], float | None]:
    path = config_dir / "hotels.yaml"
    if not path.exists():
        return [], None
    raw = _load(path, _Hotels)
    point_value = raw.hilton.point_value_eur if raw.hilton else None
    watches: list[HotelWatch] = []
    for w in raw.hotel_watches:
        where = f"hotels.yaml: watch {w.id}"
        if w.slot not in slot_ids:
            raise ConfigError(f"{where}: unknown slot {w.slot}")
        if w.check_out <= w.check_in:
            raise ConfigError(f"{where}: check_out must be after check_in")
        nights = (w.check_out - w.check_in).days
        if HotelSource.GOOGLE in w.sources and nights > GOOGLE_MAX_NIGHTS:
            raise ConfigError(f"{where}: Google Hotels prices at most {GOOGLE_MAX_NIGHTS} nights, this stay has {nights}")
        if w.rule.type == "booked":
            if w.rule.paid_total is None:
                raise ConfigError(f"{where}: booked rule needs paid_total")
            rule: BookedRule | PointsRule = BookedRule(w.rule.paid_total, w.rule.min_saving_ratio, w.rule.until)
        else:
            if HotelSource.HILTON not in w.sources:
                raise ConfigError(f"{where}: points rule needs a hilton source")
            if point_value is None:
                raise ConfigError(f"{where}: points rule needs hilton.point_value_eur")
            rule = PointsRule(w.rule.points_adults, w.rule.surcharge, w.rule.until)
        watches.append(HotelWatch(w.id, w.name, w.slot, w.check_in, w.check_out,
                                  tuple(w.room), dict(w.sources), rule))
    return watches, point_value
```

At the end of `load_config`, before `return Config(...)`: `hotels, point_value = _hotel_watches(config_dir, seen)` (`seen` already holds every slot id) and pass `hotels=hotels, point_value_eur=point_value` to `Config(...)`.

An invalid `rule.type` raises from `_load` as `hotels.yaml: hotel_watches.0.rule.type: Input should be 'booked' or 'points'` — the test matches on `rule`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_hotels_config.py -v && uv run pytest -q`
Expected: all PASS (the full suite still passes; `test_the_live_repo_config_loads` also loads the new `config/hotels.yaml`)

- [ ] **Step 7: Commit**

```bash
git add vacation_planner/models.py vacation_planner/config.py config/hotels.yaml tests/fixtures/config/hotels.yaml tests/test_hotels_config.py
git commit -m "feat(hotels): hotel watch models and hotels.yaml config"
```

---

### Task 2: Storage for hotel checks, rates and alerts

**Files:**
- Create: `vacation_planner/migrations/003_hotels.sql`
- Modify: `vacation_planner/storage.py`
- Test: `tests/test_hotels_storage.py`

**Interfaces:**
- Consumes: `HotelStay`, `HotelResult`, `HotelRate`, `HotelSource` (Task 1)
- Produces (all on `Storage`):
  - `save_hotel_result(batch: str, result: HotelResult, now: datetime) -> int`
  - `record_hotel_check(batch: str, stay: HotelStay, status: str, now: datetime, error: str | None = None) -> int` — `status` `"error" | "skipped"`
  - `hotel_batches(watch_id: str) -> list[str]` — newest first
  - `hotel_checks(watch_id: str, batch: str) -> list[HotelCheckRow]`
  - `hotel_rates(watch_id: str, batch: str) -> list[StoredHotelRate]`
  - `insert_hotel_alert(watch_id: str, batch: str, reason: str, value: float | None, threshold: float | None, headline: str, now: datetime) -> int`
  - `last_hotel_alert(watch_id: str, reason: str) -> HotelAlertRow | None`
  - `pending_hotel_alerts() -> list[HotelAlertRow]`, `mark_hotel_alerts_notified(ids: list[int], now: datetime) -> None`
  - dataclasses `HotelCheckRow(id, batch, watch_id, source: HotelSource, mode, check_in: date, check_out: date, adults, child_ages: tuple[int, ...], status, error)`, `StoredHotelRate(check: HotelCheckRow, rate: HotelRate)`, `HotelAlertRow(id, watch_id, batch, reason, value, threshold, headline, detected_at: datetime, notified_at: datetime | None)`
  - `searches_by_provider_since(Provider.SERPAPI, since)` now also counts Google hotel checks with status `ok | no_rates | error`

`batch` is the run's timestamp as an ISO string (`now.isoformat()`); every check of one run shares it, so "this run's rates" is "the newest batch".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hotels_storage.py
from datetime import date, datetime, timedelta, timezone

from vacation_planner.models import HotelRate, HotelResult, HotelSource, HotelStay, Provider
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
STAY = HotelStay("ikos-pfingsten-2027", HotelSource.BOOKING, "https://www.booking.com/hotel/es/ikos-porto-petro.de.html",
                 "rates", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))
GOOGLE = HotelStay("ikos-pfingsten-2027", HotelSource.GOOGLE, "tok", "rooms", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def rate(total, room="Junior Suite mit eigenem Garten", refundable=True):
    return HotelRate("booking", "cash", total=total, room=room, rate_name="All-Inclusive", refundable=refundable,
                     url="https://www.booking.com/x")


def test_a_result_round_trips_with_its_rates():
    db = Storage(":memory:")
    batch = NOW.isoformat()
    db.save_hotel_result(batch, HotelResult(STAY, "ok", [rate(6897.96), rate(6503.58, "Junior Suite mit Gartenblick")]), NOW)
    rows = db.hotel_rates("ikos-pfingsten-2027", batch)
    assert [r.rate.total for r in rows] == [6897.96, 6503.58]
    assert rows[0].rate == rate(6897.96)
    assert rows[0].check.source is HotelSource.BOOKING and rows[0].check.child_ages == (8,)
    assert db.hotel_checks("ikos-pfingsten-2027", batch)[0].status == "ok"


def test_batches_are_listed_newest_first_per_watch():
    db = Storage(":memory:")
    for days in (0, 7, 14):
        t = NOW + timedelta(days=days)
        db.save_hotel_result(t.isoformat(), HotelResult(STAY, "ok", [rate(6000 - days)]), t)
    assert db.hotel_batches("ikos-pfingsten-2027") == [(NOW + timedelta(days=d)).isoformat() for d in (14, 7, 0)]
    assert db.hotel_batches("other") == []


def test_errors_and_no_rates_are_stored_without_rates():
    db = Storage(":memory:")
    b = NOW.isoformat()
    db.record_hotel_check(b, STAY, "error", NOW, error="apify 500")
    db.save_hotel_result(b, HotelResult(GOOGLE, "no_rates", [], "Google priced other dates"), NOW)
    checks = db.hotel_checks("ikos-pfingsten-2027", b)
    assert [(c.source, c.status, c.error) for c in checks] == [
        (HotelSource.BOOKING, "error", "apify 500"), (HotelSource.GOOGLE, "no_rates", "Google priced other dates")]
    assert db.hotel_rates("ikos-pfingsten-2027", b) == []


def test_google_hotel_checks_count_against_the_serpapi_budget():
    db = Storage(":memory:")
    b = NOW.isoformat()
    db.save_hotel_result(b, HotelResult(GOOGLE, "ok", []), NOW)
    db.record_hotel_check(b, GOOGLE, "error", NOW, error="x")
    db.record_hotel_check(b, GOOGLE, "skipped", NOW, error="no key")   # never sent: free
    db.save_hotel_result(b, HotelResult(STAY, "ok", []), NOW)          # Apify, not SerpApi
    assert db.searches_by_provider_since(Provider.SERPAPI, NOW - timedelta(days=1)) == 2
    assert db.searches_by_provider_since(Provider.SEARCHAPI, NOW - timedelta(days=1)) == 0


def test_alerts_are_pending_until_notified():
    db = Storage(":memory:")
    b = NOW.isoformat()
    a = db.insert_hotel_alert("ikos-pfingsten-2027", b, "cheaper_room", 4980.0, 5076.0, "Rebook: Booking.com 4,980 €", NOW)
    assert [x.id for x in db.pending_hotel_alerts()] == [a]
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cheaper_room").value == 4980.0
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cash_beats_points") is None
    db.mark_hotel_alerts_notified([a], NOW)
    assert db.pending_hotel_alerts() == []
    assert db.last_hotel_alert("ikos-pfingsten-2027", "cheaper_room").notified_at == NOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hotels_storage.py -v`
Expected: FAIL with `AttributeError: 'Storage' object has no attribute 'save_hotel_result'`

- [ ] **Step 3: Write the migration**

```sql
-- vacation_planner/migrations/003_hotels.sql
BEGIN;

-- One row per hotel query. `batch` is the run's timestamp: every check of one run
-- shares it, so the newest batch of a watch is "this run".
CREATE TABLE hotel_checks (
  id INTEGER PRIMARY KEY,
  batch TEXT NOT NULL,
  watch_id TEXT NOT NULL,
  source TEXT NOT NULL,
  mode TEXT NOT NULL,
  check_in TEXT NOT NULL,
  check_out TEXT NOT NULL,
  adults INTEGER NOT NULL,
  child_ages TEXT NOT NULL,          -- JSON list of ints
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL,              -- ok | no_rates | error | skipped
  error TEXT
);
CREATE INDEX idx_hotel_checks_watch ON hotel_checks(watch_id, batch);

CREATE TABLE hotel_rates (
  id INTEGER PRIMARY KEY,
  check_id INTEGER NOT NULL REFERENCES hotel_checks(id),
  channel TEXT NOT NULL,
  kind TEXT NOT NULL,                -- cash | points
  total REAL,
  points INTEGER,
  room TEXT,
  rate_name TEXT,
  refundable INTEGER,                -- 1, 0 or NULL (unknown)
  award_type TEXT,
  url TEXT
);
CREATE INDEX idx_hotel_rates_check ON hotel_rates(check_id);

CREATE TABLE hotel_alerts (
  id INTEGER PRIMARY KEY,
  watch_id TEXT NOT NULL,
  batch TEXT NOT NULL,
  reason TEXT NOT NULL,              -- cheaper_room | cash_beats_points | no_standard_reward
  value REAL,
  threshold REAL,
  headline TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  notified_at TEXT
);
CREATE INDEX idx_hotel_alerts_pending ON hotel_alerts(notified_at);

INSERT INTO schema_version (version) VALUES (3);

COMMIT;
```

- [ ] **Step 4: Add the storage code** (in `vacation_planner/storage.py`)

Extend the models import with `HotelRate, HotelResult, HotelSource, HotelStay`. Add dataclasses after `Observation`:

```python
@dataclass
class HotelCheckRow:
    id: int
    batch: str
    watch_id: str
    source: HotelSource
    mode: str
    check_in: date
    check_out: date
    adults: int
    child_ages: tuple[int, ...]
    status: str
    error: str | None

    @staticmethod
    def from_row(r: sqlite3.Row) -> "HotelCheckRow":
        return HotelCheckRow(r["id"], r["batch"], r["watch_id"], HotelSource(r["source"]), r["mode"],
                             date.fromisoformat(r["check_in"]), date.fromisoformat(r["check_out"]),
                             r["adults"], tuple(json.loads(r["child_ages"])), r["status"], r["error"])


@dataclass
class StoredHotelRate:
    check: HotelCheckRow
    rate: HotelRate


@dataclass
class HotelAlertRow:
    id: int
    watch_id: str
    batch: str
    reason: str
    value: float | None
    threshold: float | None
    headline: str
    detected_at: datetime
    notified_at: datetime | None

    @staticmethod
    def from_row(r: sqlite3.Row) -> "HotelAlertRow":
        return HotelAlertRow(r["id"], r["watch_id"], r["batch"], r["reason"], r["value"], r["threshold"],
                             r["headline"], _dt(r["detected_at"]), _dt(r["notified_at"]))


_HOTEL_CHECK_COLS = "id, batch, watch_id, source, mode, check_in, check_out, adults, child_ages, status, error"
```

Add methods to `Storage` (after `mark_notified`):

```python
    # ---- hotels ----
    def _insert_hotel_check(self, batch: str, stay: HotelStay, status: str, now: datetime,
                            error: str | None) -> int:
        cur = self.conn.execute(
            """INSERT INTO hotel_checks (batch, watch_id, source, mode, check_in, check_out, adults,
               child_ages, requested_at, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (batch, stay.watch_id, stay.source.value, stay.mode, stay.check_in.isoformat(),
             stay.check_out.isoformat(), stay.adults, json.dumps(list(stay.child_ages)), _iso(now), status, error))
        return cur.lastrowid

    def record_hotel_check(self, batch: str, stay: HotelStay, status: str, now: datetime,
                           error: str | None = None) -> int:
        with self.conn:
            return self._insert_hotel_check(batch, stay, status, now, error)

    def save_hotel_result(self, batch: str, result: HotelResult, now: datetime) -> int:
        with self.conn:   # the check and its rates commit together
            cid = self._insert_hotel_check(batch, result.stay, result.status, now, result.note)
            self.conn.executemany(
                """INSERT INTO hotel_rates (check_id, channel, kind, total, points, room, rate_name,
                   refundable, award_type, url) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(cid, r.channel, r.kind, r.total, r.points, r.room, r.rate_name,
                  None if r.refundable is None else int(r.refundable), r.award_type, r.url)
                 for r in result.rates])
        return cid

    def hotel_batches(self, watch_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT batch FROM hotel_checks WHERE watch_id=? ORDER BY batch DESC", (watch_id,))
        return [r["batch"] for r in rows]

    def hotel_checks(self, watch_id: str, batch: str) -> list[HotelCheckRow]:
        rows = self.conn.execute(
            f"SELECT {_HOTEL_CHECK_COLS} FROM hotel_checks WHERE watch_id=? AND batch=? ORDER BY id",
            (watch_id, batch))
        return [HotelCheckRow.from_row(r) for r in rows]

    def hotel_rates(self, watch_id: str, batch: str) -> list[StoredHotelRate]:
        checks = {c.id: c for c in self.hotel_checks(watch_id, batch)}
        if not checks:
            return []
        marks = ",".join("?" * len(checks))
        rows = self.conn.execute(
            f"""SELECT check_id, channel, kind, total, points, room, rate_name, refundable, award_type, url
                FROM hotel_rates WHERE check_id IN ({marks}) ORDER BY id""", tuple(checks))
        return [StoredHotelRate(checks[r["check_id"]], HotelRate(
            r["channel"], r["kind"], r["total"], r["points"], r["room"], r["rate_name"],
            None if r["refundable"] is None else bool(r["refundable"]), r["award_type"], r["url"]))
            for r in rows]

    def insert_hotel_alert(self, watch_id: str, batch: str, reason: str, value: float | None,
                           threshold: float | None, headline: str, now: datetime) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO hotel_alerts (watch_id, batch, reason, value, threshold, headline, detected_at)
                   VALUES (?,?,?,?,?,?,?)""", (watch_id, batch, reason, value, threshold, headline, _iso(now)))
        return cur.lastrowid

    def last_hotel_alert(self, watch_id: str, reason: str) -> HotelAlertRow | None:
        r = self.conn.execute(
            "SELECT * FROM hotel_alerts WHERE watch_id=? AND reason=? ORDER BY id DESC LIMIT 1",
            (watch_id, reason)).fetchone()
        return HotelAlertRow.from_row(r) if r else None

    def pending_hotel_alerts(self) -> list[HotelAlertRow]:
        rows = self.conn.execute("SELECT * FROM hotel_alerts WHERE notified_at IS NULL ORDER BY id")
        return [HotelAlertRow.from_row(r) for r in rows]

    def mark_hotel_alerts_notified(self, ids: list[int], now: datetime) -> None:
        with self.conn:
            self.conn.executemany("UPDATE hotel_alerts SET notified_at=? WHERE id=?", [(_iso(now), i) for i in ids])
```

Replace `searches_by_provider_since` with:

```python
    def searches_by_provider_since(self, provider: Provider, since: datetime) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS n FROM searches WHERE provider=? AND requested_at>=? AND status IN ('ok','error')",
            (provider.value, _iso(since))).fetchone()
        n = r["n"]
        if provider is Provider.SERPAPI:
            # Google Hotels queries go through the same SerpApi key and plan.
            h = self.conn.execute(
                """SELECT COUNT(*) AS n FROM hotel_checks WHERE source='google' AND requested_at>=?
                   AND status IN ('ok','no_rates','error')""", (_iso(since),)).fetchone()
            n += h["n"]
        return n
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_hotels_storage.py -v && uv run pytest -q`
Expected: all PASS. Also open the real database once to prove the migration applies to existing data: `uv run python -c "from vacation_planner.storage import Storage; Storage('data/planner.sqlite').close()"` must not raise — then `git checkout data/planner.sqlite` (do not commit a migrated DB from a local run; CI migrates it).

- [ ] **Step 6: Commit**

```bash
git add vacation_planner/migrations/003_hotels.sql vacation_planner/storage.py tests/test_hotels_storage.py
git commit -m "feat(hotels): storage for hotel checks, rates and alerts"
```

---

### Task 3: Hotel clients (Booking.com, Hilton, Google Hotels)

**Files:**
- Create: `vacation_planner/hotels/__init__.py` (empty), `vacation_planner/hotels/clients.py`
- Test: `tests/test_hotels_clients.py`
- Fixtures (already committed): `tests/fixtures/hotels/booking_ikos.json`, `hilton_waldorf_rates.json`, `hilton_waldorf_award_2a.json`, `hilton_waldorf_parity.json`, `hilton_mango_award_2a.json`, `hilton_mango_rates.json`, `google_ikos.json`, `google_waldorf_wrong_dates.json`

**Interfaces:**
- Consumes: `HotelStay`, `HotelRate`, `HotelResult`, `HotelSource` (Task 1); `ProviderError`, `AuthError`, `redact` from `vacation_planner/providers/base.py`
- Produces:
  - `class HotelClient(Protocol): source: HotelSource; def check(self, stay: HotelStay) -> HotelResult`
  - `ApifyActor(token: str, http: httpx.Client | None = None)` with `.run(actor: str, payload: dict) -> list[dict]`
  - `BookingClient(actor: ApifyActor)`, `HiltonClient(actor: ApifyActor)`, `GoogleHotelsClient(api_key: str, http: httpx.Client | None = None)`
  - module functions `parse_booking(items, stay) -> HotelResult`, `parse_hilton(rows, stay) -> HotelResult`, `parse_google(data, stay) -> HotelResult`, `booking_url(base: str, stay: HotelStay) -> str`
  - constants `BOOKING_ACTOR = "voyager~booking-scraper"`, `HILTON_ACTOR = "mu0i~hotel-rates-award-points"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hotels_clients.py
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.hotels.clients import (BOOKING_ACTOR, HILTON_ACTOR, ApifyActor, BookingClient,
                                             GoogleHotelsClient, HiltonClient, booking_url, parse_booking,
                                             parse_google, parse_hilton)
from vacation_planner.models import HotelSource, HotelStay
from vacation_planner.providers.base import AuthError, ProviderError

FIX = Path(__file__).parent / "fixtures" / "hotels"
BOOKING_URL = "https://www.booking.com/hotel/es/ikos-porto-petro.de.html"
IKOS = HotelStay("ikos-pfingsten-2027", HotelSource.BOOKING, BOOKING_URL, "rates",
                 date(2027, 5, 7), date(2027, 5, 14), 2, (8,))
IKOS_GOOGLE = HotelStay("ikos-pfingsten-2027", HotelSource.GOOGLE, "ChkI2u_o2PSToOZmGg0vZy8xMXQwbTM2Y3piEAE",
                        "rooms", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def waldorf(mode, adults=2, ages=(8,), source=HotelSource.HILTON, ref="SEZPIWA"):
    return HotelStay("waldorf-herbst-2027", source, ref, mode, date(2027, 10, 10), date(2027, 10, 15), adults, ages)


def load(name):
    return json.loads((FIX / name).read_text())


# ---- Booking.com ----

def test_booking_url_carries_dates_guests_and_child_ages():
    u = booking_url(BOOKING_URL, IKOS)
    assert u.startswith(BOOKING_URL + "?")
    for part in ("checkin=2027-05-07", "checkout=2027-05-14", "group_adults=2", "group_children=1",
                 "age=8", "no_rooms=1", "selected_currency=EUR"):
        assert part in u


def test_parse_booking_returns_every_room_with_its_refundability():
    res = parse_booking(load("booking_ikos.json"), IKOS)
    assert res.status == "ok"
    garden = [r for r in res.rates if r.room == "Junior Suite mit eigenem Garten"]
    assert len(garden) == 1
    g = garden[0]
    assert (g.channel, g.kind, round(g.total, 2), g.refundable) == ("booking", "cash", 6897.96, True)
    assert "Kostenlose Stornierung vor dem 7. April 2027" in g.rate_name
    assert len(res.rates) == 17


def test_parse_booking_rejects_other_dates():
    items = load("booking_ikos.json")
    items[0]["checkInDate"] = "2027-05-08"
    res = parse_booking(items, IKOS)
    assert res.status == "no_rates" and res.rates == [] and "other dates" in res.note


def test_parse_booking_with_nothing_found_is_no_rates():
    assert parse_booking([], IKOS).status == "no_rates"


# ---- Hilton ----

def test_parse_hilton_rates_keeps_cash_and_marks_advance_purchase_non_refundable():
    res = parse_hilton(load("hilton_waldorf_rates.json"), waldorf("rates"))
    assert res.status == "ok"
    assert all(r.kind == "cash" and r.channel == "direct" and r.total > 0 for r in res.rates)
    villa = {r.rate_name: r for r in res.rates if r.room == "One Bedroom Hawksbill Pool Villa"}
    assert villa["Honors Book Early and Save Breakfast Included"].total == 7402.5
    assert villa["Honors Book Early and Save Breakfast Included"].refundable is True
    # Hilton's data says refundable: true; an Advance Purchase rate is not.
    assert villa["Advance Purchase Breakfast Included"].refundable is False
    assert "Premium Room Rewards" not in villa   # a points plan with price_total 0 is not a cash rate


def test_parse_hilton_award_tells_standard_from_premium():
    res = parse_hilton(load("hilton_waldorf_award_2a.json"), waldorf("award", ages=()))
    std = [r for r in res.rates if r.award_type == "standard"]
    assert [(r.room, r.points) for r in std] == [("One Bedroom Hawksbill Garden Pool Villa", 850000)]
    assert all(r.kind == "points" for r in res.rates)
    assert {r.award_type for r in res.rates} == {"standard", "premium"}


def test_parse_hilton_parity_keeps_other_channels_and_unknown_refundability():
    res = parse_hilton(load("hilton_waldorf_parity.json"), waldorf("parity"))
    by = {r.channel: r for r in res.rates}
    assert set(by) == {"booking", "expedia", "hotelscom", "agoda"}   # direct is covered by `rates`; ctrip had no hotel
    assert (by["agoda"].total, by["agoda"].refundable) == (6470, False)
    assert by["expedia"].refundable is None


def test_parse_hilton_rejects_rows_for_other_guests_or_dates():
    rows = load("hilton_waldorf_rates.json")
    assert parse_hilton(rows, waldorf("rates", adults=3, ages=())).status == "no_rates"
    rows[0]["checkin"] = "2027-10-11"
    assert parse_hilton(rows, waldorf("rates")).status == "no_rates"


# ---- Google Hotels ----

def test_parse_google_reads_sites_and_rooms():
    res = parse_google(load("google_ikos.json"), IKOS_GOOGLE)
    assert res.status == "ok"
    rooms = {r.room for r in res.rates if r.room}
    assert "Junior Suite mit Gartenblick" in rooms
    assert any(r.channel == "Booking.com" and r.room is None and r.total == 6840 for r in res.rates)


def test_parse_google_detects_the_silent_wrong_date_fallback():
    stay = waldorf("rooms", source=HotelSource.GOOGLE, ref="ChkInpaOvMuKrLtZGg0vZy8xMWw3M3ZodDQ1EAE")
    res = parse_google(load("google_waldorf_wrong_dates.json"), stay)
    assert res.status == "no_rates" and res.rates == [] and "other dates" in res.note


def test_google_no_results_is_no_rates_not_an_error(httpx_mock):
    httpx_mock.add_response(json={"error": "Google Hotels hasn't returned any results for this query."})
    res = GoogleHotelsClient("KEY", http=httpx.Client()).check(IKOS_GOOGLE)
    assert res.status == "no_rates"


def test_google_auth_failure_is_an_auth_error_without_the_key(httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": "Invalid API key KEY"})
    with pytest.raises(AuthError) as e:
        GoogleHotelsClient("KEY", http=httpx.Client()).check(IKOS_GOOGLE)
    assert "KEY" not in str(e.value)


# ---- Apify ----

def test_apify_runs_the_actor_and_returns_its_items(httpx_mock):
    httpx_mock.add_response(
        url=f"https://api.apify.com/v2/acts/{BOOKING_ACTOR}/run-sync-get-dataset-items?token=TOKEN",
        status_code=201, json=load("booking_ikos.json"))
    res = BookingClient(ApifyActor("TOKEN", http=httpx.Client())).check(IKOS)
    assert res.status == "ok"
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["startUrls"][0]["url"] == booking_url(BOOKING_URL, IKOS)
    assert (body["adults"], body["children"], body["checkIn"], body["checkOut"]) == (2, 1, "2027-05-07", "2027-05-14")


def test_hilton_client_sends_mode_code_and_child_ages(httpx_mock):
    httpx_mock.add_response(status_code=201, json=load("hilton_waldorf_award_2a.json"))
    HiltonClient(ApifyActor("TOKEN", http=httpx.Client())).check(waldorf("award", ages=()))
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert (body["mode"], body["chain"], body["hotelCodes"], body["checkin"], body["los"], body["adults"],
            body["childrenAges"]) == ("award", "hilton", ["SEZPIWA"], "2027-10-10", 5, 2, [])
    assert httpx_mock.get_requests()[0].url.path == f"/v2/acts/{HILTON_ACTOR}/run-sync-get-dataset-items"


def test_a_failed_actor_run_is_a_provider_error_without_the_token(httpx_mock):
    httpx_mock.add_response(status_code=400, json={"error": {"type": "run-failed",
                                                            "message": "Actor run did not succeed (token TOKEN)"}})
    with pytest.raises(ProviderError) as e:
        BookingClient(ApifyActor("TOKEN", http=httpx.Client())).check(IKOS)
    assert "TOKEN" not in str(e.value) and "run-failed" in str(e.value)


def test_a_rejected_token_is_an_auth_error(httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": {"type": "user-or-token-not-found", "message": "x"}})
    with pytest.raises(AuthError):
        HiltonClient(ApifyActor("TOKEN", http=httpx.Client())).check(waldorf("rates"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hotels_clients.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vacation_planner.hotels'`

- [ ] **Step 3: Write the clients**

```python
# vacation_planner/hotels/clients.py
"""Hotel price sources: Booking.com and Hilton through Apify actors, Google Hotels through SerpApi.

Every parser checks that the response is for the requested dates and guests; anything else
is `no_rates`, never a price. Tested live 2026-11-03/05; the fixtures are those responses.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

import httpx

from ..models import HotelRate, HotelResult, HotelSource, HotelStay
from ..providers.base import AuthError, ProviderError, redact

APIFY_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
BOOKING_ACTOR = "voyager~booking-scraper"
HILTON_ACTOR = "mu0i~hotel-rates-award-points"
SERPAPI_URL = "https://serpapi.com/search.json"
#: An actor run is synchronous and a Booking.com page takes a while to load.
APIFY_TIMEOUT = 300
#: Google's wrong-date fallback prices one night; a real total is about nightly x nights.
TOTAL_TOLERANCE = 0.15


class HotelClient(Protocol):
    source: HotelSource

    def check(self, stay: HotelStay) -> HotelResult: ...


def _no_rates(stay: HotelStay, note: str) -> HotelResult:
    return HotelResult(stay, "no_rates", [], note)


def _non_refundable_name(name: str | None) -> bool:
    # Hilton reports its Advance Purchase plans as refundable; they are prepaid and are not.
    return "advance purchase" in (name or "").lower()


class ApifyActor:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self.token = token
        self.http = http or httpx.Client(timeout=APIFY_TIMEOUT)

    def run(self, actor: str, payload: dict) -> list[dict]:
        try:
            r = self.http.post(APIFY_URL.format(actor=actor), params={"token": self.token}, json=payload,
                               timeout=APIFY_TIMEOUT)
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"apify {actor}: network error: {e}", [self.token])) from e
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code in (401, 403):
            raise AuthError(redact(f"apify {actor}: {r.status_code} token rejected", [self.token]))
        if r.status_code >= 400 or not isinstance(body, list):
            err = (body or {}).get("error") if isinstance(body, dict) else None
            detail = f"{err.get('type')}: {err.get('message')}" if isinstance(err, dict) else str(err or r.status_code)
            raise ProviderError(redact(f"apify {actor}: {detail}", [self.token]))
        return body


# ---- Booking.com ----

def booking_url(base: str, stay: HotelStay) -> str:
    q = {"checkin": stay.check_in.isoformat(), "checkout": stay.check_out.isoformat(),
         "group_adults": stay.adults, "group_children": len(stay.child_ages), "no_rooms": 1,
         "selected_currency": "EUR", "lang": "de"}
    ages = "".join(f"&age={a}" for a in stay.child_ages)   # Booking repeats `age` once per child
    return f"{base}?{urlencode(q)}{ages}"


def parse_booking(items: list[dict], stay: HotelStay) -> HotelResult:
    if not items:
        return _no_rates(stay, "Booking.com returned no hotel")
    h = items[0]
    if (h.get("checkInDate"), h.get("checkOutDate")) != (stay.check_in.isoformat(), stay.check_out.isoformat()):
        return _no_rates(stay, f"Booking.com priced other dates ({h.get('checkInDate')}..{h.get('checkOutDate')})")
    rates = []
    for room in h.get("rooms") or []:
        for o in room.get("options") or []:
            if not o.get("price"):
                continue
            rates.append(HotelRate(
                "booking", "cash", total=round(float(o["price"]), 2), room=room.get("roomType"),
                rate_name=", ".join(o.get("yourChoices") or []) or None,
                refundable=bool(o.get("freeCancellation")), url=h.get("url")))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Booking.com: no room available")


class BookingClient:
    source = HotelSource.BOOKING

    def __init__(self, actor: ApifyActor):
        self.actor = actor

    def check(self, stay: HotelStay) -> HotelResult:
        payload = {"startUrls": [{"url": booking_url(stay.ref, stay)}], "maxItems": 1,
                   "checkIn": stay.check_in.isoformat(), "checkOut": stay.check_out.isoformat(),
                   "rooms": 1, "adults": stay.adults, "children": len(stay.child_ages),
                   "currency": "EUR", "language": "de"}
        return parse_booking(self.actor.run(BOOKING_ACTOR, payload), stay)


# ---- Hilton ----

def _matches_stay(row: dict, stay: HotelStay) -> bool:
    ages = row.get("children_ages")
    got = tuple(int(a) for a in str(ages).split(",")) if ages else ()
    return (row.get("checkin"), row.get("checkout"), row.get("adults"), got) == (
        stay.check_in.isoformat(), stay.check_out.isoformat(), stay.adults, stay.child_ages)


def parse_hilton(rows: list[dict], stay: HotelStay) -> HotelResult:
    rows = [r for r in rows if r.get("status") == "available"]
    if not rows:
        return _no_rates(stay, "Hilton: nothing available")
    if not all(_matches_stay(r, stay) for r in rows):
        return _no_rates(stay, "Hilton answered for other dates or guests")
    rates: list[HotelRate] = []
    for r in rows:
        if stay.mode == "award":
            if not r.get("points_total"):
                continue
            std = (r.get("rate_name") or "").lower().startswith("standard")
            rates.append(HotelRate("direct", "points", points=int(r["points_total"]), room=r.get("room_name"),
                                   rate_name=r.get("rate_name"), award_type="standard" if std else "premium"))
        elif stay.mode == "parity":
            if r.get("channel") == "direct" or not r.get("public_total"):
                continue   # the `rates` check prices direct room by room
            rates.append(HotelRate(r["channel"], "cash", total=float(r["public_total"]),
                                   rate_name=r.get("rate_name"), refundable=r.get("refundable")))
        else:   # rates
            if not r.get("price_total"):
                continue   # award plans appear here with price 0
            name = r.get("rate_name")
            refundable = bool(r.get("refundable")) and not _non_refundable_name(name)
            rates.append(HotelRate("direct", "cash", total=float(r["price_total"]), room=r.get("room_name"),
                                   rate_name=name, refundable=refundable))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Hilton: no priced rate")


class HiltonClient:
    source = HotelSource.HILTON

    def __init__(self, actor: ApifyActor):
        self.actor = actor

    def check(self, stay: HotelStay) -> HotelResult:
        payload = {"mode": stay.mode, "chain": "hilton", "hotelCodes": [stay.ref],
                   "checkin": stay.check_in.isoformat(), "los": stay.nights, "adults": stay.adults,
                   "childrenAges": list(stay.child_ages), "currency": "EUR", "maxCredits": 40}
        return parse_hilton(self.actor.run(HILTON_ACTOR, payload), stay)


# ---- Google Hotels (SerpApi) ----

def _lowest(block: dict | None) -> float | None:
    v = (block or {}).get("extracted_lowest")
    return float(v) if v else None


def parse_google(data: dict, stay: HotelStay) -> HotelResult:
    nightly, total = _lowest(data.get("rate_per_night")), _lowest(data.get("total_rate"))
    if nightly is None or total is None:
        return _no_rates(stay, "Google Hotels: no price")
    if stay.nights > 1 and abs(total - nightly * stay.nights) > TOTAL_TOLERANCE * nightly * stay.nights:
        return _no_rates(stay, f"Google priced other dates ({total:,.0f} total vs {nightly:,.0f} per night)")
    rates: list[HotelRate] = []
    for src in data.get("prices") or []:
        t = _lowest(src.get("total_rate"))
        if t:
            rates.append(HotelRate(src.get("source") or "google", "cash", total=t, url=src.get("link")))
    for src in data.get("featured_prices") or []:
        for room in src.get("rooms") or []:
            t = _lowest(room.get("total_rate"))
            if not t:
                continue
            free = [x.get("free_cancellation") for x in room.get("rates") or [] if "free_cancellation" in x]
            rates.append(HotelRate(src.get("source") or "google", "cash", total=t, room=room.get("name"),
                                   refundable=(any(free) if free else None), url=room.get("link")))
    return HotelResult(stay, "ok", rates) if rates else _no_rates(stay, "Google Hotels: no price")


class GoogleHotelsClient:
    source = HotelSource.GOOGLE

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=60)

    def check(self, stay: HotelStay) -> HotelResult:
        params = {"engine": "google_hotels", "q": stay.watch_id, "property_token": stay.ref,
                  "check_in_date": stay.check_in.isoformat(), "check_out_date": stay.check_out.isoformat(),
                  "adults": str(stay.adults), "currency": "EUR", "gl": "de", "hl": "de", "api_key": self.api_key}
        if stay.child_ages:
            params["children"] = str(len(stay.child_ages))
            params["children_ages"] = ",".join(str(a) for a in stay.child_ages)
        try:
            r = self.http.get(SERPAPI_URL, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"google hotels: network error: {e}", [self.api_key])) from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if r.status_code in (401, 403):
            raise AuthError(redact(f"google hotels {r.status_code}: {err}", [self.api_key]))
        if err and "hasn't returned any results" in err:
            return _no_rates(stay, "Google Hotels: no results")
        if r.status_code >= 400 or err:
            raise ProviderError(redact(f"google hotels {r.status_code}: {err}", [self.api_key]))
        return parse_google(body, stay)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hotels_clients.py -v`
Expected: all PASS. The fixtures are the source of truth: if a parser assertion fails, fix the parser's field names against the fixture, never the fixture.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/hotels/__init__.py vacation_planner/hotels/clients.py tests/test_hotels_clients.py tests/fixtures/hotels
git commit -m "feat(hotels): Booking.com, Hilton and Google Hotels clients with date guards"
```

---

### Task 4: Rules and the check runner

**Files:**
- Create: `vacation_planner/hotels/rules.py`, `vacation_planner/hotels/runner.py`
- Test: `tests/test_hotels_rules.py`, `tests/test_hotels_runner.py`

**Interfaces:**
- Consumes: `HotelWatch`, `BookedRule`, `PointsRule`, `HotelRate`, `HotelStay`, `HotelResult`, `HotelSource` (Task 1); `guests_on`, `Config` (Task 1); `Storage.save_hotel_result`, `record_hotel_check`, `hotel_rates`, `hotel_batches`, `insert_hotel_alert`, `last_hotel_alert` (Task 2); `HotelClient` (Task 3); `ProviderError` (`providers/base.py`); `money` (`report.py`)
- Produces:
  - `rules.Verdict(watch_id: str, state: str, headline: str, reason: str | None = None, value: float | None = None, threshold: float | None = None, best: HotelRate | None = None)`; `state` is `"alert" | "fine" | "no_rates" | "ended"`
  - `rules.evaluate_booked(watch, rates: list[HotelRate]) -> Verdict`
  - `rules.evaluate_points(watch, rates: list[HotelRate], point_value: float) -> Verdict`
  - `rules.evaluate(watch, rates, point_value: float | None, today: date) -> Verdict`
  - `runner.planned_stays(config: Config, today: date) -> list[HotelStay]`
  - `runner.HotelSummary(batch: str, ok: int = 0, no_rates: int = 0, errors: int = 0, skipped: int = 0, verdicts: list[Verdict], alerts: int = 0)`
  - `runner.check_hotels(config, storage, clients: Mapping[HotelSource, HotelClient], today: date, now: datetime) -> HotelSummary`
  - `runner.build_hotel_clients(config: Config) -> dict[HotelSource, HotelClient]`

- [ ] **Step 1: Write the failing rule tests**

```python
# tests/test_hotels_rules.py
from datetime import date

from vacation_planner.hotels.rules import evaluate, evaluate_booked, evaluate_points
from vacation_planner.models import BookedRule, HotelRate, HotelSource, HotelWatch, PointsRule

IKOS = HotelWatch("ikos-pfingsten-2027", "Ikos Porto Petro", "pfingsten-2027", date(2027, 5, 7), date(2027, 5, 14),
                  ("Junior Suite mit eigenem Garten",), {HotelSource.BOOKING: "u"},
                  BookedRule(5640, 0.10, date(2027, 4, 7)))
WALDORF = HotelWatch("waldorf-herbst-2027", "Waldorf Astoria Seychelles Platte Island", "herbst-2027",
                     date(2027, 10, 10), date(2027, 10, 15), (), {HotelSource.HILTON: "SEZPIWA"},
                     PointsRule(2, 350, date(2027, 10, 9)))
GARDEN = "Junior Suite mit eigenem Garten"


def cash(total, room=GARDEN, refundable=True, channel="booking", rate_name="All-Inclusive"):
    return HotelRate(channel, "cash", total=total, room=room, rate_name=rate_name, refundable=refundable)


def points(n, award_type="standard"):
    return HotelRate("direct", "points", points=n, room="Villa", award_type=award_type)


def test_booked_alerts_at_exactly_the_threshold():
    v = evaluate_booked(IKOS, [cash(5076)])
    assert (v.state, v.reason, v.value, v.threshold) == ("alert", "cheaper_room", 5076, 5076)
    assert v.headline == "Rebook: Junior Suite mit eigenem Garten 5,076 € on booking (you pay 5,640 €, alert at ≤ 5,076 €)"


def test_booked_does_not_alert_one_euro_above():
    v = evaluate_booked(IKOS, [cash(5077), cash(6898)])
    assert (v.state, v.value) == ("fine", 5077)
    assert v.headline == "Keep your booking: cheapest Junior Suite mit eigenem Garten 5,077 € on booking (you pay 5,640 €)"


def test_booked_ignores_other_rooms_and_non_refundable_rates():
    v = evaluate_booked(IKOS, [cash(4000, room="Junior Suite mit Gartenblick"), cash(4500, refundable=False),
                               cash(4600, refundable=None), cash(6000)])
    assert (v.state, v.value) == ("fine", 6000)
    assert "non-refundable 4,500 €" in v.headline


def test_booked_without_the_room_is_no_rates():
    v = evaluate_booked(IKOS, [cash(4000, room="Superior Doppelzimmer")])
    assert v.state == "no_rates" and v.headline == "Junior Suite mit eigenem Garten not offered this week"


def test_points_cost_is_standard_points_times_value_plus_surcharge():
    v = evaluate_points(WALDORF, [points(850000), points(3925000, "premium"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.threshold, v.value) == ("fine", 4090, 7402.5)
    assert v.headline == "Points still better: cash 7,402 € (direct) vs points 4,090 € (850,000 pts + 350 €)"   # f"{7402.5:,.0f}" rounds half to even


def test_points_alert_when_refundable_cash_is_cheaper():
    v = evaluate_points(WALDORF, [points(850000), cash(4089, channel="agoda")], 0.0044)
    assert (v.state, v.reason, v.value) == ("alert", "cash_beats_points", 4089)
    assert v.headline == "Pay cash: 4,089 € (agoda) vs points 4,090 € (850,000 pts + 350 €)"


def test_points_ignore_non_refundable_and_unknown_cash():
    v = evaluate_points(WALDORF, [points(850000), cash(3000, refundable=False, channel="agoda"),
                                  cash(3100, refundable=None, channel="expedia"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.value) == ("fine", 7402.5)


def test_only_premium_rewards_alert_once_as_no_standard_reward():
    v = evaluate_points(WALDORF, [points(3925000, "premium"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.reason) == ("alert", "no_standard_reward")
    assert v.headline == "Only Premium rewards this week — no Standard Room Reward for 2 adults"


def test_points_without_any_cash_still_reports_the_points_cost():
    v = evaluate_points(WALDORF, [points(850000)], 0.0044)
    assert (v.state, v.threshold) == ("no_rates", 4090)


def test_a_watch_past_until_has_ended():
    v = evaluate(IKOS, [cash(1000)], None, date(2027, 4, 8))
    assert (v.state, v.headline) == ("ended", "Watch ended on 7 Apr 2027")
    assert evaluate(IKOS, [cash(1000)], None, date(2027, 4, 7)).state == "alert"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_hotels_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vacation_planner.hotels.rules'`

- [ ] **Step 3: Write the rules**

```python
# vacation_planner/hotels/rules.py
"""Turn one batch of hotel rates into a verdict. Pure functions: no storage, no network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import BookedRule, HotelRate, HotelWatch, PointsRule
from ..report import money

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class Verdict:
    watch_id: str
    state: str                    # alert | fine | no_rates | ended
    headline: str
    reason: str | None = None     # cheaper_room | cash_beats_points | no_standard_reward
    value: float | None = None    # the cash price compared
    threshold: float | None = None
    best: HotelRate | None = None


def _refundable_cash(rates: list[HotelRate]) -> list[HotelRate]:
    return [r for r in rates if r.kind == "cash" and r.total and r.refundable is True]


def evaluate_booked(watch: HotelWatch, rates: list[HotelRate]) -> Verdict:
    rule = watch.rule
    assert isinstance(rule, BookedRule)
    room = watch.room[0] if watch.room else "your room"
    same = [r for r in rates if r.kind == "cash" and r.total and watch.matches_room(r.room)]
    refundable = [r for r in same if r.refundable is True]
    threshold = round(rule.paid_total * (1 - rule.min_saving_ratio), 2)
    if not refundable:
        return Verdict(watch.id, "no_rates", f"{room} not offered this week", threshold=threshold)
    best = min(refundable, key=lambda r: r.total)
    if best.total <= threshold:
        return Verdict(watch.id, "alert",
                       f"Rebook: {room} {money(best.total)} on {best.channel} "
                       f"(you pay {money(rule.paid_total)}, alert at ≤ {money(threshold)})",
                       "cheaper_room", best.total, threshold, best)
    headline = f"Keep your booking: cheapest {room} {money(best.total)} on {best.channel} (you pay {money(rule.paid_total)})"
    cheaper_fixed = [r for r in same if r.refundable is not True and r.total < best.total]
    if cheaper_fixed:
        headline += f"; non-refundable {money(min(r.total for r in cheaper_fixed))}"
    return Verdict(watch.id, "fine", headline, None, best.total, threshold, best)


def evaluate_points(watch: HotelWatch, rates: list[HotelRate], point_value: float) -> Verdict:
    rule = watch.rule
    assert isinstance(rule, PointsRule)
    standard = [r for r in rates if r.kind == "points" and r.award_type == "standard" and r.points]
    if not standard:
        return Verdict(watch.id, "alert",
                       f"Only Premium rewards this week — no Standard Room Reward for {rule.points_adults} adults",
                       "no_standard_reward")
    pts = min(r.points for r in standard)
    cost = round(pts * point_value + rule.surcharge, 2)
    detail = f"{pts:,} pts" + (f" + {money(rule.surcharge)}" if rule.surcharge else "")
    cash = _refundable_cash(rates)
    if not cash:
        return Verdict(watch.id, "no_rates", f"No refundable cash rate this week; points cost {money(cost)} ({detail})",
                       threshold=cost)
    best = min(cash, key=lambda r: r.total)
    if best.total < cost:
        return Verdict(watch.id, "alert", f"Pay cash: {money(best.total)} ({best.channel}) vs points {money(cost)} ({detail})",
                       "cash_beats_points", best.total, cost, best)
    return Verdict(watch.id, "fine", f"Points still better: cash {money(best.total)} ({best.channel}) vs points {money(cost)} ({detail})",
                   None, best.total, cost, best)


def evaluate(watch: HotelWatch, rates: list[HotelRate], point_value: float | None, today: date) -> Verdict:
    if today > watch.until:
        u = watch.until
        return Verdict(watch.id, "ended", f"Watch ended on {u.day} {MONTHS[u.month - 1]} {u.year}")
    if isinstance(watch.rule, BookedRule):
        return evaluate_booked(watch, rates)
    assert point_value is not None, "config validation guarantees a point value for points watches"
    return evaluate_points(watch, rates, point_value)
```

- [ ] **Step 4: Run the rule tests**

Run: `uv run pytest tests/test_hotels_rules.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing runner tests**

```python
# tests/test_hotels_runner.py
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.hotels.runner import build_hotel_clients, check_hotels, planned_stays
from vacation_planner.models import HotelRate, HotelResult, HotelSource
from vacation_planner.providers.base import ProviderError
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


class FakeHotelClient:
    """Answers every stay from `prices` (watch_id, mode) -> list[HotelRate]; raises for `failing` modes."""

    def __init__(self, source, prices=None, failing=()):
        self.source, self.prices, self.failing, self.calls = source, prices or {}, failing, []

    def check(self, stay):
        self.calls.append(stay)
        if stay.mode in self.failing:
            raise ProviderError(f"{self.source.value} {stay.mode} down")
        rates = self.prices.get((stay.watch_id, stay.mode), [])
        return HotelResult(stay, "ok" if rates else "no_rates", rates, None if rates else "nothing")


GARDEN = "Junior Suite mit eigenem Garten"


def clients(ikos_total=6898.0, waldorf_cash=7402.5, failing=()):
    booking = FakeHotelClient(HotelSource.BOOKING, {
        ("ikos-pfingsten-2027", "rates"): [HotelRate("booking", "cash", total=ikos_total, room=GARDEN, refundable=True)]})
    hilton = FakeHotelClient(HotelSource.HILTON, {
        ("waldorf-herbst-2027", "award"): [HotelRate("direct", "points", points=850000, room="V", award_type="standard")],
        ("waldorf-herbst-2027", "rates"): [HotelRate("direct", "cash", total=waldorf_cash, room="V", refundable=True)],
        ("mango-herbst-2027", "award"): [HotelRate("direct", "points", points=600000, room="K", award_type="standard")],
        ("mango-herbst-2027", "rates"): [HotelRate("direct", "cash", total=3850, room="K", refundable=True)],
    }, failing)
    google = FakeHotelClient(HotelSource.GOOGLE)
    return {HotelSource.BOOKING: booking, HotelSource.HILTON: hilton, HotelSource.GOOGLE: google}


def test_planned_stays_cover_every_source_and_the_points_guests(config_dir: Path):
    cfg = load_config(config_dir, env={})
    got = [(s.watch_id, s.source.value, s.mode, s.adults, s.child_ages) for s in planned_stays(cfg, TODAY)]
    assert got == [
        ("ikos-pfingsten-2027", "booking", "rates", 2, (8,)),
        ("ikos-pfingsten-2027", "google", "rooms", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "rates", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "parity", 2, (8,)),
        ("waldorf-herbst-2027", "hilton", "award", 2, ()),
        ("waldorf-herbst-2027", "google", "rooms", 2, (8,)),
        ("mango-herbst-2027", "hilton", "rates", 2, (8,)),
        ("mango-herbst-2027", "hilton", "parity", 2, (8,)),
        ("mango-herbst-2027", "hilton", "award", 2, ()),
        ("mango-herbst-2027", "google", "rooms", 2, (8,)),
    ]


def test_an_ended_watch_spends_nothing(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert [s.watch_id for s in planned_stays(cfg, date(2027, 4, 8))].count("ikos-pfingsten-2027") == 0
    db = Storage(":memory:")
    cl = clients()
    s = check_hotels(cfg, db, cl, date(2027, 4, 8), NOW)
    assert cl[HotelSource.BOOKING].calls == []
    assert {v.watch_id: v.state for v in s.verdicts}["ikos-pfingsten-2027"] == "ended"
    assert db.pending_hotel_alerts() == []


def test_a_quiet_week_stores_everything_and_alerts_nothing(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    s = check_hotels(cfg, db, clients(), TODAY, NOW)
    # ok: Ikos booking + Waldorf rates/award + Mango rates/award; the fake answers parity and Google empty
    assert (s.ok, s.no_rates, s.errors, s.skipped, s.alerts) == (5, 5, 0, 0, 0)
    assert {v.watch_id: v.state for v in s.verdicts} == {
        "ikos-pfingsten-2027": "fine", "waldorf-herbst-2027": "fine", "mango-herbst-2027": "fine"}
    assert db.hotel_batches("waldorf-herbst-2027") == [NOW.isoformat()]


def test_an_alert_is_stored_once_and_repeated_only_after_a_further_drop(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    check_hotels(cfg, db, clients(ikos_total=5000), TODAY, NOW)
    assert [(a.watch_id, a.reason, a.value) for a in db.pending_hotel_alerts()] == [
        ("ikos-pfingsten-2027", "cheaper_room", 5000)]
    later = NOW + timedelta(days=7)
    check_hotels(cfg, db, clients(ikos_total=4900), later.date(), later)    # only 2 % lower: quiet
    assert len(db.pending_hotel_alerts()) == 1
    latest = NOW + timedelta(days=14)
    check_hotels(cfg, db, clients(ikos_total=4700), latest.date(), latest)  # 6 % below the alerted 5,000
    assert [a.value for a in db.pending_hotel_alerts()] == [5000, 4700]


def test_no_standard_reward_alerts_once(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    cl = clients()
    del cl[HotelSource.HILTON].prices[("mango-herbst-2027", "award")]
    check_hotels(cfg, db, cl, TODAY, NOW)
    later = NOW + timedelta(days=7)
    check_hotels(cfg, db, cl, later.date(), later)
    assert [a.reason for a in db.pending_hotel_alerts()] == ["no_standard_reward"]


def test_a_failing_source_is_stored_as_error_and_the_rest_still_runs(config_dir: Path):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    s = check_hotels(cfg, db, clients(failing=("parity",)), TODAY, NOW)
    assert s.errors == 2
    errs = [c for c in db.hotel_checks("waldorf-herbst-2027", NOW.isoformat()) if c.status == "error"]
    assert [(c.mode, c.error) for c in errs] == [("parity", "hilton parity down")]
    assert {v.watch_id: v.state for v in s.verdicts}["waldorf-herbst-2027"] == "fine"


def test_an_unexpected_client_bug_is_contained(config_dir: Path):
    cfg = load_config(config_dir, env={})
    cl = clients()

    def boom(stay):
        raise KeyError("rooms")

    cl[HotelSource.BOOKING].check = boom
    s = check_hotels(cfg, Storage(":memory:"), cl, TODAY, NOW)
    assert s.errors == 1


def test_without_apify_token_booking_and_hilton_are_skipped_and_google_runs(config_dir: Path):
    cfg = load_config(config_dir, env={"SERPAPI_KEY": "k"})
    built = build_hotel_clients(cfg)
    assert set(built) == {HotelSource.GOOGLE}
    db = Storage(":memory:")
    s = check_hotels(cfg, db, {HotelSource.GOOGLE: FakeHotelClient(HotelSource.GOOGLE)}, TODAY, NOW)
    assert s.skipped == 7 and s.no_rates == 3
    skipped = [c for c in db.hotel_checks("ikos-pfingsten-2027", NOW.isoformat()) if c.status == "skipped"]
    assert [(c.source, c.error) for c in skipped] == [(HotelSource.BOOKING, "APIFY_TOKEN not set")]


def test_with_both_keys_all_three_clients_are_built(config_dir: Path):
    cfg = load_config(config_dir, env={"SERPAPI_KEY": "k", "APIFY_TOKEN": "t"})
    assert set(build_hotel_clients(cfg)) == {HotelSource.BOOKING, HotelSource.HILTON, HotelSource.GOOGLE}
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_hotels_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vacation_planner.hotels.runner'`

- [ ] **Step 7: Write the runner**

```python
# vacation_planner/hotels/runner.py
"""Run every active hotel watch: query each source, store the batch, judge it, store alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from ..config import Config, guests_on
from ..models import HotelSource, HotelStay, HotelWatch, PointsRule
from ..providers.base import ProviderError
from ..storage import Storage
from .clients import ApifyActor, BookingClient, GoogleHotelsClient, HiltonClient, HotelClient
from .rules import Verdict, evaluate

log = logging.getLogger(__name__)

#: Which env var a missing client needs, for the "skipped" note.
NEEDS = {HotelSource.BOOKING: "APIFY_TOKEN", HotelSource.HILTON: "APIFY_TOKEN", HotelSource.GOOGLE: "SERPAPI_KEY"}


@dataclass
class HotelSummary:
    batch: str
    ok: int = 0
    no_rates: int = 0
    errors: int = 0
    skipped: int = 0
    verdicts: list[Verdict] = field(default_factory=list)
    alerts: int = 0


def _stays(watch: HotelWatch, config: Config) -> list[HotelStay]:
    adults, ages = guests_on(config.travellers, watch.check_in)

    def stay(source: HotelSource, mode: str, a: int = adults, ch: tuple[int, ...] = ages) -> HotelStay:
        return HotelStay(watch.id, source, watch.sources[source], mode, watch.check_in, watch.check_out, a, ch)

    out: list[HotelStay] = []
    if HotelSource.BOOKING in watch.sources:
        out.append(stay(HotelSource.BOOKING, "rates"))
    if HotelSource.HILTON in watch.sources:
        out += [stay(HotelSource.HILTON, "rates"), stay(HotelSource.HILTON, "parity")]
        if isinstance(watch.rule, PointsRule):
            out.append(stay(HotelSource.HILTON, "award", watch.rule.points_adults, ()))
    if HotelSource.GOOGLE in watch.sources:
        out.append(stay(HotelSource.GOOGLE, "rooms"))
    return out


def planned_stays(config: Config, today: date) -> list[HotelStay]:
    return [s for w in config.hotels if today <= w.until for s in _stays(w, config)]


def build_hotel_clients(config: Config) -> dict[HotelSource, HotelClient]:
    clients: dict[HotelSource, HotelClient] = {}
    if config.secrets.apify_token:
        actor = ApifyActor(config.secrets.apify_token)
        clients[HotelSource.BOOKING] = BookingClient(actor)
        clients[HotelSource.HILTON] = HiltonClient(actor)
    if config.secrets.serpapi_key:
        clients[HotelSource.GOOGLE] = GoogleHotelsClient(config.secrets.serpapi_key)
    return clients


def _maybe_alert(storage: Storage, config: Config, batch: str, v: Verdict, now: datetime) -> bool:
    if v.state != "alert" or v.reason is None:
        return False
    last = storage.last_hotel_alert(v.watch_id, v.reason)
    if last is not None:
        if v.value is None or last.value is None:
            return False   # "no standard reward" says the same thing every week
        if v.value > last.value * config.settings.deals.renotify_drop_ratio:
            return False
    storage.insert_hotel_alert(v.watch_id, batch, v.reason, v.value, v.threshold, v.headline, now)
    return True


def check_hotels(config: Config, storage: Storage, clients: Mapping[HotelSource, HotelClient],
                 today: date, now: datetime) -> HotelSummary:
    batch = now.isoformat()
    summary = HotelSummary(batch)
    for watch in config.hotels:
        if today > watch.until:
            summary.verdicts.append(evaluate(watch, [], config.point_value_eur, today))
            continue
        for stay in _stays(watch, config):
            client = clients.get(stay.source)
            if client is None:
                storage.record_hotel_check(batch, stay, "skipped", now, error=f"{NEEDS[stay.source]} not set")
                summary.skipped += 1
                continue
            try:
                result = client.check(stay)
            except ProviderError as e:
                storage.record_hotel_check(batch, stay, "error", now, error=str(e))
                summary.errors += 1
                continue
            except Exception as e:   # a parser bug must not take the run down
                log.exception("hotel check %s %s %s failed", stay.watch_id, stay.source.value, stay.mode)
                storage.record_hotel_check(batch, stay, "error", now, error=f"unexpected {type(e).__name__}: {e}")
                summary.errors += 1
                continue
            storage.save_hotel_result(batch, result, now)
            if result.status == "ok":
                summary.ok += 1
            else:
                summary.no_rates += 1
        rates = [r.rate for r in storage.hotel_rates(watch.id, batch)]
        verdict = evaluate(watch, rates, config.point_value_eur, today)
        summary.verdicts.append(verdict)
        summary.alerts += _maybe_alert(storage, config, batch, verdict, now)
    return summary
```

- [ ] **Step 8: Run all hotel tests**

Run: `uv run pytest tests/test_hotels_rules.py tests/test_hotels_runner.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add vacation_planner/hotels/rules.py vacation_planner/hotels/runner.py tests/test_hotels_rules.py tests/test_hotels_runner.py
git commit -m "feat(hotels): booked and points rules, check runner with alert damping"
```

---

### Task 5: Budget reservation and CLI wiring

**Files:**
- Modify: `vacation_planner/planner.py` (`provider_shares`, `plan`)
- Modify: `vacation_planner/cli.py` (`plan_cmd`, new `hotels` command, `run`)
- Modify: `.github/workflows/scan.yml` (env of "Run planner")
- Test: `tests/test_planner.py` (append), `tests/test_hotels_cli.py`

**Interfaces:**
- Consumes: `planned_stays`, `check_hotels`, `build_hotel_clients`, `HotelSummary` (Task 4)
- Produces: `plan(config, storage, today, reserved: Mapping[Provider, int] | None = None)`; `provider_shares(config, storage, today, reserved=None)`; CLI `vacation-planner hotels`; `run` prints `hotels: <ok> ok, <no_rates> no rates, <errors> error, <skipped> skipped; alerts: <n>` and one line per verdict

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_planner.py` (its imports already include `provider_shares`, `Provider`, `Storage`; `TODAY = date(2026, 9, 21)` and `load_config_dir()` are defined at the top):

```python
def test_reserved_serpapi_searches_come_off_the_flight_share():
    cfg = load_config_dir()
    assert provider_shares(cfg, Storage(":memory:"), TODAY, reserved={Provider.SERPAPI: 3}) == [
        (Provider.SERPAPI, 59), (Provider.SEARCHAPI, 25), (Provider.FAST_FLIGHTS, None)]


def test_a_reservation_never_drives_a_share_below_zero():
    cfg = load_config_dir()
    shares = dict(provider_shares(cfg, Storage(":memory:"), TODAY, reserved={Provider.SERPAPI: 500}))
    assert shares[Provider.SERPAPI] == 0
```

```python
# tests/test_hotels_cli.py
from pathlib import Path

from typer.testing import CliRunner

from vacation_planner.cli import app

runner = CliRunner()


def test_plan_lists_the_hotel_checks_without_calling_anything(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "plan"])
    assert r.exit_code == 0, r.output
    assert "hotel   ikos-pfingsten-2027      booking  rates   2027-05-07..2027-05-14 2A (8)" in r.output
    assert "hotel   waldorf-herbst-2027      hilton   award   2027-10-10..2027-10-15 2A" in r.output
    assert "10 hotel checks (3 via SerpApi)" in r.output


def test_fake_run_skips_hotels(config_dir: Path, tmp_path: Path):
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "run", "--fake"])
    assert r.exit_code == 0, r.output
    assert "hotels: skipped (fake run)" in r.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_hotels_cli.py tests/test_planner.py -v`
Expected: FAIL (`provider_shares() got an unexpected keyword argument 'reserved'`; plan output lacks hotel lines)

- [ ] **Step 3: Planner reservation** (in `vacation_planner/planner.py`)

Change the import line to `from typing import Mapping` (add) and update:

```python
def provider_shares(config: Config, storage: Storage, today: date,
                    reserved: Mapping[Provider, int] | None = None) -> list[tuple[Provider, int | None]]:
    """... (keep the existing docstring) ...

    `reserved` holds searches another part of the run (the hotel checks) will make on a
    provider; they come off its share first so the flight plan cannot spend them.
    """
    runs = config.settings.budget.runs_per_month
    month_start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    shares: list[tuple[Provider, int | None]] = []
    for e in config.settings.providers.order:
        if e.monthly_budget is None:
            shares.append((e.name, None))
            continue
        used = storage.searches_by_provider_since(e.name, month_start)
        share = max(0, min(max(1, e.monthly_budget // runs), e.monthly_budget - used))
        shares.append((e.name, max(0, share - (reserved or {}).get(e.name, 0))))
    return shares
```

and in `plan(...)` add the parameter `reserved: Mapping[Provider, int] | None = None` and call `provider_shares(config, storage, today, reserved)`.

- [ ] **Step 4: CLI** (in `vacation_planner/cli.py`)

Add imports: `from .hotels.runner import HotelSummary, build_hotel_clients, check_hotels, planned_stays` and `from .models import HotelSource, Provider`.

Add helpers:

```python
def _hotel_reservation(config: Config, today: date) -> dict[Provider, int]:
    google = sum(1 for s in planned_stays(config, today) if s.source is HotelSource.GOOGLE)
    return {Provider.SERPAPI: google} if google and config.secrets.serpapi_key else {}


def _print_hotels(s: HotelSummary) -> None:
    typer.echo(f"hotels: {s.ok} ok, {s.no_rates} no rates, {s.errors} error, {s.skipped} skipped; alerts: {s.alerts}")
    for v in s.verdicts:
        typer.echo(f"  {v.watch_id}: {v.headline}")
```

In `do_scan` change `planned = make_plan(config, storage, today)` to `planned = make_plan(config, storage, today, _hotel_reservation(config, today) if not fake else None)`.

In `plan_cmd`, after the flight lines and before the summary line:

```python
    planned = make_plan(c.config, storage, c.today, _hotel_reservation(c.config, c.today))
    ...   # existing flight lines and summary
    stays = planned_stays(c.config, c.today)
    for s in stays:
        ages = f" ({', '.join(map(str, s.child_ages))})" if s.child_ages else ""
        typer.echo(f"hotel   {s.watch_id:24} {s.source.value:8} {s.mode:7} {s.check_in}..{s.check_out} {s.adults}A{ages}")
    google = sum(1 for s in stays if s.source is HotelSource.GOOGLE)
    typer.echo(f"{len(stays)} hotel checks ({google} via SerpApi)")
```

(The existing first line `planned = make_plan(c.config, storage, c.today)` is replaced by the version with the reservation.)

New command:

```python
@app.command("hotels")
def hotels_cmd(ctx: typer.Context):
    """Check the hotel watches now and store the results (no report, no mail)."""
    c: Ctx = ctx.obj
    _print_hotels(check_hotels(c.config, _storage(c), build_hotel_clients(c.config), c.today, c.now))
```

In `run`, after `_print_summary(summary, deals)`:

```python
    if fake:
        typer.echo("hotels: skipped (fake run)")
    else:
        try:
            _print_hotels(check_hotels(c.config, storage, build_hotel_clients(c.config), c.today, c.now))
        except Exception as e:   # the flight results are already stored; never lose the report and mail
            log.exception("hotel checks failed")
            typer.echo(f"hotels: failed ({type(e).__name__}: {e})")
```

- [ ] **Step 5: CI** — in `.github/workflows/scan.yml`, step "Run planner", add under `env:` after `SEARCHAPI_KEY`:

```yaml
          APIFY_TOKEN: ${{ secrets.APIFY_TOKEN }}
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_hotels_cli.py tests/test_planner.py -v && uv run pytest -q`
Expected: all PASS. Then the dry run against the real config: `uv run vacation-planner --today 2026-11-16 plan` — expect the 7 flight searches from before (Catania + 6 Seychelles, now 3 fewer SerpApi slots available but still enough) followed by 10 hotel lines and `10 hotel checks (3 via SerpApi)`.

- [ ] **Step 7: Commit**

```bash
git add vacation_planner/planner.py vacation_planner/cli.py .github/workflows/scan.yml tests/test_planner.py tests/test_hotels_cli.py
git commit -m "feat(hotels): hotels command, plan listing, run step and SerpApi reservation"
```

---

### Task 6: Hotel cards in the report and the digest

**Files:**
- Create: `vacation_planner/hotels/cards.py`
- Modify: `vacation_planner/report.py` (`ReportData`, `build_report`), `vacation_planner/templates/index.html`
- Modify: `vacation_planner/notify.py` (`_Digest`, `_prepare`, `_subject`, `_html`, `_text`, `send_pending`)
- Test: `tests/test_hotels_cards.py`

**Interfaces:**
- Consumes: `Storage.hotel_batches`, `hotel_checks`, `hotel_rates`, `pending_hotel_alerts`, `mark_hotel_alerts_notified` (Task 2); `evaluate`, `Verdict` (Task 4); `money`, `change` (`report.py`); `_dates` (`notify.py` — move it, see Step 3)
- Produces:
  - `cards.HotelRow(label: str, detail: str, price: str, change: str, change_class: str, url: str | None)`
  - `cards.HotelCard(watch_id: str, name: str, dates: str, verdict: Verdict, rows: list[HotelRow], problems: list[str])`
  - `cards.build_hotel_cards(storage: Storage, config: Config, today: date) -> list[HotelCard]`
  - `ReportData.hotels: list[HotelCard]` (default empty)

**What a card shows** (latest batch of the watch; "vs last week" compares with the batch before):

- one row per channel that has a relevant cash rate: for a booked watch the cheapest rate of the watched room on that channel (detail: room · "free cancellation"/"non-refundable"); for a points watch the cheapest cash rate on that channel (detail: room or rate name · refundability)
- for a points watch, one more row "Hilton points": the cheapest Standard award (detail: room · `850,000 pts`), price = points cost in EUR
- `problems`: one line per check of the latest batch with status `error`, `skipped` or `no_rates` — e.g. `Google Hotels: Google priced other dates (1,641 total vs 1,641 per night)`, `Booking.com: skipped (APIFY_TOKEN not set)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hotels_cards.py
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.hotels.cards import build_hotel_cards
from vacation_planner.models import HotelRate, HotelResult, HotelSource, HotelStay
from vacation_planner.notify import compose_digest
from vacation_planner.report import build_report, render
from vacation_planner.storage import Storage

NOW = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)
GARDEN = "Junior Suite mit eigenem Garten"


def ikos_stay(source=HotelSource.BOOKING, mode="rates"):
    from datetime import date
    return HotelStay("ikos-pfingsten-2027", source, "u", mode, date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def seeded(config_dir, totals=(7100.0, 6898.0)):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    for i, total in enumerate(totals):
        t = NOW - timedelta(days=7 * (len(totals) - 1 - i))
        db.save_hotel_result(t.isoformat(), HotelResult(ikos_stay(), "ok", [
            HotelRate("booking", "cash", total=total, room=GARDEN, rate_name="All-Inclusive", refundable=True,
                      url="https://www.booking.com/x"),
            HotelRate("booking", "cash", total=6503.58, room="Junior Suite mit Gartenblick", refundable=True)]), t)
        db.save_hotel_result(t.isoformat(), HotelResult(ikos_stay(HotelSource.GOOGLE, "rooms"), "no_rates", [],
                                                        "Google priced other dates"), t)
    return cfg, db


def test_card_shows_the_watched_room_per_channel_with_last_weeks_change(config_dir: Path):
    cfg, db = seeded(config_dir)
    card = next(c for c in build_hotel_cards(db, cfg, NOW.date()) if c.watch_id == "ikos-pfingsten-2027")
    assert card.name == "Ikos Porto Petro" and card.dates == "7–14 May 2027"
    assert card.verdict.state == "fine"
    assert [(r.label, r.price, r.change, r.change_class) for r in card.rows] == [("Booking.com", "6,898 €", "▼ 3 %", "down")]
    assert card.rows[0].detail == f"{GARDEN} · free cancellation"
    assert card.problems == ["Google Hotels: Google priced other dates"]


def test_watches_never_checked_have_no_card(config_dir: Path):
    cfg, db = seeded(config_dir)
    assert [c.watch_id for c in build_hotel_cards(db, cfg, NOW.date())] == ["ikos-pfingsten-2027"]


def test_report_index_has_a_hotels_section(config_dir: Path, tmp_path: Path):
    cfg, db = seeded(config_dir)
    render(db, cfg, tmp_path, NOW)
    html = (tmp_path / "index.html").read_text()
    assert "<h2>Hotels</h2>" in html and "Ikos Porto Petro" in html and "Keep your booking" in html


def test_digest_carries_hotel_verdicts_and_puts_alerts_first(config_dir: Path):
    cfg, db = seeded(config_dir, totals=(7100.0, 5000.0))
    db.insert_hotel_alert("ikos-pfingsten-2027", NOW.isoformat(), "cheaper_room", 5000, 5076,
                          "Rebook: Junior Suite mit eigenem Garten 5,000 € on booking (you pay 5,640 €, alert at ≤ 5,076 €)", NOW)
    data = build_report(db, cfg, NOW, None)
    subject, text, html = compose_digest(data, [], cfg, None, hotel_alerts=db.pending_hotel_alerts())
    assert subject.startswith("Vacation Planner · 1 hotel alert")
    assert text.index("Hotel alerts") < text.index("New deals")
    assert "Rebook: Junior Suite mit eigenem Garten 5,000 €" in text and "Ikos Porto Petro · 7–14 May 2027" in text
    assert "Hotel alerts" in html and "Hotels" in html


def test_quiet_digest_still_lists_the_hotel_verdict(config_dir: Path):
    cfg, db = seeded(config_dir)
    subject, text, _ = compose_digest(build_report(db, cfg, NOW, None), [], cfg, None)
    assert "Hotel alerts" not in text
    assert "Keep your booking: cheapest Junior Suite mit eigenem Garten 6,898 € on booking" in text
```

Also append to `tests/test_notify.py` (it already defines `ENV`, `NOW`, `FakeSMTP`, `set_mode`, and imports `load_config`, `Storage`, `send_pending`):

```python
def test_mode_deals_only_mails_for_a_hotel_alert_and_marks_it(config_dir):
    set_mode(config_dir, "deals_only")
    cfg = load_config(config_dir, env=ENV)
    db = Storage(":memory:")
    db.insert_hotel_alert("ikos-pfingsten-2027", NOW.isoformat(), "cheaper_room", 5000, 5076,
                          "Rebook: Junior Suite mit eigenem Garten 5,000 € on booking", NOW)
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP).sent is True
    assert FakeSMTP.instances[0].sent[0]["Subject"].startswith("Vacation Planner · 1 hotel alert")
    assert db.pending_hotel_alerts() == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_hotels_cards.py tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vacation_planner.hotels.cards'` (and the new notify test fails on the subject)

- [ ] **Step 3: Move `_dates` so cards can use it without importing notify**

Move `MONTHS`, `_day` and `_dates` from `vacation_planner/notify.py` into `vacation_planner/report.py` (as `MONTHS`, `day_label`, `date_range`), and in `notify.py` replace them with `from .report import MONTHS, day_label as _day, date_range as _dates`. Run `uv run pytest -q` — still green — before continuing. In `hotels/rules.py`, replace the local `MONTHS` tuple with `from ..report import MONTHS, money`.

- [ ] **Step 4: Write the cards module**

```python
# vacation_planner/hotels/cards.py
"""What the report and the digest show per hotel watch: verdict, one row per channel, problems."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import Config
from ..models import BookedRule, HotelRate, HotelWatch
from ..report import change, date_range, money
from ..storage import HotelCheckRow, Storage
from .rules import Verdict, evaluate

#: Channel ids the Hilton actor uses -> how the family knows them.
CHANNELS = {"direct": "Hilton direct", "booking": "Booking.com", "expedia": "Expedia",
            "hotelscom": "Hotels.com", "agoda": "Agoda", "ctrip": "Trip.com"}
SOURCES = {"booking": "Booking.com", "hilton": "Hilton", "google": "Google Hotels"}


@dataclass
class HotelRow:
    label: str
    detail: str
    price: str
    change: str
    change_class: str
    url: str | None


@dataclass
class HotelCard:
    watch_id: str
    name: str
    dates: str
    verdict: Verdict
    rows: list[HotelRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _refund_text(r: HotelRate) -> str:
    return {True: "free cancellation", False: "non-refundable"}.get(r.refundable, "cancellation unknown")


def _relevant_cash(watch: HotelWatch, rates: list[HotelRate]) -> dict[str, HotelRate]:
    """Cheapest relevant cash rate per channel."""
    best: dict[str, HotelRate] = {}
    for r in rates:
        if r.kind != "cash" or not r.total:
            continue
        if isinstance(watch.rule, BookedRule) and not watch.matches_room(r.room):
            continue
        if r.channel not in best or r.total < best[r.channel].total:
            best[r.channel] = r
    return best


def _points_cost(watch: HotelWatch, rates: list[HotelRate], point_value: float | None) -> tuple[float, HotelRate] | None:
    std = [r for r in rates if r.kind == "points" and r.award_type == "standard" and r.points]
    if not std or point_value is None:
        return None
    r = min(std, key=lambda x: x.points)
    return r.points * point_value + watch.rule.surcharge, r


def _problem(c: HotelCheckRow) -> str:
    src = SOURCES.get(c.source.value, c.source.value)
    label = src if c.mode in ("rates", "rooms") else f"{src} {c.mode}"
    if c.status == "skipped":
        return f"{label}: skipped ({c.error})"
    return f"{label}: {c.error or c.status}"


def _card(storage: Storage, config: Config, watch: HotelWatch, today: date) -> HotelCard | None:
    batches = storage.hotel_batches(watch.id)
    if not batches:
        return None
    rates = [s.rate for s in storage.hotel_rates(watch.id, batches[0])]
    before = [s.rate for s in storage.hotel_rates(watch.id, batches[1])] if len(batches) > 1 else []
    verdict = evaluate(watch, rates, config.point_value_eur, today)
    rows: list[HotelRow] = []
    previous = _relevant_cash(watch, before)
    for channel, r in sorted(_relevant_cash(watch, rates).items(), key=lambda kv: kv[1].total):
        prev = previous.get(channel)
        label, cls = change(prev.total if prev else None, r.total)
        detail = " · ".join(x for x in (r.room or r.rate_name, _refund_text(r)) if x)
        rows.append(HotelRow(CHANNELS.get(channel, channel), detail, money(r.total), label, cls, r.url))
    points = _points_cost(watch, rates, config.point_value_eur)
    if points:
        cost, r = points
        prev_points = _points_cost(watch, before, config.point_value_eur)
        label, cls = change(prev_points[0] if prev_points else None, cost)
        rows.append(HotelRow("Hilton points", f"{r.room} · {r.points:,} pts", money(cost), label, cls, None))
    problems = [_problem(c) for c in storage.hotel_checks(watch.id, batches[0]) if c.status != "ok"]
    return HotelCard(watch.id, watch.name, f"{date_range(watch.check_in, watch.check_out)} {watch.check_out.year}",
                     verdict, rows, problems)


def build_hotel_cards(storage: Storage, config: Config, today: date) -> list[HotelCard]:
    return [c for w in config.hotels if (c := _card(storage, config, w, today)) is not None]
```

- [ ] **Step 5: Report** — in `vacation_planner/report.py` add to `ReportData` the field `hotels: list = field(default_factory=list)   # HotelCard per watch`; at the end of `build_report`, before the `return`, build the cards with a local import (`hotels.cards` imports `report`, so a top-level import would be circular):

```python
    from .hotels.cards import build_hotel_cards   # local: hotels.cards imports this module
    hotels = build_hotel_cards(storage, config, now.date())
```

and pass `hotels=hotels` to the returned `ReportData(...)`.

In `templates/index.html`, insert after the "New deals" block (before `{% for slot, window, routes in data.slots %}`):

```html
{% if data.hotels %}
<h2>Hotels</h2>
{% for h in data.hotels %}
<h3>{{ h.name }} <span class="muted">· {{ h.dates }}</span></h3>
<p class="{{ 'deal' if h.verdict.state == 'alert' else '' }}">{{ h.verdict.headline }}</p>
{% if h.rows %}
<div class="wrap"><table>
<tr><th>Where</th><th>Room / rate</th><th class="num">Total</th><th class="num">vs last week</th><th></th></tr>
{% for r in h.rows %}
<tr><td>{{ r.label }}</td><td>{{ r.detail }}</td><td class="num">{{ r.price }}</td>
<td class="num{{ ' ' + r.change_class if r.change_class }}">{{ r.change }}</td>
<td>{% if r.url %}<a href="{{ r.url }}">Book</a>{% endif %}</td></tr>
{% endfor %}
</table></div>
{% endif %}
{% for p in h.problems %}<p class="muted">{{ p }}</p>{% endfor %}
{% endfor %}
{% endif %}
```

- [ ] **Step 6: Digest** — in `vacation_planner/notify.py`:

1. `_Digest` gains `hotel_alerts: list[_Deal] = field(default_factory=list)` and `hotels: list = field(default_factory=list)` (the `HotelCard`s).
2. `compose_digest(data, deals, config, report_url, hotel_alerts=None)` and `_prepare(..., hotel_alerts=None)`: build `alert_cards` from the alert rows:

```python
    by_id = {c.watch_id: c for c in data.hotels}
    alert_cards = [_Deal(title=f"{by_id[a.watch_id].name} · {by_id[a.watch_id].dates}" if a.watch_id in by_id else a.watch_id,
                         price=a.headline, why="", url=(by_id[a.watch_id].verdict.best.url or "")
                         if a.watch_id in by_id and by_id[a.watch_id].verdict.best else "")
                   for a in (hotel_alerts or [])]
```

   and pass `hotel_alerts=alert_cards, hotels=data.hotels` into `_Digest(...)`; the subject becomes `_subject(data, len(cards), len(alert_cards))`.
3. `_subject(data, cards, hotel_alerts=0)`: when `hotel_alerts`, the head is `f"{hotel_alerts} hotel alert{'' if hotel_alerts == 1 else 's'}"`, followed by `f" · {cards} new deal…"` when there are flight cards too; the rest unchanged.
4. `_html`: before the "New deals" heading, when `d.hotel_alerts`, add `<h2 style="{H2}">Hotel alerts</h2>` and one `CARD` per alert (title bold, headline below, a "Book" link when `url`). After the deals and before the holiday blocks, when `d.hotels`, add `<h2 style="{H2}">Hotels</h2>` and per card: `<p><b>{name}</b> · {dates}<br>{verdict.headline}</p>` followed by `<span style="{SUB}">` lines for each row (`{label}: {price} {change} — {detail}`) and each problem.
5. `_text`: the same two sections in the same places — `_heading("Hotel alerts")` with `title`, `  headline`, `  book: url`; `_heading("Hotels")` with `name · dates`, `  headline`, `    label: price (change) — detail` per row and `    problem` lines.
6. `send_pending`: read `alerts = storage.pending_hotel_alerts()` next to `pending`; in `deals_only` mode send when `pending or alerts`; pass `hotel_alerts=alerts` to `compose_digest`; after a successful send call `storage.mark_hotel_alerts_notified([a.id for a in alerts], now)` when there are any. `SendResult` stays as is.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_hotels_cards.py tests/test_notify.py tests/test_report.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add vacation_planner/hotels/cards.py vacation_planner/report.py vacation_planner/templates/index.html vacation_planner/notify.py tests/test_hotels_cards.py tests/test_notify.py
git commit -m "feat(hotels): hotel section in the report and the weekly digest"
```

---

### Task 7: Live smoke check and docs

**Files:**
- Modify: `README.md` (add a "Hotel watch" section)
- No code changes unless the smoke check finds a parser mismatch (then: fix the parser, add the new raw response under `tests/fixtures/hotels/` with secrets stripped, add a test, commit)

- [ ] **Step 1: Run the hotel checks live once** (costs ~3 SerpApi credits and a few Apify cents; writes to a scratch DB, not `data/planner.sqlite`)

Run: `uv run vacation-planner --db /tmp/hotel-smoke.sqlite hotels`
Expected output shape:

```
hotels: <n> ok, <n> no rates, 0 error, 0 skipped; alerts: 0
  ikos-pfingsten-2027: Keep your booking: cheapest Junior Suite mit eigenem Garten ~6,9xx € on booking (you pay 5,640 €)
  waldorf-herbst-2027: Points still better: cash ~7,4xx € (direct) vs points 4,090 € (850,000 pts + 350 €)
  mango-herbst-2027: Points still better: cash ~3,8xx € (direct) vs points 2,640 € (600,000 pts)
```

Any `error` line → read the stored error (`sqlite3 /tmp/hotel-smoke.sqlite "select source, mode, error from hotel_checks where status='error'"`) and fix before going on. Google for the Seychelles stays is expected to be `no rates` until roughly mid-November (date outside Google's window).

- [ ] **Step 2: Render the report from the smoke DB and look at it**

Run: `uv run vacation-planner --db /tmp/hotel-smoke.sqlite report` writes to `docs/site`; open `docs/site/index.html` and check the Hotels section, then `git checkout docs/site` (CI owns the published report).

- [ ] **Step 3: README** — add after the existing configuration section:

```markdown
## Hotel watch

`config/hotels.yaml` lists hotel stays the family has chosen. Every Monday run checks each one
and the digest carries one verdict per hotel:

- `booked` (Ikos Porto Petro): alerts when the same room type, refundable, costs at least
  `min_saving_ratio` less than `paid_total` — rebook and cancel the old booking. Ends at `until`
  (the free-cancellation deadline).
- `points` (Waldorf Astoria, Mango House): alerts when refundable cash for the whole family is
  cheaper than the Standard Room Reward for `points_adults` × `hilton.point_value_eur` + `surcharge`,
  or when no Standard Room Reward is offered.

Sources: Booking.com and Hilton (incl. Booking/Expedia/Hotels.com/Agoda prices for Hilton hotels)
through Apify (`APIFY_TOKEN`), Google Hotels through SerpApi (`SERPAPI_KEY`, at most 8 nights).
`vacation-planner plan` lists the checks; `vacation-planner hotels` runs them on their own.
```

- [ ] **Step 4: Full suite and commit**

Run: `uv run pytest -q`
Expected: all PASS.

```bash
git add README.md
git commit -m "docs: hotel watch in the README"
```
