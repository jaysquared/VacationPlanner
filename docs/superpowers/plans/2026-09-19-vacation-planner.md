# Vacation Planner Implementation Plan

> **Superseded in part:** the provider design (primary/backup, `serpapi_per_month`) was replaced by the ordered provider list in the design spec §2.5 on 2026-09-19. This plan is kept as history.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly GitHub Actions job that searches Google Flights for a Hamburg family's school-holiday trips, stores price history in SQLite, flags deals, publishes an HTML report and emails new deals.

**Architecture:** One Python package `vacation_planner` with independent stages (config, calendar, planner, providers, storage, deals, report, notify) wired by a typer CLI. Two flight providers (SerpApi primary, `fast-flights` backup) behind one `FlightClient` protocol. SQLite file and rendered HTML are committed back to the repo by CI.

**Tech Stack:** Python 3.12, uv, typer, pydantic v2, PyYAML, Jinja2, httpx, python-dotenv, fast-flights 3.1, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-19-vacation-planner-design.md`

## Global Constraints

- Python `>=3.12`; dependency management with `uv`; run everything via `uv run`.
- No live network calls in tests. Providers are tested against recorded fixtures and stubs.
- Every stage is a module with a small public function; stages never import each other's internals except through `models.py` and `storage.py`.
- Cabin mapping: `Destination.cabin == business` searches Business; `any` searches Economy. Never search a long-haul target in Economy.
- Excluded airlines (`settings.excluded_airlines`, seeded `[AI]`) must never appear in a stored offer, from either provider.
- Prices are stored as returned (`price_total`) with passenger counts; `per_person = price_total / (adults + children)`. Provider flag `price_is_total` defaults to `true` for both providers and is verified in Task 16.
- Money is stored as `REAL` EUR; dates as ISO strings; timestamps as ISO UTC strings.
- Commit after each task with the message given in the task. Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HvcAdtHaxgnxeMqykTiv9L
  ```

## File Structure

```
pyproject.toml                       project metadata, deps, pytest config
.gitignore                           (exists) .env, .venv, data/raw, caches
.env.example                         names of all env vars
config/travellers.yaml               adults + child birthdates
config/destinations.yaml             catalogue: code, name, cabin, max price
config/holidays.yaml                 Hamburg slots 2026/27-2029/30 with targets
config/settings.yaml                 origins, nights, budget, providers, deals, report, email
vacation_planner/__init__.py
vacation_planner/models.py           dataclasses + enums shared by all stages
vacation_planner/config.py           YAML -> pydantic -> Config; env secrets
vacation_planner/calendar.py         free_window, child_age_on, pax_for
vacation_planner/planner.py          plan(config, storage, today) -> list[PlannedSearch]
vacation_planner/storage.py          Storage class over sqlite3; runs migrations
vacation_planner/migrations/001_initial.sql
vacation_planner/providers/__init__.py
vacation_planner/providers/base.py   FlightClient protocol, errors
vacation_planner/providers/fake.py   FakeFlightClient for tests and dry runs
vacation_planner/providers/serpapi.py
vacation_planner/providers/fast_flights.py
vacation_planner/providers/executor.py  execute(plan, clients, storage, run_id, cfg)
vacation_planner/deals.py            detect_for_run(storage, run_id, config, now)
vacation_planner/report.py           render(storage, config, out_dir, now)
vacation_planner/templates/base.html, index.html, route.html
vacation_planner/notify.py           send_pending(storage, config, secrets, now)
vacation_planner/cli.py              typer app: plan, scan, report, notify, run, holidays
tests/conftest.py                    tmp config dir, seeded storage fixtures
tests/test_models.py … one test file per module, mirrors package
tests/fixtures/serpapi_ham_bkk.json  recorded SerpApi response (Task 8 builds a synthetic one, Task 16 replaces with real)
tests/fixtures/fast_flights_result.py  builder for a fast-flights ResultList
.github/workflows/scan.yml
README.md
```

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `vacation_planner/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`, `.env.example`, `README.md`

**Interfaces:**
- Produces: importable package `vacation_planner` with `__version__ = "0.1.0"`; `uv run pytest` works.

- [ ] **Step 1: Install uv if missing**

Run: `which uv || brew install uv`
Expected: a path to `uv`.

- [ ] **Step 2: Write pyproject.toml**

```toml
[project]
name = "vacation-planner"
version = "0.1.0"
description = "Flight deal scanner for Hamburg school holidays"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "pyyaml>=6",
    "jinja2>=3.1",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "fast-flights>=3.1,<4",
    "typing_extensions>=4.12",
]

[project.scripts]
vacation-planner = "vacation_planner.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-httpx>=0.30"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["vacation_planner"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Write package init and smoke test**

`vacation_planner/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

`tests/test_smoke.py`:
```python
import vacation_planner


def test_version():
    assert vacation_planner.__version__ == "0.1.0"
```

- [ ] **Step 4: Write .env.example and README**

`.env.example`:
```
SERPAPI_KEY=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
MAIL_FROM=
MAIL_TO=
```

`README.md`:
```markdown
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
```

- [ ] **Step 5: Install and run tests**

Run: `uv sync && uv run pytest -v`
Expected: `test_version PASSED`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock vacation_planner tests .env.example README.md
git commit -m "chore: scaffold vacation_planner package"
```

---

### Task 2: Domain models

**Files:**
- Create: `vacation_planner/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (used by every later task):

```python
class Cabin(str, Enum): ANY = "any"; BUSINESS = "business"
class SeatClass(str, Enum): ECONOMY = "economy"; BUSINESS = "business"
class Provider(str, Enum): SERPAPI = "serpapi"; FAST_FLIGHTS = "fast_flights"; FAKE = "fake"
class DealReason(str, Enum): BELOW_MEDIAN = "below_median"; GOOGLE_LOW = "google_low"; UNDER_MAX = "under_max"; NEW_LOW = "new_low"

@dataclass(frozen=True) class Nights: min: int; max: int
@dataclass(frozen=True) class Slot: id: str; name: str; start: date; end: date; targets: tuple[str, ...]; nights: Nights | None = None
@dataclass(frozen=True) class Destination: code: str; name: str; cabin: Cabin; max_price_per_person: float | None = None
@dataclass(frozen=True) class Child: birthdate: date
@dataclass(frozen=True) class Travellers: adults: int; children: tuple[Child, ...]
@dataclass(frozen=True) class SearchRequest: slot_id: str; origin: str; destination: str; outbound_date: date; return_date: date; seat: SeatClass; adults: int; children: int
    # property nights -> int, property pax -> int
@dataclass class Offer: provider: Provider; price_total: float; currency: str; per_person: float; airlines: list[str]; stops: int; duration_minutes: int; departs_at: str; arrives_at: str; price_level: str | None; typical_low: float | None; typical_high: float | None; google_url: str; raw: dict
@dataclass class SearchResult: request: SearchRequest; provider: Provider; offers: list[Offer]; raw_path: str | None = None
@dataclass(frozen=True) class PlannedSearch: request: SearchRequest; provider: Provider
def seat_for(cabin: Cabin) -> SeatClass
```

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
from datetime import date

from vacation_planner.models import (
    Cabin, SearchRequest, SeatClass, seat_for,
)


def make_request(**over):
    base = dict(
        slot_id="herbst-2026", origin="HAM", destination="BKK",
        outbound_date=date(2026, 10, 17), return_date=date(2026, 10, 31),
        seat=SeatClass.BUSINESS, adults=2, children=1,
    )
    base.update(over)
    return SearchRequest(**base)


def test_nights_and_pax():
    r = make_request()
    assert r.nights == 14
    assert r.pax == 3


def test_request_is_hashable_and_equal_by_value():
    assert make_request() == make_request()
    assert len({make_request(), make_request()}) == 1


def test_seat_for_cabin():
    assert seat_for(Cabin.BUSINESS) is SeatClass.BUSINESS
    assert seat_for(Cabin.ANY) is SeatClass.ECONOMY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: vacation_planner.models`.

- [ ] **Step 3: Write models.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Cabin(str, Enum):
    ANY = "any"
    BUSINESS = "business"


class SeatClass(str, Enum):
    ECONOMY = "economy"
    BUSINESS = "business"


class Provider(str, Enum):
    SERPAPI = "serpapi"
    FAST_FLIGHTS = "fast_flights"
    FAKE = "fake"


class DealReason(str, Enum):
    BELOW_MEDIAN = "below_median"
    GOOGLE_LOW = "google_low"
    UNDER_MAX = "under_max"
    NEW_LOW = "new_low"


def seat_for(cabin: Cabin) -> SeatClass:
    return SeatClass.BUSINESS if cabin is Cabin.BUSINESS else SeatClass.ECONOMY


@dataclass(frozen=True)
class Nights:
    min: int
    max: int


@dataclass(frozen=True)
class Slot:
    id: str
    name: str
    start: date
    end: date
    targets: tuple[str, ...]
    nights: Nights | None = None


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    cabin: Cabin
    max_price_per_person: float | None = None


@dataclass(frozen=True)
class Child:
    birthdate: date


@dataclass(frozen=True)
class Travellers:
    adults: int
    children: tuple[Child, ...]


@dataclass(frozen=True)
class SearchRequest:
    slot_id: str
    origin: str
    destination: str
    outbound_date: date
    return_date: date
    seat: SeatClass
    adults: int
    children: int

    @property
    def nights(self) -> int:
        return (self.return_date - self.outbound_date).days

    @property
    def pax(self) -> int:
        return self.adults + self.children


@dataclass
class Offer:
    provider: Provider
    price_total: float
    currency: str
    per_person: float
    airlines: list[str]
    stops: int
    duration_minutes: int
    departs_at: str
    arrives_at: str
    price_level: str | None
    typical_low: float | None
    typical_high: float | None
    google_url: str
    raw: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    request: SearchRequest
    provider: Provider
    offers: list[Offer]
    raw_path: str | None = None


@dataclass(frozen=True)
class PlannedSearch:
    request: SearchRequest
    provider: Provider
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/models.py tests/test_models.py
git commit -m "feat: add domain models"
```

---

### Task 3: Configuration loading and seed config files

**Files:**
- Create: `vacation_planner/config.py`, `config/travellers.yaml`, `config/destinations.yaml`, `config/holidays.yaml`, `config/settings.yaml`
- Test: `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `models.py` types.
- Produces:

```python
class BudgetSettings(BaseModel): serpapi_per_month: int; runs_per_month: int; max_searches_per_run: int; max_pairs_per_route_per_run: int
class ProviderSettings(BaseModel): primary: Provider; backup: Provider | None; backup_pause_seconds: float; price_is_total: bool = True
class DealSettings(BaseModel): median_ratio: float; min_history_points: int; renotify_drop_ratio: float; lookahead_days: int
class ReportSettings(BaseModel): output_dir: str
class EmailSettings(BaseModel): mode: Literal["deals_only", "always", "never"]
class BridgeDays(BaseModel): before: int; after: int
class Settings(BaseModel): origins: list[str]; nights: Nights; bridge_days: BridgeDays; max_stops: int; excluded_airlines: list[str]; providers: ProviderSettings; budget: BudgetSettings; deals: DealSettings; report: ReportSettings; email: EmailSettings
@dataclass class Secrets: serpapi_key: str | None; smtp_host: str | None; smtp_port: int; smtp_user: str | None; smtp_password: str | None; mail_from: str | None; mail_to: list[str]
@dataclass class Config: travellers: Travellers; destinations: dict[str, Destination]; slots: list[Slot]; settings: Settings; secrets: Secrets; root: Path
    # method destination(code) -> Destination
def load_config(config_dir: Path, env: Mapping[str, str] | None = None) -> Config   # raises ConfigError(str)
class ConfigError(Exception)
```

- [ ] **Step 1: Write the seed config files**

`config/travellers.yaml`:
```yaml
adults: 2
children:
  - birthdate: 2019-04-21
```

`config/destinations.yaml`:
```yaml
destinations:
  # Europe: any cabin
  - { code: LIS, name: Lisbon, cabin: any }
  - { code: PMI, name: Palma de Mallorca, cabin: any }
  - { code: ATH, name: Athens, cabin: any }
  - { code: FNC, name: Madeira, cabin: any }
  - { code: TFS, name: Tenerife South, cabin: any }
  - { code: LCA, name: Larnaca, cabin: any }
  # Long haul: Business only
  - { code: BKK, name: Bangkok, cabin: business, max_price_per_person: 2200 }
  - { code: HKT, name: Phuket, cabin: business, max_price_per_person: 2400 }
  - { code: DXB, name: Dubai, cabin: business, max_price_per_person: 1500 }
  - { code: MLE, name: Malé, cabin: business, max_price_per_person: 2800 }
  - { code: CPT, name: Cape Town, cabin: business, max_price_per_person: 2500 }
  - { code: MRU, name: Mauritius, cabin: business, max_price_per_person: 2800 }
  - { code: JFK, name: New York, cabin: business, max_price_per_person: 1900 }
  - { code: MIA, name: Miami, cabin: business, max_price_per_person: 2200 }
  - { code: CUN, name: Cancún, cabin: business, max_price_per_person: 2500 }
  - { code: NRT, name: Tokyo Narita, cabin: business, max_price_per_person: 2800 }
  - { code: SIN, name: Singapore, cabin: business, max_price_per_person: 2500 }
  - { code: DPS, name: Bali, cabin: business, max_price_per_person: 2900 }
```

`config/holidays.yaml` (dates from the official Ferienordnung; targets only on 2026/27 to start small):
```yaml
source: https://www.hamburg.de/resource/blob/134372/5bc131bdd36a604f67b361d21f7df37e/ferienordnung-hamburg-2024-2030-data.pdf
holidays:
  - { id: herbst-2026,      name: "Herbstferien 2026",           start: 2026-10-19, end: 2026-10-30, targets: [BKK] }
  - { id: weihnachten-2026, name: "Weihnachtsferien 2026/27",    start: 2026-12-21, end: 2027-01-01, targets: [MLE], nights: { min: 10, max: 14 } }
  - { id: fruehjahr-2027,   name: "Frühjahrsferien 2027",        start: 2027-03-01, end: 2027-03-12, targets: [DXB] }
  - { id: pfingsten-2027,   name: "Himmelfahrt/Pfingsten 2027",  start: 2027-05-07, end: 2027-05-14, targets: [PMI, LIS], nights: { min: 7, max: 9 } }
  - { id: sommer-2027,      name: "Sommerferien 2027",           start: 2027-07-01, end: 2027-08-11, targets: [JFK], nights: { min: 14, max: 21 } }
  - { id: herbst-2027,      name: "Herbstferien 2027",           start: 2027-10-11, end: 2027-10-22, targets: [] }
  - { id: weihnachten-2027, name: "Weihnachtsferien 2027/28",    start: 2027-12-20, end: 2027-12-31, targets: [] }
  - { id: fruehjahr-2028,   name: "Frühjahrsferien 2028",        start: 2028-03-06, end: 2028-03-17, targets: [] }
  - { id: pfingsten-2028,   name: "Himmelfahrt/Pfingsten 2028",  start: 2028-05-22, end: 2028-05-26, targets: [] }
  - { id: sommer-2028,      name: "Sommerferien 2028",           start: 2028-07-03, end: 2028-08-11, targets: [] }
  - { id: herbst-2028,      name: "Herbstferien 2028",           start: 2028-10-02, end: 2028-10-13, targets: [] }
  - { id: weihnachten-2028, name: "Weihnachtsferien 2028/29",    start: 2028-12-18, end: 2028-12-29, targets: [] }
  - { id: fruehjahr-2029,   name: "Frühjahrsferien 2029",        start: 2029-03-05, end: 2029-03-16, targets: [] }
  - { id: pfingsten-2029,   name: "Himmelfahrt/Pfingsten 2029",  start: 2029-05-11, end: 2029-05-18, targets: [] }
  - { id: sommer-2029,      name: "Sommerferien 2029",           start: 2029-07-02, end: 2029-08-10, targets: [] }
  - { id: herbst-2029,      name: "Herbstferien 2029",           start: 2029-10-01, end: 2029-10-12, targets: [] }
  - { id: weihnachten-2029, name: "Weihnachtsferien 2029/30",    start: 2029-12-21, end: 2030-01-04, targets: [] }
  - { id: fruehjahr-2030,   name: "Frühjahrsferien 2030",        start: 2030-03-04, end: 2030-03-15, targets: [] }
  - { id: pfingsten-2030,   name: "Himmelfahrt/Pfingsten 2030",  start: 2030-05-20, end: 2030-05-24, targets: [] }
  - { id: sommer-2030,      name: "Sommerferien 2030",           start: 2030-07-04, end: 2030-08-14, targets: [] }
```

`config/settings.yaml`:
```yaml
origins: [HAM]
nights: { min: 7, max: 14 }
bridge_days: { before: 0, after: 0 }
max_stops: 1
excluded_airlines: [AI]
providers:
  primary: serpapi
  backup: fast_flights
  backup_pause_seconds: 5
  price_is_total: true
budget:
  serpapi_per_month: 100
  runs_per_month: 4
  max_searches_per_run: 60
  max_pairs_per_route_per_run: 3
deals:
  median_ratio: 0.85
  min_history_points: 3
  renotify_drop_ratio: 0.95
  lookahead_days: 330
report:
  output_dir: docs/site
email:
  mode: deals_only
```

- [ ] **Step 2: Write conftest and failing tests**

`tests/conftest.py`:
```python
from pathlib import Path

import pytest

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copy of the repo config so tests can edit files freely."""
    dst = tmp_path / "config"
    dst.mkdir()
    for f in REPO_CONFIG.glob("*.yaml"):
        (dst / f.name).write_text(f.read_text())
    return dst
```

`tests/test_config.py`:
```python
from datetime import date
from pathlib import Path

import pytest

from vacation_planner.config import ConfigError, load_config
from vacation_planner.models import Cabin, Provider


def test_loads_repo_config(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert cfg.travellers.adults == 2
    assert cfg.travellers.children[0].birthdate == date(2019, 4, 21)
    assert cfg.destination("BKK").cabin is Cabin.BUSINESS
    assert cfg.destination("PMI").cabin is Cabin.ANY
    herbst = next(s for s in cfg.slots if s.id == "herbst-2026")
    assert herbst.start == date(2026, 10, 19) and herbst.targets == ("BKK",)
    assert cfg.settings.providers.primary is Provider.SERPAPI
    assert cfg.settings.budget.serpapi_per_month == 100
    assert cfg.secrets.serpapi_key is None


def test_secrets_from_env(config_dir: Path):
    env = {"SERPAPI_KEY": "k", "MAIL_TO": "a@x.de, b@x.de", "SMTP_PORT": "2525"}
    cfg = load_config(config_dir, env=env)
    assert cfg.secrets.serpapi_key == "k"
    assert cfg.secrets.mail_to == ["a@x.de", "b@x.de"]
    assert cfg.secrets.smtp_port == 2525


def test_unknown_target_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("targets: [BKK]", "targets: [XXX]", 1))
    with pytest.raises(ConfigError, match="XXX"):
        load_config(config_dir, env={})


def test_duplicate_slot_id_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("id: herbst-2027", "id: herbst-2026", 1))
    with pytest.raises(ConfigError, match="herbst-2026"):
        load_config(config_dir, env={})


def test_bad_yaml_value_names_file_and_key(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_stops: 1", "max_stops: many"))
    with pytest.raises(ConfigError, match="settings.yaml.*max_stops"):
        load_config(config_dir, env={})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: vacation_planner.config`.

- [ ] **Step 4: Write config.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Mapping

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from .models import Cabin, Child, Destination, Nights, Provider, Slot, Travellers


class ConfigError(Exception):
    pass


# ---- pydantic schemas (YAML shape) ----

class _Child(BaseModel):
    birthdate: date


class _Travellers(BaseModel):
    adults: int
    children: list[_Child] = []


class _Destination(BaseModel):
    code: str
    name: str
    cabin: Cabin
    max_price_per_person: float | None = None


class _Destinations(BaseModel):
    destinations: list[_Destination]


class _Nights(BaseModel):
    min: int
    max: int


class _Slot(BaseModel):
    id: str
    name: str
    start: date
    end: date
    targets: list[str] = []
    nights: _Nights | None = None


class _Holidays(BaseModel):
    source: str | None = None
    holidays: list[_Slot]


class BridgeDays(BaseModel):
    before: int = 0
    after: int = 0


class ProviderSettings(BaseModel):
    primary: Provider
    backup: Provider | None = None
    backup_pause_seconds: float = 5.0
    price_is_total: bool = True


class BudgetSettings(BaseModel):
    serpapi_per_month: int
    runs_per_month: int
    max_searches_per_run: int
    max_pairs_per_route_per_run: int

    @field_validator("runs_per_month")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v


class DealSettings(BaseModel):
    median_ratio: float
    min_history_points: int
    renotify_drop_ratio: float
    lookahead_days: int


class ReportSettings(BaseModel):
    output_dir: str = "docs/site"


class EmailSettings(BaseModel):
    mode: Literal["deals_only", "always", "never"] = "deals_only"


class Settings(BaseModel):
    origins: list[str]
    nights: _Nights
    bridge_days: BridgeDays = BridgeDays()
    max_stops: int = 1
    excluded_airlines: list[str] = []
    providers: ProviderSettings
    budget: BudgetSettings
    deals: DealSettings
    report: ReportSettings = ReportSettings()
    email: EmailSettings = EmailSettings()


# ---- runtime objects ----

@dataclass
class Secrets:
    serpapi_key: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    mail_from: str | None
    mail_to: list[str]


@dataclass
class Config:
    travellers: Travellers
    destinations: dict[str, Destination]
    slots: list[Slot]
    settings: Settings
    secrets: Secrets
    root: Path

    def destination(self, code: str) -> Destination:
        return self.destinations[code]


def _load(path: Path, model: type[BaseModel]) -> BaseModel:
    if not path.exists():
        raise ConfigError(f"{path.name}: file not found in {path.parent}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return model.model_validate(data)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: invalid YAML: {e}") from e
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ConfigError(f"{path.name}: {loc}: {first['msg']}") from e


def _secrets(env: Mapping[str, str]) -> Secrets:
    to = [x.strip() for x in env.get("MAIL_TO", "").split(",") if x.strip()]
    return Secrets(
        serpapi_key=env.get("SERPAPI_KEY") or None,
        smtp_host=env.get("SMTP_HOST") or None,
        smtp_port=int(env.get("SMTP_PORT") or 587),
        smtp_user=env.get("SMTP_USER") or None,
        smtp_password=env.get("SMTP_PASSWORD") or None,
        mail_from=env.get("MAIL_FROM") or None,
        mail_to=to,
    )


def load_config(config_dir: Path, env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        import os
        from dotenv import load_dotenv
        load_dotenv(config_dir.parent / ".env")
        env = os.environ

    trav = _load(config_dir / "travellers.yaml", _Travellers)
    dests = _load(config_dir / "destinations.yaml", _Destinations)
    hols = _load(config_dir / "holidays.yaml", _Holidays)
    settings = _load(config_dir / "settings.yaml", Settings)

    destinations: dict[str, Destination] = {}
    for d in dests.destinations:
        if d.code in destinations:
            raise ConfigError(f"destinations.yaml: duplicate code {d.code}")
        destinations[d.code] = Destination(d.code, d.name, d.cabin, d.max_price_per_person)

    slots: list[Slot] = []
    seen: set[str] = set()
    for s in hols.holidays:
        if s.id in seen:
            raise ConfigError(f"holidays.yaml: duplicate slot id {s.id}")
        seen.add(s.id)
        if s.end < s.start:
            raise ConfigError(f"holidays.yaml: slot {s.id} ends before it starts")
        for t in s.targets:
            if t not in destinations:
                raise ConfigError(f"holidays.yaml: slot {s.id} targets unknown destination {t}")
        nights = Nights(s.nights.min, s.nights.max) if s.nights else None
        slots.append(Slot(s.id, s.name, s.start, s.end, tuple(s.targets), nights))
    slots.sort(key=lambda s: s.start)

    travellers = Travellers(trav.adults, tuple(Child(c.birthdate) for c in trav.children))
    return Config(
        travellers=travellers,
        destinations=destinations,
        slots=slots,
        settings=settings,
        secrets=_secrets(env),
        root=config_dir.parent,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add config vacation_planner/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: config loading with seed Hamburg holidays and destinations"
```

---

### Task 4: Calendar helpers

**Files:**
- Create: `vacation_planner/calendar.py`
- Test: `tests/test_calendar.py`

**Interfaces:**
- Consumes: `Slot`, `Travellers`, `BridgeDays` (from config).
- Produces:

```python
@dataclass(frozen=True) class Window: start: date; end: date   # first and last free day, inclusive; property days -> int
def free_window(slot: Slot, before: int = 0, after: int = 0) -> Window
def child_age_on(birthdate: date, on: date) -> int
def pax_for(travellers: Travellers, on: date) -> tuple[int, int, int]  # adults, children(2-11), infants(<2)
```

- [ ] **Step 1: Write the failing tests**

`tests/test_calendar.py`:
```python
from datetime import date

from vacation_planner.calendar import Window, child_age_on, free_window, pax_for
from vacation_planner.models import Child, Slot, Travellers


def slot(start, end):
    return Slot("x", "x", start, end, ())


def test_monday_to_friday_extends_to_enclosing_weekend():
    w = free_window(slot(date(2026, 10, 19), date(2026, 10, 30)))  # Mon..Fri
    assert w == Window(date(2026, 10, 17), date(2026, 11, 1))       # Sat..Sun
    assert w.days == 16


def test_midweek_boundaries_are_not_extended():
    # Sommerferien 2027: Thu 1 Jul .. Wed 11 Aug
    w = free_window(slot(date(2027, 7, 1), date(2027, 8, 11)))
    assert w == Window(date(2027, 7, 1), date(2027, 8, 11))


def test_friday_start_is_kept_but_friday_end_extends():
    # Pfingsten 2027: Fri 7 May .. Fri 14 May
    w = free_window(slot(date(2027, 5, 7), date(2027, 5, 14)))
    assert w == Window(date(2027, 5, 7), date(2027, 5, 16))


def test_bridge_days_widen_window():
    w = free_window(slot(date(2026, 10, 19), date(2026, 10, 30)), before=2, after=1)
    assert w == Window(date(2026, 10, 15), date(2026, 11, 2))


def test_child_age_on_birthday_edges():
    b = date(2019, 4, 21)
    assert child_age_on(b, date(2027, 4, 20)) == 7
    assert child_age_on(b, date(2027, 4, 21)) == 8


def test_pax_for_buckets_children():
    t = Travellers(2, (Child(date(2019, 4, 21)), Child(date(2025, 1, 1)), Child(date(2013, 1, 1))))
    assert pax_for(t, date(2026, 10, 17)) == (3, 1, 1)   # 13yo counts as adult, 1yo infant
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write calendar.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Slot, Travellers

SATURDAY, SUNDAY, MONDAY, FRIDAY = 5, 6, 0, 4


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def free_window(slot: Slot, before: int = 0, after: int = 0) -> Window:
    start, end = slot.start, slot.end
    if start.weekday() == MONDAY:
        start -= timedelta(days=2)
    if end.weekday() == FRIDAY:
        end += timedelta(days=2)
    return Window(start - timedelta(days=before), end + timedelta(days=after))


def child_age_on(birthdate: date, on: date) -> int:
    age = on.year - birthdate.year
    if (on.month, on.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age


def pax_for(travellers: Travellers, on: date) -> tuple[int, int, int]:
    adults, children, infants = travellers.adults, 0, 0
    for c in travellers.children:
        age = child_age_on(c.birthdate, on)
        if age < 2:
            infants += 1
        elif age < 12:
            children += 1
        else:
            adults += 1
    return adults, children, infants
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_calendar.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/calendar.py tests/test_calendar.py
git commit -m "feat: school-free window and passenger age helpers"
```

---

### Task 5: SQLite storage

**Files:**
- Create: `vacation_planner/storage.py`, `vacation_planner/migrations/__init__.py` (empty), `vacation_planner/migrations/001_initial.sql`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `models.py`.
- Produces (all later tasks use only these):

```python
@dataclass class SearchRow: id: int; run_id: int; slot_id: str; origin: str; destination: str; outbound_date: date; return_date: date; seat: SeatClass; adults: int; children: int; provider: Provider; requested_at: datetime; status: str; error: str | None
@dataclass class OfferRow: id: int; search_id: int; provider: Provider; price_total: float; per_person: float; airlines: list[str]; stops: int; duration_minutes: int; departs_at: str; arrives_at: str; price_level: str | None; typical_low: float | None; typical_high: float | None; google_url: str
@dataclass class DealRow: id: int; offer_id: int; search_id: int; slot_id: str; reasons: list[DealReason]; score: float; detected_at: datetime; notifiable: bool; notified_at: datetime | None
@dataclass class Observation: search: SearchRow; offer: OfferRow

class Storage:
    def __init__(self, path: Path | str)          # ":memory:" allowed; runs migrations
    def close(self) -> None
    def start_run(self, now: datetime, planned: int) -> int
    def finish_run(self, run_id: int, executed: int, status: str, now: datetime) -> None
    def record_search(self, run_id: int, req: SearchRequest, provider: Provider, status: str, now: datetime, error: str | None = None, raw_path: str | None = None) -> int
    def record_offers(self, search_id: int, offers: list[Offer]) -> None
    def save_result(self, run_id: int, result: SearchResult, now: datetime) -> int   # search 'ok' + offers, one transaction
    def last_observed(self, req: SearchRequest) -> datetime | None
    def searches_by_provider_since(self, provider: Provider, since: datetime) -> int
    def searches_in_run(self, run_id: int, status: str = "ok") -> list[SearchRow]
    def cheapest_offer(self, search_id: int) -> OfferRow | None
    def prior_cheapest_prices(self, slot_id: str, origin: str, destination: str, seat: SeatClass, before_search_id: int) -> list[float]
    def prior_route_prices(self, origin: str, destination: str, seat: SeatClass, before_search_id: int) -> list[float]
    def insert_deal(self, offer_id: int, search_id: int, slot_id: str, reasons: list[DealReason], score: float, now: datetime, notifiable: bool) -> int
    def last_notified_price(self, slot_id: str, origin: str, destination: str, seat: SeatClass) -> float | None
    def deals_in_run(self, run_id: int) -> list[DealRow]
    def pending_deals(self) -> list[DealRow]                      # notifiable and not yet notified
    def mark_notified(self, deal_ids: list[int], now: datetime) -> None
    def latest_per_pair(self, slot_id: str) -> list[Observation]   # newest ok search per (origin, dest, seat, outbound, return) with its cheapest offer
    def route_observations(self, slot_id: str, origin: str, destination: str, seat: SeatClass) -> list[Observation]  # newest first
    def offer_by_id(self, offer_id: int) -> OfferRow; def search_by_id(self, search_id: int) -> SearchRow
    def last_run_id(self) -> int | None
```

- [ ] **Step 1: Write the migration**

`vacation_planner/migrations/001_initial.sql`:
```sql
CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  planned INTEGER NOT NULL DEFAULT 0,
  executed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE searches (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  slot_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  destination TEXT NOT NULL,
  outbound_date TEXT NOT NULL,
  return_date TEXT NOT NULL,
  seat TEXT NOT NULL,
  adults INTEGER NOT NULL,
  children INTEGER NOT NULL,
  provider TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  raw_path TEXT
);
CREATE INDEX idx_searches_pair ON searches(slot_id, origin, destination, outbound_date, return_date);
CREATE INDEX idx_searches_route ON searches(slot_id, origin, destination, seat, requested_at);

CREATE TABLE offers (
  id INTEGER PRIMARY KEY,
  search_id INTEGER NOT NULL REFERENCES searches(id),
  provider TEXT NOT NULL,
  price_total REAL NOT NULL,
  currency TEXT NOT NULL,
  per_person REAL NOT NULL,
  airlines_json TEXT NOT NULL,
  stops INTEGER NOT NULL,
  duration_minutes INTEGER NOT NULL,
  departs_at TEXT NOT NULL,
  arrives_at TEXT NOT NULL,
  price_level TEXT,
  typical_low REAL,
  typical_high REAL,
  google_url TEXT NOT NULL,
  flight_json TEXT NOT NULL
);
CREATE INDEX idx_offers_search ON offers(search_id, price_total);

CREATE TABLE deals (
  id INTEGER PRIMARY KEY,
  offer_id INTEGER NOT NULL REFERENCES offers(id),
  search_id INTEGER NOT NULL REFERENCES searches(id),
  slot_id TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  score REAL NOT NULL,
  detected_at TEXT NOT NULL,
  notifiable INTEGER NOT NULL DEFAULT 0,
  notified_at TEXT
);
CREATE INDEX idx_deals_pending ON deals(notifiable, notified_at);

-- cheapest offer per search (lowest id wins ties)
CREATE VIEW cheapest_per_search AS
SELECT o.*
FROM offers o
WHERE o.id = (
  SELECT o2.id FROM offers o2
  WHERE o2.search_id = o.search_id
  ORDER BY o2.price_total ASC, o2.id ASC LIMIT 1
);
```

- [ ] **Step 2: Write the failing tests**

`tests/test_storage.py`:
```python
from datetime import date, datetime, timezone

import pytest

from vacation_planner.models import (
    DealReason, Offer, Provider, SearchRequest, SearchResult, SeatClass,
)
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)


def req(**over) -> SearchRequest:
    base = dict(slot_id="herbst-2026", origin="HAM", destination="BKK",
                outbound_date=date(2026, 10, 17), return_date=date(2026, 10, 31),
                seat=SeatClass.BUSINESS, adults=2, children=1)
    base.update(over)
    return SearchRequest(**base)


def offer(price: float, level=None, airlines=("LH",), provider=Provider.SERPAPI) -> Offer:
    return Offer(provider=provider, price_total=price, currency="EUR", per_person=price / 3,
                 airlines=list(airlines), stops=1, duration_minutes=800,
                 departs_at="2026-10-17T10:00", arrives_at="2026-10-18T06:00",
                 price_level=level, typical_low=None, typical_high=None,
                 google_url="https://g/x", raw={"k": 1})


@pytest.fixture
def db() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


def test_migrations_are_idempotent(tmp_path):
    p = tmp_path / "x.sqlite"
    Storage(p).close()
    Storage(p).close()  # second open must not fail on existing tables


def test_save_result_round_trip(db: Storage):
    run = db.start_run(NOW, planned=1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000, "low"), offer(5400)]), NOW)
    rows = db.searches_in_run(run)
    assert [r.id for r in rows] == [sid]
    assert rows[0].seat is SeatClass.BUSINESS and rows[0].provider is Provider.SERPAPI
    cheapest = db.cheapest_offer(sid)
    assert cheapest.price_total == 5400 and cheapest.airlines == ["LH"]
    db.finish_run(run, executed=1, status="ok", now=NOW)
    assert db.last_run_id() == run


def test_last_observed_and_provider_count(db: Storage):
    run = db.start_run(NOW, 2)
    assert db.last_observed(req()) is None
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(1)]), NOW)
    db.record_search(run, req(destination="DXB"), Provider.FAST_FLIGHTS, "error", NOW, error="boom")
    assert db.last_observed(req()) == NOW
    assert db.searches_by_provider_since(Provider.SERPAPI, datetime(2026, 9, 1, tzinfo=timezone.utc)) == 1
    assert db.searches_by_provider_since(Provider.FAST_FLIGHTS, datetime(2026, 9, 1, tzinfo=timezone.utc)) == 1
    assert db.searches_in_run(run, status="error")[0].error == "boom"


def test_prior_prices_exclude_current_and_other_slots(db: Storage):
    run = db.start_run(NOW, 3)
    a = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), NOW)
    b = db.save_result(run, SearchResult(req(slot_id="sommer-2027"), Provider.SERPAPI, [offer(7000)]), NOW)
    c = db.save_result(run, SearchResult(req(), Provider.FAST_FLIGHTS, [offer(5000)]), NOW)
    assert db.prior_cheapest_prices("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS, before_search_id=c) == [6000]
    assert sorted(db.prior_route_prices("HAM", "BKK", SeatClass.BUSINESS, before_search_id=c)) == [6000, 7000]


def test_deals_pending_and_notified(db: Storage):
    run = db.start_run(NOW, 1)
    sid = db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5000)]), NOW)
    oid = db.cheapest_offer(sid).id
    d1 = db.insert_deal(oid, sid, "herbst-2026", [DealReason.NEW_LOW], 1.0, NOW, notifiable=True)
    d2 = db.insert_deal(oid, sid, "herbst-2026", [DealReason.GOOGLE_LOW], 1.0, NOW, notifiable=False)
    assert [d.id for d in db.pending_deals()] == [d1]
    assert {d.id for d in db.deals_in_run(run)} == {d1, d2}
    assert db.last_notified_price("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) is None
    db.mark_notified([d1], NOW)
    assert db.pending_deals() == []
    assert db.last_notified_price("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == 5000


def test_report_queries(db: Storage):
    earlier = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
    run = db.start_run(earlier, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(6000)]), earlier)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(5500)]), NOW)
    db.save_result(run, SearchResult(req(destination="DXB", seat=SeatClass.BUSINESS), Provider.SERPAPI, [offer(3000)]), NOW)
    best = db.latest_per_pair("herbst-2026")
    assert {(o.search.destination, o.offer.price_total) for o in best} == {("BKK", 5500), ("DXB", 3000)}
    hist = db.route_observations("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS)
    assert [o.offer.price_total for o in hist] == [5500, 6000]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write storage.py**

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path

from .models import DealReason, Offer, Provider, SearchRequest, SearchResult, SeatClass


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class SearchRow:
    id: int
    run_id: int
    slot_id: str
    origin: str
    destination: str
    outbound_date: date
    return_date: date
    seat: SeatClass
    adults: int
    children: int
    provider: Provider
    requested_at: datetime
    status: str
    error: str | None

    @staticmethod
    def from_row(r: sqlite3.Row) -> "SearchRow":
        return SearchRow(
            id=r["id"], run_id=r["run_id"], slot_id=r["slot_id"], origin=r["origin"],
            destination=r["destination"], outbound_date=date.fromisoformat(r["outbound_date"]),
            return_date=date.fromisoformat(r["return_date"]), seat=SeatClass(r["seat"]),
            adults=r["adults"], children=r["children"], provider=Provider(r["provider"]),
            requested_at=_dt(r["requested_at"]), status=r["status"], error=r["error"],
        )


@dataclass
class OfferRow:
    id: int
    search_id: int
    provider: Provider
    price_total: float
    per_person: float
    airlines: list[str]
    stops: int
    duration_minutes: int
    departs_at: str
    arrives_at: str
    price_level: str | None
    typical_low: float | None
    typical_high: float | None
    google_url: str

    @staticmethod
    def from_row(r: sqlite3.Row) -> "OfferRow":
        return OfferRow(
            id=r["id"], search_id=r["search_id"], provider=Provider(r["provider"]),
            price_total=r["price_total"], per_person=r["per_person"],
            airlines=json.loads(r["airlines_json"]), stops=r["stops"],
            duration_minutes=r["duration_minutes"], departs_at=r["departs_at"],
            arrives_at=r["arrives_at"], price_level=r["price_level"],
            typical_low=r["typical_low"], typical_high=r["typical_high"], google_url=r["google_url"],
        )


@dataclass
class DealRow:
    id: int
    offer_id: int
    search_id: int
    slot_id: str
    reasons: list[DealReason]
    score: float
    detected_at: datetime
    notifiable: bool
    notified_at: datetime | None

    @staticmethod
    def from_row(r: sqlite3.Row) -> "DealRow":
        return DealRow(
            id=r["id"], offer_id=r["offer_id"], search_id=r["search_id"], slot_id=r["slot_id"],
            reasons=[DealReason(x) for x in json.loads(r["reasons_json"])], score=r["score"],
            detected_at=_dt(r["detected_at"]), notifiable=bool(r["notifiable"]),
            notified_at=_dt(r["notified_at"]),
        )


@dataclass
class Observation:
    search: SearchRow
    offer: OfferRow


_SEARCH_COLS = "id, run_id, slot_id, origin, destination, outbound_date, return_date, seat, adults, children, provider, requested_at, status, error"
_OFFER_COLS = "id, search_id, provider, price_total, per_person, airlines_json, stops, duration_minutes, departs_at, arrives_at, price_level, typical_low, typical_high, google_url"


class Storage:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    # ---- migrations ----
    def _migrate(self) -> None:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        files = sorted(p for p in resources.files("vacation_planner.migrations").iterdir() if p.name.endswith(".sql"))
        for f in files:
            version = int(f.name.split("_", 1)[0])
            if version <= current:
                continue
            with self.conn:
                self.conn.executescript(f.read_text())
                self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    # ---- runs ----
    def start_run(self, now: datetime, planned: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, planned) VALUES (?, ?)", (_iso(now), planned))
        return cur.lastrowid

    def finish_run(self, run_id: int, executed: int, status: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, executed=?, status=? WHERE id=?",
                (_iso(now), executed, status, run_id))

    def last_run_id(self) -> int | None:
        return self.conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()["id"]

    # ---- searches / offers ----
    def record_search(self, run_id: int, req: SearchRequest, provider: Provider, status: str,
                      now: datetime, error: str | None = None, raw_path: str | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO searches (run_id, slot_id, origin, destination, outbound_date, return_date,
                   seat, adults, children, provider, requested_at, status, error, raw_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, req.slot_id, req.origin, req.destination, req.outbound_date.isoformat(),
                 req.return_date.isoformat(), req.seat.value, req.adults, req.children,
                 provider.value, _iso(now), status, error, raw_path))
        return cur.lastrowid

    def record_offers(self, search_id: int, offers: list[Offer]) -> None:
        with self.conn:
            self.conn.executemany(
                """INSERT INTO offers (search_id, provider, price_total, currency, per_person, airlines_json,
                   stops, duration_minutes, departs_at, arrives_at, price_level, typical_low, typical_high,
                   google_url, flight_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(search_id, o.provider.value, o.price_total, o.currency, o.per_person,
                  json.dumps(o.airlines), o.stops, o.duration_minutes, o.departs_at, o.arrives_at,
                  o.price_level, o.typical_low, o.typical_high, o.google_url, json.dumps(o.raw, default=str))
                 for o in offers])

    def save_result(self, run_id: int, result: SearchResult, now: datetime) -> int:
        with self.conn:
            sid = self.record_search(run_id, result.request, result.provider, "ok", now, raw_path=result.raw_path)
            self.record_offers(sid, result.offers)
        return sid

    def last_observed(self, req: SearchRequest) -> datetime | None:
        r = self.conn.execute(
            """SELECT MAX(requested_at) AS t FROM searches WHERE status='ok' AND slot_id=? AND origin=?
               AND destination=? AND outbound_date=? AND return_date=?""",
            (req.slot_id, req.origin, req.destination, req.outbound_date.isoformat(),
             req.return_date.isoformat())).fetchone()
        return _dt(r["t"])

    def searches_by_provider_since(self, provider: Provider, since: datetime) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS n FROM searches WHERE provider=? AND requested_at>=? AND status IN ('ok','error')",
            (provider.value, _iso(since))).fetchone()
        return r["n"]

    def searches_in_run(self, run_id: int, status: str = "ok") -> list[SearchRow]:
        rows = self.conn.execute(
            f"SELECT {_SEARCH_COLS} FROM searches WHERE run_id=? AND status=? ORDER BY id", (run_id, status))
        return [SearchRow.from_row(r) for r in rows]

    def search_by_id(self, search_id: int) -> SearchRow:
        return SearchRow.from_row(self.conn.execute(
            f"SELECT {_SEARCH_COLS} FROM searches WHERE id=?", (search_id,)).fetchone())

    def offer_by_id(self, offer_id: int) -> OfferRow:
        return OfferRow.from_row(self.conn.execute(
            f"SELECT {_OFFER_COLS} FROM offers WHERE id=?", (offer_id,)).fetchone())

    def cheapest_offer(self, search_id: int) -> OfferRow | None:
        r = self.conn.execute(
            f"SELECT {_OFFER_COLS} FROM cheapest_per_search WHERE search_id=?", (search_id,)).fetchone()
        return OfferRow.from_row(r) if r else None

    def prior_cheapest_prices(self, slot_id: str, origin: str, destination: str, seat: SeatClass,
                              before_search_id: int) -> list[float]:
        rows = self.conn.execute(
            """SELECT c.price_total FROM cheapest_per_search c JOIN searches s ON s.id=c.search_id
               WHERE s.status='ok' AND s.slot_id=? AND s.origin=? AND s.destination=? AND s.seat=? AND s.id<?""",
            (slot_id, origin, destination, seat.value, before_search_id))
        return [r["price_total"] for r in rows]

    def prior_route_prices(self, origin: str, destination: str, seat: SeatClass, before_search_id: int) -> list[float]:
        rows = self.conn.execute(
            """SELECT c.price_total FROM cheapest_per_search c JOIN searches s ON s.id=c.search_id
               WHERE s.status='ok' AND s.origin=? AND s.destination=? AND s.seat=? AND s.id<?""",
            (origin, destination, seat.value, before_search_id))
        return [r["price_total"] for r in rows]

    # ---- deals ----
    def insert_deal(self, offer_id: int, search_id: int, slot_id: str, reasons: list[DealReason],
                    score: float, now: datetime, notifiable: bool) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO deals (offer_id, search_id, slot_id, reasons_json, score, detected_at, notifiable)
                   VALUES (?,?,?,?,?,?,?)""",
                (offer_id, search_id, slot_id, json.dumps([r.value for r in reasons]), score, _iso(now), int(notifiable)))
        return cur.lastrowid

    def last_notified_price(self, slot_id: str, origin: str, destination: str, seat: SeatClass) -> float | None:
        r = self.conn.execute(
            """SELECT o.price_total FROM deals d JOIN offers o ON o.id=d.offer_id JOIN searches s ON s.id=d.search_id
               WHERE d.notified_at IS NOT NULL AND d.slot_id=? AND s.origin=? AND s.destination=? AND s.seat=?
               ORDER BY d.notified_at DESC, d.id DESC LIMIT 1""",
            (slot_id, origin, destination, seat.value)).fetchone()
        return r["price_total"] if r else None

    def deals_in_run(self, run_id: int) -> list[DealRow]:
        rows = self.conn.execute(
            "SELECT d.* FROM deals d JOIN searches s ON s.id=d.search_id WHERE s.run_id=? ORDER BY d.id", (run_id,))
        return [DealRow.from_row(r) for r in rows]

    def pending_deals(self) -> list[DealRow]:
        rows = self.conn.execute(
            "SELECT * FROM deals WHERE notifiable=1 AND notified_at IS NULL ORDER BY id")
        return [DealRow.from_row(r) for r in rows]

    def mark_notified(self, deal_ids: list[int], now: datetime) -> None:
        with self.conn:
            self.conn.executemany("UPDATE deals SET notified_at=? WHERE id=?", [(_iso(now), i) for i in deal_ids])

    # ---- report ----
    def _observations(self, sql: str, params: tuple) -> list[Observation]:
        out = []
        for r in self.conn.execute(sql, params):
            out.append(Observation(self.search_by_id(r["search_id"]), self.offer_by_id(r["offer_id"])))
        return out

    def latest_per_pair(self, slot_id: str) -> list[Observation]:
        return self._observations(
            """SELECT s.id AS search_id, c.id AS offer_id FROM searches s JOIN cheapest_per_search c ON c.search_id=s.id
               WHERE s.status='ok' AND s.slot_id=? AND s.id = (
                 SELECT s2.id FROM searches s2 JOIN cheapest_per_search c2 ON c2.search_id=s2.id
                 WHERE s2.status='ok' AND s2.slot_id=s.slot_id AND s2.origin=s.origin
                   AND s2.destination=s.destination AND s2.seat=s.seat
                   AND s2.outbound_date=s.outbound_date AND s2.return_date=s.return_date
                 ORDER BY s2.requested_at DESC, s2.id DESC LIMIT 1)
               ORDER BY c.price_total""",
            (slot_id,))

    def route_observations(self, slot_id: str, origin: str, destination: str, seat: SeatClass) -> list[Observation]:
        return self._observations(
            """SELECT s.id AS search_id, c.id AS offer_id FROM searches s JOIN cheapest_per_search c ON c.search_id=s.id
               WHERE s.status='ok' AND s.slot_id=? AND s.origin=? AND s.destination=? AND s.seat=?
               ORDER BY s.requested_at DESC, s.id DESC""",
            (slot_id, origin, destination, seat.value))
```

Note on `latest_per_pair`: it returns the most recent price for every date pair searched in the slot. The report (Task 12) groups these by destination and takes the cheapest, so the index shows the best current price per target without re-showing stale prices for the same dates.

The empty `migrations/__init__.py` is required so `importlib.resources.files("vacation_planner.migrations")` resolves; hatchling ships `.sql` and `.html` files inside the package directory without extra config.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: 6 PASSED. If `test_report_queries` fails on the BKK count, check that both BKK searches share `seat` and that `requested_at` ordering is ISO strings (they sort lexically, which matches chronological order for same-offset timestamps).

- [ ] **Step 6: Commit**

```bash
git add vacation_planner/storage.py vacation_planner/migrations tests/test_storage.py
git commit -m "feat: sqlite storage with runs, searches, offers, deals"
```

---

### Task 6: Search planner

**Files:**
- Create: `vacation_planner/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `Config` (Task 3), `free_window`/`pax_for` (Task 4), `Storage.last_observed` (Task 5), `PlannedSearch`/`SearchRequest`/`seat_for` (Task 2).
- Produces:

```python
def candidate_pairs(window: Window, nights: Nights) -> list[tuple[date, date]]   # all (out, ret) with out>=window.start, ret<=window.end, nights.min<=ret-out<=nights.max; sorted by out then ret
def plan(config: Config, storage: Storage, today: date) -> list[PlannedSearch]
def serpapi_share(budget: BudgetSettings) -> int   # max(1, serpapi_per_month // runs_per_month)
```

- [ ] **Step 1: Write the failing tests**

`tests/test_planner.py`:
```python
from datetime import date, datetime, timezone

from vacation_planner.calendar import Window
from vacation_planner.config import load_config
from vacation_planner.models import Nights, Provider, SearchResult, SeatClass
from vacation_planner.planner import candidate_pairs, plan, serpapi_share
from vacation_planner.storage import Storage

TODAY = date(2026, 9, 21)


def test_candidate_pairs_respect_window_and_nights():
    pairs = candidate_pairs(Window(date(2026, 10, 17), date(2026, 11, 1)), Nights(14, 14))
    assert pairs == [(date(2026, 10, 17), date(2026, 10, 31)), (date(2026, 10, 18), date(2026, 11, 1))]


def test_candidate_pairs_empty_when_window_too_short():
    assert candidate_pairs(Window(date(2027, 1, 29), date(2027, 1, 31)), Nights(7, 14)) == []


def test_serpapi_share():
    cfg = load_config_dir()
    assert serpapi_share(cfg.settings.budget) == 25


def load_config_dir(config_dir=None):
    from tests.conftest import REPO_CONFIG
    return load_config(config_dir or REPO_CONFIG, env={})


def test_plan_orders_by_slot_and_assigns_providers(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, TODAY)
    # herbst-2026 first (BKK business), only slots with targets, no past slots
    assert ps[0].request.slot_id == "herbst-2026"
    assert ps[0].request.destination == "BKK" and ps[0].request.seat is SeatClass.BUSINESS
    assert ps[0].request.adults == 2 and ps[0].request.children == 1
    slots = [p.request.slot_id for p in ps]
    assert "fruehjahr-2027" in slots and "herbst-2027" not in slots  # herbst-2027 has no targets
    # per-route cap 3
    assert sum(1 for p in ps if p.request.slot_id == "herbst-2026") == 3
    # first 25 serpapi, rest backup, total <= 60
    assert all(p.provider is Provider.SERPAPI for p in ps[:25])
    assert all(p.provider is Provider.FAST_FLIGHTS for p in ps[25:])
    assert len(ps) <= 60
    # PMI in pfingsten is economy (cabin any)
    pmi = next(p for p in ps if p.request.destination == "PMI")
    assert pmi.request.seat is SeatClass.ECONOMY and 7 <= pmi.request.nights <= 9


def test_plan_prefers_unseen_pairs(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    first = plan(cfg, db, TODAY)
    run = db.start_run(datetime(2026, 9, 21, tzinfo=timezone.utc), 1)
    seen = first[0].request
    db.save_result(run, SearchResult(seen, Provider.SERPAPI, []), datetime(2026, 9, 21, tzinfo=timezone.utc))
    second = plan(cfg, db, TODAY)
    herbst = [p.request for p in second if p.request.slot_id == "herbst-2026"]
    assert seen not in herbst[:2]  # two unseen pairs come before the seen one


def test_plan_skips_started_slots_and_far_future(config_dir):
    cfg = load_config_dir(config_dir)
    db = Storage(":memory:")
    ps = plan(cfg, db, date(2026, 10, 20))  # herbst-2026 already started
    assert all(p.request.slot_id != "herbst-2026" for p in ps)
    ps = plan(cfg, db, date(2025, 1, 1))  # everything > 330 days away
    assert ps == []


def test_plan_without_backup_cuts_at_serpapi_share(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("backup: fast_flights", "backup: null"))
    cfg = load_config_dir(config_dir)
    ps = plan(cfg, Storage(":memory:"), TODAY)
    assert len(ps) <= 25 and all(p.provider is Provider.SERPAPI for p in ps)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write planner.py**

```python
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .calendar import Window, free_window, pax_for
from .config import BudgetSettings, Config
from .models import Nights, PlannedSearch, SearchRequest, Slot, seat_for
from .storage import Storage


def candidate_pairs(window: Window, nights: Nights) -> list[tuple[date, date]]:
    pairs = []
    out = window.start
    while out <= window.end:
        for n in range(nights.min, nights.max + 1):
            ret = out + timedelta(days=n)
            if ret > window.end:
                break
            pairs.append((out, ret))
        out += timedelta(days=1)
    return pairs


def serpapi_share(budget: BudgetSettings) -> int:
    return max(1, budget.serpapi_per_month // budget.runs_per_month)


def _slot_nights(slot: Slot, cfg: Config) -> Nights:
    return slot.nights or Nights(cfg.settings.nights.min, cfg.settings.nights.max)


def _route_requests(cfg: Config, storage: Storage, slot: Slot, origin: str, dest_code: str) -> list[SearchRequest]:
    dest = cfg.destination(dest_code)
    bd = cfg.settings.bridge_days
    window = free_window(slot, bd.before, bd.after)
    pairs = candidate_pairs(window, _slot_nights(slot, cfg))
    reqs = []
    for out, ret in pairs:
        adults, children, _infants = pax_for(cfg.travellers, out)
        reqs.append(SearchRequest(slot.id, origin, dest_code, out, ret, seat_for(dest.cabin), adults, children))
    never = datetime.min.replace(tzinfo=timezone.utc)
    reqs.sort(key=lambda r: (storage.last_observed(r) or never, r.outbound_date, r.return_date))
    return reqs[: cfg.settings.budget.max_pairs_per_route_per_run]


def plan(config: Config, storage: Storage, today: date) -> list[PlannedSearch]:
    s = config.settings
    horizon = today + timedelta(days=s.deals.lookahead_days)
    requests: list[SearchRequest] = []
    for slot in config.slots:
        if not slot.targets or slot.start <= today or slot.start > horizon:
            continue
        for origin in s.origins:
            for dest_code in slot.targets:
                requests.extend(_route_requests(config, storage, slot, origin, dest_code))

    primary_n = serpapi_share(s.budget)
    backup = s.providers.backup
    limit = s.budget.max_searches_per_run if backup else primary_n
    out = []
    for i, req in enumerate(requests[:limit]):
        provider = s.providers.primary if i < primary_n else backup
        out.append(PlannedSearch(req, provider))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planner.py -v`
Expected: 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/planner.py tests/test_planner.py
git commit -m "feat: search planner with per-slot targets and provider split"
```

---

### Task 7: Provider protocol, errors and fake client

**Files:**
- Create: `vacation_planner/providers/__init__.py`, `vacation_planner/providers/base.py`, `vacation_planner/providers/fake.py`
- Test: `tests/test_providers_fake.py`

**Interfaces:**
- Produces:

```python
class ProviderError(Exception): ...          # transient or parse failure, search may fall back
class QuotaExhausted(ProviderError): ...     # primary budget gone for this month
class FlightClient(Protocol):
    provider: Provider
    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult: ...
def filter_excluded(offers: list[Offer], excluded: Iterable[str]) -> list[Offer]   # drop offers whose airlines intersect excluded (case-insensitive)
def per_person(price: float, req: SearchRequest, price_is_total: bool) -> tuple[float, float]  # -> (price_total, per_person)
class FakeFlightClient:  # provider = Provider.FAKE
    def __init__(self, prices: dict[tuple[str, str], list[float]] | None = None, fail: set[tuple[str, str]] = frozenset(), quota_after: int | None = None)
    calls: list[SearchRequest]
```

The fake returns one offer per configured price for `(origin, destination)`, defaulting to a single deterministic price derived from the request (`1000 + 100 * nights`) so end-to-end tests don't need per-route setup. `fail` makes those routes raise `ProviderError`; `quota_after=n` raises `QuotaExhausted` on call n+1 onwards.

- [ ] **Step 1: Write the failing tests**

`tests/test_providers_fake.py`:
```python
from datetime import date

import pytest

from vacation_planner.models import Offer, Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError, QuotaExhausted, filter_excluded, per_person
from vacation_planner.providers.fake import FakeFlightClient


def req(dest="BKK"):
    return SearchRequest("herbst-2026", "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


def mk(airlines):
    return Offer(Provider.FAKE, 1, "EUR", 1, list(airlines), 0, 1, "", "", None, None, None, "", {})


def test_filter_excluded_is_case_insensitive():
    kept = filter_excluded([mk(["LH"]), mk(["ai", "LH"]), mk(["EK"])], ["AI"])
    assert [o.airlines for o in kept] == [["LH"], ["EK"]]


def test_per_person_split():
    assert per_person(3000, req(), True) == (3000, 1000)
    assert per_person(1000, req(), False) == (3000, 1000)


def test_fake_default_and_configured_prices():
    c = FakeFlightClient(prices={("HAM", "DXB"): [900, 800]})
    r = c.search(req())
    assert r.provider is Provider.FAKE and [o.price_total for o in r.offers] == [2400]  # 1000 + 100*14
    assert [o.price_total for o in c.search(req("DXB")).offers] == [900, 800]
    assert len(c.calls) == 2


def test_fake_failures():
    c = FakeFlightClient(fail={("HAM", "BKK")}, quota_after=1)
    with pytest.raises(ProviderError):
        c.search(req())
    c.search(req("DXB"))
    with pytest.raises(QuotaExhausted):
        c.search(req("DXB"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_fake.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write base.py and fake.py**

`vacation_planner/providers/__init__.py`: empty.

`vacation_planner/providers/base.py`:
```python
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from ..models import Offer, Provider, SearchRequest, SearchResult


class ProviderError(Exception):
    """The provider failed for this search; the executor may fall back."""


class QuotaExhausted(ProviderError):
    """The provider's monthly quota is used up."""


class FlightClient(Protocol):
    provider: Provider

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult: ...


def filter_excluded(offers: list[Offer], excluded: Iterable[str]) -> list[Offer]:
    ex = {e.upper() for e in excluded}
    return [o for o in offers if not ({a.upper() for a in o.airlines} & ex)]


def per_person(price: float, req: SearchRequest, price_is_total: bool) -> tuple[float, float]:
    if price_is_total:
        return float(price), float(price) / req.pax
    return float(price) * req.pax, float(price)
```

`vacation_planner/providers/fake.py`:
```python
from __future__ import annotations

from pathlib import Path

from ..models import Offer, Provider, SearchRequest, SearchResult
from .base import ProviderError, QuotaExhausted


class FakeFlightClient:
    provider = Provider.FAKE

    def __init__(self, prices: dict[tuple[str, str], list[float]] | None = None,
                 fail: set[tuple[str, str]] = frozenset(), quota_after: int | None = None):
        self.prices = prices or {}
        self.fail = set(fail)
        self.quota_after = quota_after
        self.calls: list[SearchRequest] = []

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        self.calls.append(req)
        key = (req.origin, req.destination)
        if key in self.fail:
            raise ProviderError(f"fake failure for {key}")
        if self.quota_after is not None and len(self.calls) > self.quota_after + 1:
            raise QuotaExhausted("fake quota exhausted")
        prices = self.prices.get(key) or [1000 + 100 * req.nights]
        offers = [
            Offer(provider=self.provider, price_total=float(p), currency="EUR", per_person=float(p) / req.pax,
                  airlines=["LH"], stops=1, duration_minutes=720,
                  departs_at=f"{req.outbound_date}T10:00", arrives_at=f"{req.outbound_date}T22:00",
                  price_level=None, typical_low=None, typical_high=None,
                  google_url=f"https://www.google.com/travel/flights?fake={req.origin}-{req.destination}", raw={})
            for p in prices
        ]
        return SearchResult(req, self.provider, offers)
```

Check `test_fake_failures`: call 1 (BKK) fails and is counted, call 2 (DXB) succeeds, call 3 raises quota because `len(calls)=3 > quota_after+1=2`. Adjust the condition to `len(self.calls) > self.quota_after` if you prefer the failed call not to count, but then update the test to `quota_after=2`. Pick one and keep test and code consistent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_fake.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/providers tests/test_providers_fake.py
git commit -m "feat: flight client protocol and fake provider"
```

---

### Task 8: SerpApi provider

**Files:**
- Create: `vacation_planner/providers/serpapi.py`, `tests/fixtures/serpapi_ham_bkk.json`
- Test: `tests/test_providers_serpapi.py`

**Interfaces:**
- Consumes: `FlightClient`, `ProviderError`, `QuotaExhausted`, `filter_excluded`, `per_person` (Task 7); `Settings` (Task 3).
- Produces:

```python
class SerpApiClient:   # provider = Provider.SERPAPI
    def __init__(self, api_key: str, settings: Settings, http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep)
    def params_for(self, req: SearchRequest) -> dict[str, str]
    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult
def parse_response(data: dict, req: SearchRequest, price_is_total: bool) -> list[Offer]
def airline_codes(itinerary: dict) -> list[str]   # from each leg's flight_number prefix, e.g. "LH 11" -> "LH"
```

Parameter mapping (from SerpApi docs, verified live in Task 16):
`engine=google_flights`, `departure_id`, `arrival_id`, `outbound_date`, `return_date` (ISO), `type=1`, `travel_class` (economy→1, business→3), `adults`, `children`, `currency=EUR`, `hl=en`, `gl=de`, `stops=max_stops+1` (SerpApi: 1 nonstop, 2 one stop or fewer, 3 two or fewer), `exclude_airlines` comma-joined when non-empty, `api_key`.

Errors: HTTP 429 or a JSON `error` string containing "out of searches" or "limit" → `QuotaExhausted`. Other HTTP ≥ 400, network errors, JSON `error` → retried up to 3 attempts with sleeps 2s, 4s, then `ProviderError`. Quota is never retried.

- [ ] **Step 1: Write the synthetic fixture**

`tests/fixtures/serpapi_ham_bkk.json` (a realistic subset of the documented shape; Task 16 replaces it with a recorded response):
```json
{
  "search_metadata": {"status": "Success", "google_flights_url": "https://www.google.com/travel/flights?hl=en&gl=de&curr=EUR&tfs=FIXTURE"},
  "search_parameters": {"engine": "google_flights", "departure_id": "HAM", "arrival_id": "BKK"},
  "best_flights": [
    {
      "flights": [
        {"departure_airport": {"name": "Hamburg Airport", "id": "HAM", "time": "2026-10-17 10:35"},
         "arrival_airport": {"name": "Frankfurt Airport", "id": "FRA", "time": "2026-10-17 11:45"},
         "duration": 70, "airline": "Lufthansa", "travel_class": "Business", "flight_number": "LH 11"},
        {"departure_airport": {"name": "Frankfurt Airport", "id": "FRA", "time": "2026-10-17 13:55"},
         "arrival_airport": {"name": "Suvarnabhumi Airport", "id": "BKK", "time": "2026-10-18 06:10"},
         "duration": 675, "airline": "Thai", "travel_class": "Business", "flight_number": "TG 921"}
      ],
      "layovers": [{"duration": 130, "name": "Frankfurt Airport", "id": "FRA"}],
      "total_duration": 875, "price": 5940, "type": "Round trip", "departure_token": "tok1"
    }
  ],
  "other_flights": [
    {
      "flights": [
        {"departure_airport": {"name": "Hamburg Airport", "id": "HAM", "time": "2026-10-17 06:00"},
         "arrival_airport": {"name": "Indira Gandhi International Airport", "id": "DEL", "time": "2026-10-17 18:30"},
         "duration": 570, "airline": "Air India", "travel_class": "Business", "flight_number": "AI 120"},
        {"departure_airport": {"name": "Indira Gandhi International Airport", "id": "DEL", "time": "2026-10-17 21:00"},
         "arrival_airport": {"name": "Suvarnabhumi Airport", "id": "BKK", "time": "2026-10-18 03:00"},
         "duration": 270, "airline": "Air India", "travel_class": "Business", "flight_number": "AI 332"}
      ],
      "layovers": [{"duration": 150, "name": "Indira Gandhi International Airport", "id": "DEL"}],
      "total_duration": 990, "price": 4100, "type": "Round trip", "departure_token": "tok2"
    },
    {
      "flights": [
        {"departure_airport": {"name": "Hamburg Airport", "id": "HAM", "time": "2026-10-17 14:20"},
         "arrival_airport": {"name": "Dubai International Airport", "id": "DXB", "time": "2026-10-17 23:05"},
         "duration": 405, "airline": "Emirates", "travel_class": "Business", "flight_number": "EK 60"},
        {"departure_airport": {"name": "Dubai International Airport", "id": "DXB", "time": "2026-10-18 03:30"},
         "arrival_airport": {"name": "Suvarnabhumi Airport", "id": "BKK", "time": "2026-10-18 12:50"},
         "duration": 380, "airline": "Emirates", "travel_class": "Business", "flight_number": "EK 384"}
      ],
      "layovers": [{"duration": 265, "name": "Dubai International Airport", "id": "DXB"}],
      "total_duration": 1050, "price": 6420, "type": "Round trip", "departure_token": "tok3"
    }
  ],
  "price_insights": {"lowest_price": 5940, "price_level": "low", "typical_price_range": [7200, 9800]}
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_providers_serpapi.py`:
```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError, QuotaExhausted
from vacation_planner.providers.serpapi import SerpApiClient, airline_codes, parse_response

FIX = Path(__file__).parent / "fixtures" / "serpapi_ham_bkk.json"
REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


@pytest.fixture
def client(config_dir):
    cfg = load_config(config_dir, env={})
    sleeps = []
    c = SerpApiClient("KEY", cfg.settings, sleep=sleeps.append)
    c._sleeps = sleeps
    return c


def test_params_mapping(client):
    p = client.params_for(REQ)
    assert p["engine"] == "google_flights" and p["departure_id"] == "HAM" and p["arrival_id"] == "BKK"
    assert p["outbound_date"] == "2026-10-17" and p["return_date"] == "2026-10-31" and p["type"] == "1"
    assert p["travel_class"] == "3" and p["adults"] == "2" and p["children"] == "1"
    assert p["currency"] == "EUR" and p["stops"] == "2" and p["exclude_airlines"] == "AI"
    assert p["api_key"] == "KEY"


def test_airline_codes():
    it = json.loads(FIX.read_text())["best_flights"][0]
    assert airline_codes(it) == ["LH", "TG"]


def test_parse_response_reads_both_lists_and_insights():
    offers = parse_response(json.loads(FIX.read_text()), REQ, price_is_total=True)
    assert [o.price_total for o in offers] == [5940, 4100, 6420]
    o = offers[0]
    assert o.provider is Provider.SERPAPI and o.per_person == 1980 and o.stops == 1
    assert o.duration_minutes == 875 and o.departs_at == "2026-10-17 10:35" and o.arrives_at == "2026-10-18 06:10"
    assert o.price_level == "low" and o.typical_low == 7200 and o.typical_high == 9800
    assert o.google_url.endswith("tfs=FIXTURE")


def test_search_filters_excluded_airlines_and_saves_raw(client, httpx_mock, tmp_path):
    httpx_mock.add_response(json=json.loads(FIX.read_text()))
    res = client.search(REQ, raw_dir=tmp_path)
    assert [o.price_total for o in res.offers] == [5940, 6420]   # AI itinerary dropped
    assert res.raw_path and Path(res.raw_path).exists()
    assert json.loads(Path(res.raw_path).read_text())["search_metadata"]["status"] == "Success"


def test_quota_error_is_not_retried(client, httpx_mock):
    httpx_mock.add_response(status_code=429, json={"error": "Your account has run out of searches."})
    with pytest.raises(QuotaExhausted):
        client.search(REQ)
    assert client._sleeps == []


def test_transient_error_retries_then_raises(client, httpx_mock):
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    with pytest.raises(ProviderError):
        client.search(REQ)
    assert client._sleeps == [2, 4]


def test_transient_then_success(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_response(json=json.loads(FIX.read_text()))
    assert len(client.search(REQ).offers) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_serpapi.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write serpapi.py**

```python
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import httpx

from ..config import Settings
from ..models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from .base import ProviderError, QuotaExhausted, filter_excluded, per_person

URL = "https://serpapi.com/search.json"
TRAVEL_CLASS = {SeatClass.ECONOMY: "1", SeatClass.BUSINESS: "3"}
BACKOFF = [2, 4]


def airline_codes(itinerary: dict) -> list[str]:
    codes = []
    for leg in itinerary.get("flights", []):
        fn = leg.get("flight_number") or ""
        code = fn.split(" ")[0].strip() if fn else (leg.get("airline") or "?")
        if code not in codes:
            codes.append(code)
    return codes


def parse_response(data: dict, req: SearchRequest, price_is_total: bool) -> list[Offer]:
    insights = data.get("price_insights") or {}
    rng = insights.get("typical_price_range") or [None, None]
    url = (data.get("search_metadata") or {}).get("google_flights_url", "")
    offers = []
    for it in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = it.get("flights") or []
        if not legs or it.get("price") is None:
            continue
        total, pp = per_person(it["price"], req, price_is_total)
        offers.append(Offer(
            provider=Provider.SERPAPI, price_total=total, currency="EUR", per_person=pp,
            airlines=airline_codes(it), stops=len(legs) - 1,
            duration_minutes=int(it.get("total_duration") or sum(l.get("duration", 0) for l in legs)),
            departs_at=legs[0]["departure_airport"]["time"], arrives_at=legs[-1]["arrival_airport"]["time"],
            price_level=insights.get("price_level"), typical_low=rng[0], typical_high=rng[1],
            google_url=url, raw=it,
        ))
    return offers


def _is_quota(status: int, body: dict | None) -> bool:
    msg = ((body or {}).get("error") or "").lower()
    return status == 429 or "out of searches" in msg or "limit" in msg


class SerpApiClient:
    provider = Provider.SERPAPI

    def __init__(self, api_key: str, settings: Settings, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key
        self.settings = settings
        self.http = http or httpx.Client(timeout=60)
        self.sleep = sleep

    def params_for(self, req: SearchRequest) -> dict[str, str]:
        p = {
            "engine": "google_flights", "departure_id": req.origin, "arrival_id": req.destination,
            "outbound_date": req.outbound_date.isoformat(), "return_date": req.return_date.isoformat(),
            "type": "1", "travel_class": TRAVEL_CLASS[req.seat], "adults": str(req.adults),
            "children": str(req.children), "currency": "EUR", "hl": "en", "gl": "de",
            "stops": str(self.settings.max_stops + 1), "api_key": self.api_key,
        }
        if self.settings.excluded_airlines:
            p["exclude_airlines"] = ",".join(self.settings.excluded_airlines)
        return p

    def _fetch(self, req: SearchRequest) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            if attempt:
                self.sleep(BACKOFF[attempt - 1])
            try:
                r = self.http.get(URL, params=self.params_for(req))
                body = None
                try:
                    body = r.json()
                except ValueError:
                    pass
                if _is_quota(r.status_code, body):
                    raise QuotaExhausted(str((body or {}).get("error") or r.status_code))
                if r.status_code >= 400 or body is None or body.get("error"):
                    last = ProviderError(f"serpapi {r.status_code}: {(body or {}).get('error')}")
                    continue
                return body
            except QuotaExhausted:
                raise
            except httpx.HTTPError as e:
                last = ProviderError(f"serpapi network error: {e}")
        raise last or ProviderError("serpapi: unknown failure")

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        data = self._fetch(req)
        raw_path = None
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            f = raw_dir / f"serpapi_{req.origin}-{req.destination}_{req.outbound_date}_{req.return_date}_{req.seat.value}.json"
            f.write_text(json.dumps(data))
            raw_path = str(f)
        offers = filter_excluded(parse_response(data, req, self.settings.providers.price_is_total),
                                 self.settings.excluded_airlines)
        return SearchResult(req, self.provider, offers, raw_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_serpapi.py -v`
Expected: 7 PASSED.

- [ ] **Step 6: Commit**

```bash
git add vacation_planner/providers/serpapi.py tests/fixtures/serpapi_ham_bkk.json tests/test_providers_serpapi.py
git commit -m "feat: SerpApi Google Flights provider"
```

---

### Task 9: fast-flights backup provider

**Files:**
- Create: `vacation_planner/providers/fast_flights.py`, `tests/fixtures/fast_flights_result.py`
- Test: `tests/test_providers_fast_flights.py`

**Interfaces:**
- Consumes: Task 7 base; `fast_flights` package (3.1): `create_query`, `FlightQuery`, `Passengers`, `get_flights`, `FlightsNotFound`, `ResultList` (list of `Flights(type, price:int, airlines: list[str] names, flights: list[SingleFlight], carbon)`, attribute `metadata.airlines: list[Airline(code, name)]`), `SingleFlight(from_airport, to_airport, departure: SimpleDatetime(date=(y,m,d), time=(h,mi)), arrival, duration, plane_type)`, `Query.url()`.
- Produces:

```python
class FastFlightsClient:   # provider = Provider.FAST_FLIGHTS
    def __init__(self, settings: Settings, fetch: Callable[[Query], ResultList] = get_flights, sleep: Callable[[float], None] = time.sleep)
    def build_query(self, req: SearchRequest) -> Query
    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult
def parse_results(results: ResultList, req: SearchRequest, url: str, price_is_total: bool) -> list[Offer]
```

Mapping: two `FlightQuery`s (out: origin→dest on outbound date, back: dest→origin on return date), each with `max_stops=settings.max_stops`; `seat` = `"business"` or `"economy"`; `trip="round-trip"`; `Passengers(adults, children)`; `currency="EUR"`; `language="en-US"`. Airline codes come from inverting `results.metadata.airlines` (name→code); an unknown name is kept as the name. Stops = legs − 1. Duration = minutes between first departure and last arrival computed from the `SimpleDatetime` tuples (naive local times; good enough for display). `price_level`, `typical_low`, `typical_high` are `None`. `FlightsNotFound`, empty result, or any exception from the fetch → `ProviderError`. The client sleeps `backup_pause_seconds` before every fetch.

- [ ] **Step 1: Write the fixture builder**

`tests/fixtures/fast_flights_result.py`:
```python
from fast_flights.model import Airline, Airport, Alliance, CarbonEmission, Flights, JsMetadata, SimpleDatetime, SingleFlight
from fast_flights.parser import ResultList


def leg(frm, to, dep, arr, minutes):
    return SingleFlight(
        from_airport=Airport(name=frm, code=frm), to_airport=Airport(name=to, code=to),
        departure=SimpleDatetime(date=dep[:3], time=dep[3:]), arrival=SimpleDatetime(date=arr[:3], time=arr[3:]),
        duration=minutes, plane_type="A350",
    )


def build() -> ResultList:
    rl = ResultList([
        Flights(type="best", price=5940, airlines=["Lufthansa", "Thai"],
                flights=[leg("HAM", "FRA", (2026, 10, 17, 10, 35), (2026, 10, 17, 11, 45), 70),
                         leg("FRA", "BKK", (2026, 10, 17, 13, 55), (2026, 10, 18, 6, 10), 675)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
        Flights(type="other", price=4100, airlines=["Air India"],
                flights=[leg("HAM", "DEL", (2026, 10, 17, 6, 0), (2026, 10, 17, 18, 30), 570),
                         leg("DEL", "BKK", (2026, 10, 17, 21, 0), (2026, 10, 18, 3, 0), 270)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
        Flights(type="other", price=6100, airlines=["Emirates"],
                flights=[leg("HAM", "DXB", (2026, 10, 17, 14, 20), (2026, 10, 17, 23, 5), 405),
                         leg("DXB", "BKK", (2026, 10, 18, 3, 30), (2026, 10, 18, 12, 50), 380)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
    ])
    rl.metadata = JsMetadata(
        airlines=[Airline("LH", "Lufthansa"), Airline("TG", "Thai"), Airline("AI", "Air India"), Airline("EK", "Emirates")],
        alliances=[Alliance("STAR_ALLIANCE", "Star Alliance")],
    )
    return rl
```

- [ ] **Step 2: Write the failing tests**

`tests/test_providers_fast_flights.py`:
```python
from datetime import date

import pytest
from fast_flights import FlightsNotFound

from vacation_planner.config import load_config
from vacation_planner.models import Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError
from vacation_planner.providers.fast_flights import FastFlightsClient, parse_results
from tests.fixtures.fast_flights_result import build

REQ = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


@pytest.fixture
def settings(config_dir):
    return load_config(config_dir, env={}).settings


def test_build_query(settings):
    q = FastFlightsClient(settings).build_query(REQ)
    assert q.url().startswith("https://www.google.com/travel/flights/search?tfs=")
    assert q.currency == "EUR"
    assert q.get_trip_type() == "round-trip" and q.get_seat_type() == "business"
    assert len(q.flight_data) == 2 and len(q.passengers) == 3


def test_parse_results_maps_codes_and_times():
    offers = parse_results(build(), REQ, "https://g/url", price_is_total=True)
    assert [o.price_total for o in offers] == [5940, 4100, 6100]
    o = offers[0]
    assert o.provider is Provider.FAST_FLIGHTS and o.airlines == ["LH", "TG"] and o.stops == 1
    assert o.departs_at == "2026-10-17 10:35" and o.arrives_at == "2026-10-18 06:10"
    assert o.duration_minutes == 1175 and o.price_level is None and o.google_url == "https://g/url"
    assert o.per_person == 1980


def test_search_filters_excluded_and_pauses(settings):
    sleeps, calls = [], []

    def fetch(q):
        calls.append(q)
        return build()

    c = FastFlightsClient(settings, fetch=fetch, sleep=sleeps.append)
    res = c.search(REQ)
    assert [o.price_total for o in res.offers] == [5940, 6100]
    assert sleeps == [5.0] and len(calls) == 1 and res.raw_path is None


def test_search_wraps_failures(settings):
    def not_found(q):
        raise FlightsNotFound("none")

    with pytest.raises(ProviderError):
        FastFlightsClient(settings, fetch=not_found, sleep=lambda s: None).search(REQ)

    def empty(q):
        return build().__class__()

    with pytest.raises(ProviderError):
        FastFlightsClient(settings, fetch=empty, sleep=lambda s: None).search(REQ)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_fast_flights.py -v`
Expected: FAIL with `ModuleNotFoundError: vacation_planner.providers.fast_flights`.

- [ ] **Step 4: Write fast_flights.py**

```python
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights
from fast_flights.parser import ResultList
from fast_flights.querying import Query

from ..config import Settings
from ..models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from .base import ProviderError, filter_excluded, per_person

SEAT = {SeatClass.ECONOMY: "economy", SeatClass.BUSINESS: "business"}


def _fmt(sd) -> str:
    (y, m, d), (h, mi) = sd.date, sd.time
    return f"{y:04d}-{m:02d}-{d:02d} {h:02d}:{mi:02d}"


def _minutes_between(a, b) -> int:
    da = datetime(*a.date, *a.time)
    db = datetime(*b.date, *b.time)
    return int((db - da).total_seconds() // 60)


def parse_results(results: ResultList, req: SearchRequest, url: str, price_is_total: bool) -> list[Offer]:
    meta = getattr(results, "metadata", None)
    name_to_code = {a.name: a.code for a in (meta.airlines if meta else [])}
    offers = []
    for it in results:
        if not it.flights or it.price is None:
            continue
        total, pp = per_person(it.price, req, price_is_total)
        codes = []
        for name in it.airlines:
            code = name_to_code.get(name, name)
            if code not in codes:
                codes.append(code)
        first, last = it.flights[0], it.flights[-1]
        offers.append(Offer(
            provider=Provider.FAST_FLIGHTS, price_total=total, currency="EUR", per_person=pp,
            airlines=codes, stops=len(it.flights) - 1,
            duration_minutes=_minutes_between(first.departure, last.arrival),
            departs_at=_fmt(first.departure), arrives_at=_fmt(last.arrival),
            price_level=None, typical_low=None, typical_high=None, google_url=url,
            raw={"type": it.type, "airlines": it.airlines,
                 "legs": [{"from": l.from_airport.code, "to": l.to_airport.code, "dep": _fmt(l.departure),
                           "arr": _fmt(l.arrival), "duration": l.duration, "plane": l.plane_type} for l in it.flights]},
        ))
    return offers


class FastFlightsClient:
    provider = Provider.FAST_FLIGHTS

    def __init__(self, settings: Settings, fetch: Callable[[Query], ResultList] = get_flights,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.fetch = fetch
        self.sleep = sleep

    def build_query(self, req: SearchRequest) -> Query:
        ms = self.settings.max_stops
        return create_query(
            flights=[
                FlightQuery(date=req.outbound_date.isoformat(), from_airport=req.origin, to_airport=req.destination, max_stops=ms),
                FlightQuery(date=req.return_date.isoformat(), from_airport=req.destination, to_airport=req.origin, max_stops=ms),
            ],
            seat=SEAT[req.seat], trip="round-trip",
            passengers=Passengers(adults=req.adults, children=req.children),
            currency="EUR", language="en-US",
        )

    def search(self, req: SearchRequest, raw_dir: Path | None = None) -> SearchResult:
        q = self.build_query(req)
        self.sleep(self.settings.providers.backup_pause_seconds)
        try:
            results = self.fetch(q)
        except FlightsNotFound as e:
            raise ProviderError(f"fast_flights: no flights: {e}") from e
        except Exception as e:  # network, parse, layout change
            raise ProviderError(f"fast_flights: {type(e).__name__}: {e}") from e
        if not results:
            raise ProviderError("fast_flights: empty result")
        offers = filter_excluded(parse_results(results, req, q.url(), self.settings.providers.price_is_total),
                                 self.settings.excluded_airlines)
        return SearchResult(req, self.provider, offers, None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_fast_flights.py -v`
Expected: 4 PASSED. (`Query.flight_data`, `Query.passengers`, `get_trip_type()` and `get_seat_type()` were verified against fast-flights 3.1.0.)

- [ ] **Step 6: Commit**

```bash
git add vacation_planner/providers/fast_flights.py tests/fixtures/fast_flights_result.py tests/test_providers_fast_flights.py
git commit -m "feat: fast-flights backup provider"
```

---

### Task 10: Executor with fallback

**Files:**
- Create: `vacation_planner/providers/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `PlannedSearch`, `FlightClient`, `ProviderError`, `QuotaExhausted`, `Storage` (`start_run`, `save_result`, `record_search`, `finish_run`), `ProviderSettings`.
- Produces:

```python
@dataclass class ExecutionSummary: run_id: int; planned: int; ok: int; errors: int; skipped: int; fallbacks: int; status: str  # "ok" | "partial" | "empty"
def execute(planned: list[PlannedSearch], clients: Mapping[Provider, FlightClient], storage: Storage, providers: ProviderSettings, now: Callable[[], datetime], raw_dir: Path | None = None) -> ExecutionSummary
```

Rules:
1. `run_id = storage.start_run(now(), len(planned))`.
2. For each planned search, the provider is the planned one, unless it is the primary and the primary has been marked dead (quota) in this run, in which case it is the backup.
3. Call `client.search(req, raw_dir)`. On success `storage.save_result`.
4. On `QuotaExhausted` from the primary: mark primary dead, then retry once on the backup (if configured), counting a fallback. On `ProviderError` from the primary: retry once on the backup (if configured), counting a fallback.
5. If the backup fails, or there is no backup: record the search with status `error` (message from the exception). If the primary is dead and there is no backup: record status `skipped` without calling anything.
6. `status` = `"ok"` if every search is ok, `"empty"` if nothing was planned, else `"partial"`. `storage.finish_run(run_id, ok, status, now())`.
7. Never raise out of `execute` for provider errors; let unexpected exceptions propagate after `finish_run(..., "partial", ...)` in a `try/finally`? No: keep it simple, catch only `ProviderError` subclasses; anything else propagates and the run stays `running` (visible in the DB as a crash).

- [ ] **Step 1: Write the failing tests**

`tests/test_executor.py`:
```python
from datetime import date, datetime, timezone

from vacation_planner.config import ProviderSettings
from vacation_planner.models import PlannedSearch, Provider, SearchRequest, SeatClass
from vacation_planner.providers.executor import execute
from vacation_planner.providers.fake import FakeFlightClient
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def req(dest):
    return SearchRequest("herbst-2026", "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


class Primary(FakeFlightClient):
    provider = Provider.SERPAPI


class Backup(FakeFlightClient):
    provider = Provider.FAST_FLIGHTS


def settings(backup=Provider.FAST_FLIGHTS):
    return ProviderSettings(primary=Provider.SERPAPI, backup=backup)


def test_all_ok():
    db = Storage(":memory:")
    p, b = Primary(), Backup()
    planned = [PlannedSearch(req("BKK"), Provider.SERPAPI), PlannedSearch(req("DXB"), Provider.FAST_FLIGHTS)]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.fallbacks, s.status) == (2, 0, 0, 0, "ok")
    rows = db.searches_in_run(s.run_id)
    assert [(r.destination, r.provider) for r in rows] == [("BKK", Provider.SERPAPI), ("DXB", Provider.FAST_FLIGHTS)]


def test_primary_error_falls_back():
    db = Storage(":memory:")
    p, b = Primary(fail={("HAM", "BKK")}), Backup()
    s = execute([PlannedSearch(req("BKK"), Provider.SERPAPI)], {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.fallbacks, s.status) == (1, 1, "ok")
    assert db.searches_in_run(s.run_id)[0].provider is Provider.FAST_FLIGHTS


def test_quota_marks_primary_dead_for_rest_of_run():
    db = Storage(":memory:")
    p, b = Primary(quota_after=0), Backup()
    planned = [PlannedSearch(req(d), Provider.SERPAPI) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    # call 1 (BKK) ok, call 2 (DXB) raises quota -> fallback, MLE goes straight to backup
    assert (s.ok, s.fallbacks) == (3, 2)
    assert len(p.calls) == 2
    assert all(r.provider is Provider.FAST_FLIGHTS for r in db.searches_in_run(s.run_id)[1:])


def test_no_backup_records_error_and_skipped():
    db = Storage(":memory:")
    p = Primary(quota_after=0)
    planned = [PlannedSearch(req(d), Provider.SERPAPI) for d in ("BKK", "DXB", "MLE")]
    s = execute(planned, {Provider.SERPAPI: p}, db, settings(backup=None), lambda: NOW)
    assert (s.ok, s.errors, s.skipped, s.status) == (1, 1, 1, "partial")
    assert db.searches_in_run(s.run_id, "error")[0].error.startswith("fake quota")
    assert db.searches_in_run(s.run_id, "skipped")[0].destination == "MLE"


def test_backup_failure_is_error():
    db = Storage(":memory:")
    p, b = Primary(fail={("HAM", "BKK")}), Backup(fail={("HAM", "BKK")})
    s = execute([PlannedSearch(req("BKK"), Provider.SERPAPI)], {Provider.SERPAPI: p, Provider.FAST_FLIGHTS: b}, db, settings(), lambda: NOW)
    assert (s.ok, s.errors, s.status) == (0, 1, "partial")


def test_empty_plan():
    db = Storage(":memory:")
    s = execute([], {}, db, settings(), lambda: NOW)
    assert s.status == "empty" and s.planned == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write executor.py**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/providers/executor.py tests/test_executor.py
git commit -m "feat: search executor with primary/backup fallback"
```

---

### Task 11: Deal detection

**Files:**
- Create: `vacation_planner/deals.py`
- Test: `tests/test_deals.py`

**Interfaces:**
- Consumes: `Storage` (`searches_in_run`, `cheapest_offer`, `prior_cheapest_prices`, `prior_route_prices`, `insert_deal`, `last_notified_price`), `Config` (`deals` settings, `destination(code).max_price_per_person`), `DealReason`.
- Produces:

```python
@dataclass class DetectedDeal: deal_id: int; search: SearchRow; offer: OfferRow; reasons: list[DealReason]; score: float; median: float | None; notifiable: bool
def evaluate(price_total: float, per_person: float, price_level: str | None, history: list[float], route_history: list[float], max_pp: float | None, settings: DealSettings) -> tuple[list[DealReason], float | None]   # pure: reasons, median used
def detect_for_run(storage: Storage, run_id: int, config: Config, now: datetime) -> list[DetectedDeal]
```

Rules (from spec 5.6):
- History for `BELOW_MEDIAN` is the slot's own history if it has ≥ `min_history_points` entries, else the route history if that has ≥ `min_history_points`, else no median (rule skipped). `BELOW_MEDIAN` when `price_total <= median_ratio * median`.
- `GOOGLE_LOW` when `price_level == "low"`.
- `UNDER_MAX` when `max_pp` is set and `per_person <= max_pp`.
- `NEW_LOW` when slot history is non-empty and `price_total < min(history)`.
- Score = number of reasons + (1 − price/median) when a median exists.
- `notifiable` = last notified price for (slot, route, seat) is `None`, or `price_total <= renotify_drop_ratio * last_notified`.

- [ ] **Step 1: Write the failing tests**

`tests/test_deals.py`:
```python
from datetime import date, datetime, timezone

from vacation_planner.config import DealSettings, load_config
from vacation_planner.deals import detect_for_run, evaluate
from vacation_planner.models import DealReason, Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.storage import Storage

S = DealSettings(median_ratio=0.85, min_history_points=3, renotify_drop_ratio=0.95, lookahead_days=330)
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def test_evaluate_below_median_uses_slot_history_first():
    reasons, median = evaluate(800, 800 / 3, None, [1000, 1000, 1000], [500, 500, 500], None, S)
    assert reasons == [DealReason.BELOW_MEDIAN, DealReason.NEW_LOW] and median == 1000


def test_evaluate_falls_back_to_route_history():
    reasons, median = evaluate(800, 1, None, [1000], [1000, 1000, 1000], None, S)
    assert DealReason.BELOW_MEDIAN in reasons and median == 1000
    reasons, median = evaluate(800, 1, None, [1000], [1000], None, S)
    assert DealReason.BELOW_MEDIAN not in reasons and median is None


def test_evaluate_google_low_and_under_max():
    assert evaluate(900, 300, "low", [], [], 300, S)[0] == [DealReason.GOOGLE_LOW, DealReason.UNDER_MAX]
    assert evaluate(900, 301, "typical", [], [], 300, S)[0] == []


def req(dest="BKK", slot="herbst-2026"):
    return SearchRequest(slot, "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH"], 1, 800, "", "", level, None, None, "https://g", {})


def test_detect_for_run_records_deals_and_renotify_rule(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 3)
    for p in (9000, 9000, 9000):
        db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(p)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000, "low")]), NOW)  # 77% of median, under max 2200pp? 2333 no
    db.save_result(run, SearchResult(req("DXB"), Provider.SERPAPI, [offer(9000, "typical")]), NOW)  # DXB max 1500pp -> 3000pp no
    deals = detect_for_run(db, run, cfg, NOW)
    assert len(deals) == 1
    d = deals[0]
    assert d.search.destination == "BKK" and d.median == 9000
    assert set(d.reasons) == {DealReason.BELOW_MEDIAN, DealReason.GOOGLE_LOW, DealReason.NEW_LOW}
    assert d.notifiable is True and db.pending_deals()[0].id == d.deal_id
    db.mark_notified([d.deal_id], NOW)

    run2 = db.start_run(NOW, 1)
    db.save_result(run2, SearchResult(req(), Provider.SERPAPI, [offer(6900, "low")]), NOW)   # only 1.4% lower
    d2 = detect_for_run(db, run2, cfg, NOW)[0]
    assert d2.notifiable is False and db.pending_deals() == []

    run3 = db.start_run(NOW, 1)
    db.save_result(run3, SearchResult(req(), Provider.SERPAPI, [offer(6000, "low")]), NOW)   # >5% lower
    assert detect_for_run(db, run3, cfg, NOW)[0].notifiable is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_deals.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write deals.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median as _median

from .config import Config, DealSettings
from .models import DealReason
from .storage import OfferRow, SearchRow, Storage


@dataclass
class DetectedDeal:
    deal_id: int
    search: SearchRow
    offer: OfferRow
    reasons: list[DealReason]
    score: float
    median: float | None
    notifiable: bool


def evaluate(price_total: float, per_person: float, price_level: str | None, history: list[float],
             route_history: list[float], max_pp: float | None, settings: DealSettings) -> tuple[list[DealReason], float | None]:
    reasons: list[DealReason] = []
    median: float | None = None
    if len(history) >= settings.min_history_points:
        median = float(_median(history))
    elif len(route_history) >= settings.min_history_points:
        median = float(_median(route_history))
    if median is not None and price_total <= settings.median_ratio * median:
        reasons.append(DealReason.BELOW_MEDIAN)
    if price_level == "low":
        reasons.append(DealReason.GOOGLE_LOW)
    if max_pp is not None and per_person <= max_pp:
        reasons.append(DealReason.UNDER_MAX)
    if history and price_total < min(history):
        reasons.append(DealReason.NEW_LOW)
    return reasons, median


def detect_for_run(storage: Storage, run_id: int, config: Config, now: datetime) -> list[DetectedDeal]:
    s = config.settings.deals
    out: list[DetectedDeal] = []
    for search in storage.searches_in_run(run_id, status="ok"):
        offer = storage.cheapest_offer(search.id)
        if offer is None:
            continue
        history = storage.prior_cheapest_prices(search.slot_id, search.origin, search.destination, search.seat, search.id)
        route_history = storage.prior_route_prices(search.origin, search.destination, search.seat, search.id)
        max_pp = config.destination(search.destination).max_price_per_person if search.destination in config.destinations else None
        reasons, median = evaluate(offer.price_total, offer.per_person, offer.price_level, history, route_history, max_pp, s)
        if not reasons:
            continue
        score = len(reasons) + ((1 - offer.price_total / median) if median else 0.0)
        last = storage.last_notified_price(search.slot_id, search.origin, search.destination, search.seat)
        notifiable = last is None or offer.price_total <= s.renotify_drop_ratio * last
        deal_id = storage.insert_deal(offer.id, search.id, search.slot_id, reasons, score, now, notifiable)
        out.append(DetectedDeal(deal_id, search, offer, reasons, score, median, notifiable))
    out.sort(key=lambda d: -d.score)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_deals.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/deals.py tests/test_deals.py
git commit -m "feat: deal detection with history, Google level and max price rules"
```

---

### Task 12: HTML report

**Files:**
- Create: `vacation_planner/report.py`, `vacation_planner/templates/base.html`, `vacation_planner/templates/index.html`, `vacation_planner/templates/route.html`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Storage` (`latest_per_pair`, `route_observations`, `deals_in_run`, `search_by_id`, `offer_by_id`, `searches_by_provider_since`), `Config` (slots, destinations, budget), `free_window`.
- Produces:

```python
@dataclass class RouteSummary: slot: Slot; destination: Destination; seat: SeatClass; best: Observation; median: float | None; ratio: float | None; is_deal: bool; page: str  # relative link to route page
@dataclass class ReportData: generated_at: datetime; new_deals: list[DetectedDeal-like rows]; slots: list[tuple[Slot, Window, list[RouteSummary]]]; serpapi_used_this_month: int; serpapi_budget: int
def build_report(storage: Storage, config: Config, now: datetime, last_run_id: int | None) -> ReportData
def render(storage: Storage, config: Config, out_dir: Path, now: datetime, last_run_id: int | None = None) -> list[Path]   # writes index.html and routes/*.html, returns written paths
def route_page_name(slot_id: str, origin: str, destination: str, seat: SeatClass) -> str  # "routes/herbst-2026-HAM-BKK-business.html"
```

Behaviour:
- Slots shown: every slot in config whose `end >= now.date()`, in date order, including slots without targets (rendered with "no targets configured").
- Per slot, per (origin, destination, seat): all `latest_per_pair` observations for that route; `best` is the cheapest of them; `median` is the median of `route_observations` prices (all history for the slot route) when ≥ 3 points; `ratio = best.price / median`; `is_deal` when a deal row exists for `best.offer.id`.
- New deals section: `deals_in_run(last_run_id)` if given, else empty, joined with search and offer rows, sorted by score desc.
- Footer: generated time, SerpApi searches used since the first day of the current month vs `serpapi_per_month`.
- Route page: table of `route_observations`, newest first: searched at, outbound, return, nights, price total, per person, airlines, stops, level, provider, link.
- Templates use only inline CSS; no JavaScript. Prices shown as whole euros with thousands separators (`"{:,.0f} €"`).

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:
```python
from datetime import date, datetime, timezone
from pathlib import Path

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.report import build_report, render, route_page_name
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)


def req(dest="BKK", out=date(2026, 10, 17), ret=date(2026, 10, 31), seat=SeatClass.BUSINESS):
    return SearchRequest("herbst-2026", "HAM", dest, out, ret, seat, 2, 1)


def offer(price, level=None):
    return Offer(Provider.SERPAPI, price, "EUR", price / 3, ["LH", "TG"], 1, 875, "2026-10-17 10:35", "2026-10-18 06:10",
                 level, 7200, 9800, "https://www.google.com/travel/flights?x", {})


def seeded(config_dir):
    cfg = load_config(config_dir, env={})
    db = Storage(":memory:")
    old = db.start_run(NOW, 3)
    for p in (9000, 8800, 9100):
        db.save_result(old, SearchResult(req(), Provider.SERPAPI, [offer(p)]), NOW)
    run = db.start_run(NOW, 2)
    db.save_result(run, SearchResult(req(), Provider.SERPAPI, [offer(7000, "low")]), NOW)
    db.save_result(run, SearchResult(req(out=date(2026, 10, 18), ret=date(2026, 11, 1)), Provider.SERPAPI, [offer(7600)]), NOW)
    db.finish_run(run, 2, "ok", NOW)
    detect_for_run(db, run, cfg, NOW)
    return cfg, db, run


def test_route_page_name():
    assert route_page_name("herbst-2026", "HAM", "BKK", SeatClass.BUSINESS) == "routes/herbst-2026-HAM-BKK-business.html"


def test_build_report_summarises_best_and_median(config_dir):
    cfg, db, run = seeded(config_dir)
    data = build_report(db, cfg, NOW, last_run_id=run)
    assert len(data.new_deals) == 1
    herbst = next(routes for slot, window, routes in data.slots if slot.id == "herbst-2026")
    r = herbst[0]
    assert r.destination.code == "BKK" and r.best.offer.price_total == 7000
    assert r.median == 8800 and round(r.ratio, 3) == 0.795 and r.is_deal is True   # median of 9000, 8800, 9100, 7000, 7600
    assert data.serpapi_used_this_month == 5 and data.serpapi_budget == 100
    # past slots are hidden, future slots without targets are shown
    ids = [slot.id for slot, _, _ in data.slots]
    assert "herbst-2027" in ids and ids == sorted(ids, key=lambda i: next(s.start for s in cfg.slots if s.id == i))


def test_render_writes_index_and_route_pages(config_dir, tmp_path):
    cfg, db, run = seeded(config_dir)
    written = render(db, cfg, tmp_path, NOW, last_run_id=run)
    index = (tmp_path / "index.html").read_text()
    assert "Herbstferien 2026" in index and "7,000 €" in index and "2,333 €" in index
    assert "routes/herbst-2026-HAM-BKK-business.html" in index
    route = (tmp_path / "routes" / "herbst-2026-HAM-BKK-business.html").read_text()
    assert route.count("<tr") >= 6  # header + 5 observations
    assert all(p.exists() for p in written)


def test_render_with_empty_db(config_dir, tmp_path):
    cfg = load_config(config_dir, env={})
    render(Storage(":memory:"), cfg, tmp_path, NOW)
    assert "No deals" in (tmp_path / "index.html").read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the templates**

`vacation_planner/templates/base.html`:
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Vacation Planner{% endblock %}</title>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#e3e3e3; --deal:#0a7d32; --dealbg:#e6f6ec; --low:#0a7d32; --high:#b42318; }
  @media (prefers-color-scheme: dark) { :root { --bg:#111; --fg:#eee; --muted:#aaa; --line:#333; --dealbg:#10301c; } }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 4px; } h2 { font-size: 1.2rem; margin: 28px 0 8px; }
  .muted { color: var(--muted); }
  table { width:100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align:left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
  td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
  .wrap { overflow-x:auto; }
  .deal { background: var(--dealbg); }
  .badge { display:inline-block; padding:1px 6px; border-radius: 4px; font-size: 12px; border:1px solid var(--line); }
  .badge.deal { color: var(--deal); border-color: var(--deal); }
  .level-low { color: var(--low); } .level-high { color: var(--high); }
  footer { margin-top: 32px; font-size: 13px; color: var(--muted); }
  a { color: inherit; }
</style>
</head>
<body><main>
{% block body %}{% endblock %}
<footer>Generated {{ data.generated_at.strftime('%Y-%m-%d %H:%M UTC') }} · SerpApi searches this month: {{ data.serpapi_used_this_month }} / {{ data.serpapi_budget }}</footer>
</main></body>
</html>
```

`vacation_planner/templates/index.html`:
```html
{% extends "base.html" %}
{% block title %}Vacation Planner{% endblock %}
{% block body %}
<h1>Vacation Planner</h1>
<p class="muted">Hamburg school holidays · 2 adults + 1 child · Business outside Europe</p>

<h2>New deals</h2>
{% if data.new_deals %}
<div class="wrap"><table>
<tr><th>Slot</th><th>Route</th><th>Dates</th><th class="num">Total</th><th class="num">Per person</th><th>Airlines</th><th>Why</th><th></th></tr>
{% for d in data.new_deals %}
<tr class="deal">
  <td>{{ slot_name(d.search.slot_id) }}</td>
  <td>{{ d.search.origin }} → {{ d.search.destination }} <span class="badge">{{ d.search.seat.value }}</span></td>
  <td>{{ d.search.outbound_date }} – {{ d.search.return_date }} ({{ (d.search.return_date - d.search.outbound_date).days }} n)</td>
  <td class="num">{{ money(d.offer.price_total) }}</td>
  <td class="num">{{ money(d.offer.per_person) }}</td>
  <td>{{ d.offer.airlines|join(', ') }} · {{ d.offer.stops }} stop{{ '' if d.offer.stops == 1 else 's' }}</td>
  <td>{% for r in d.reasons %}<span class="badge deal">{{ r.value.replace('_', ' ') }}</span> {% endfor %}</td>
  <td><a href="{{ d.offer.google_url }}">Google Flights</a></td>
</tr>
{% endfor %}
</table></div>
{% else %}
<p class="muted">No deals in the last run.</p>
{% endif %}

{% for slot, window, routes in data.slots %}
<h2>{{ slot.name }} <span class="muted">· free {{ window.start }} – {{ window.end }} ({{ window.days }} days)</span></h2>
{% if not slot.targets %}
<p class="muted">No targets configured.</p>
{% elif not routes %}
<p class="muted">Not searched yet.</p>
{% else %}
<div class="wrap"><table>
<tr><th>Destination</th><th>Dates</th><th class="num">Total</th><th class="num">Per person</th><th class="num">vs median</th><th>Level</th><th>Airlines</th><th>Searched</th><th></th></tr>
{% for r in routes %}
<tr class="{{ 'deal' if r.is_deal else '' }}">
  <td>{{ r.destination.name }} ({{ r.destination.code }}) <span class="badge">{{ r.seat.value }}</span>{% if r.is_deal %} <span class="badge deal">deal</span>{% endif %}</td>
  <td>{{ r.best.search.outbound_date }} – {{ r.best.search.return_date }}</td>
  <td class="num">{{ money(r.best.offer.price_total) }}</td>
  <td class="num">{{ money(r.best.offer.per_person) }}</td>
  <td class="num">{% if r.ratio %}{{ '%+.0f%%'|format((r.ratio - 1) * 100) }}{% else %}–{% endif %}</td>
  <td class="level-{{ r.best.offer.price_level or 'none' }}">{{ r.best.offer.price_level or '–' }}</td>
  <td>{{ r.best.offer.airlines|join(', ') }} · {{ r.best.offer.stops }} stop{{ '' if r.best.offer.stops == 1 else 's' }}</td>
  <td>{{ r.best.search.requested_at.strftime('%Y-%m-%d') }} · {{ r.best.search.provider.value }}</td>
  <td><a href="{{ r.best.offer.google_url }}">Book</a> · <a href="{{ r.page }}">History</a></td>
</tr>
{% endfor %}
</table></div>
{% endif %}
{% endfor %}
{% endblock %}
```

`vacation_planner/templates/route.html`:
```html
{% extends "base.html" %}
{% block title %}{{ slot.name }} · {{ origin }} → {{ destination.name }}{% endblock %}
{% block body %}
<p><a href="../index.html">← Overview</a></p>
<h1>{{ origin }} → {{ destination.name }} ({{ destination.code }}) · {{ seat.value }}</h1>
<p class="muted">{{ slot.name }}{% if median %} · median {{ money(median) }} over {{ observations|length }} observations{% endif %}</p>
<div class="wrap"><table>
<tr><th>Searched</th><th>Outbound</th><th>Return</th><th class="num">Nights</th><th class="num">Total</th><th class="num">Per person</th><th>Airlines</th><th class="num">Stops</th><th>Level</th><th>Provider</th><th></th></tr>
{% for o in observations %}
<tr>
  <td>{{ o.search.requested_at.strftime('%Y-%m-%d') }}</td>
  <td>{{ o.search.outbound_date }}</td><td>{{ o.search.return_date }}</td>
  <td class="num">{{ (o.search.return_date - o.search.outbound_date).days }}</td>
  <td class="num">{{ money(o.offer.price_total) }}</td>
  <td class="num">{{ money(o.offer.per_person) }}</td>
  <td>{{ o.offer.airlines|join(', ') }}</td>
  <td class="num">{{ o.offer.stops }}</td>
  <td class="level-{{ o.offer.price_level or 'none' }}">{{ o.offer.price_level or '–' }}</td>
  <td>{{ o.search.provider.value }}</td>
  <td><a href="{{ o.offer.google_url }}">Google Flights</a></td>
</tr>
{% endfor %}
</table></div>
{% endblock %}
```

- [ ] **Step 4: Write report.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median as _median

from jinja2 import Environment, PackageLoader, select_autoescape

from .calendar import Window, free_window
from .config import Config
from .models import Cabin, Destination, Provider, SeatClass, Slot
from .storage import DealRow, Observation, OfferRow, SearchRow, Storage


@dataclass
class RouteSummary:
    slot: Slot
    destination: Destination
    seat: SeatClass
    best: Observation
    median: float | None
    ratio: float | None
    is_deal: bool
    page: str


@dataclass
class NewDeal:
    deal: DealRow
    search: SearchRow
    offer: OfferRow

    @property
    def reasons(self):
        return self.deal.reasons


@dataclass
class ReportData:
    generated_at: datetime
    new_deals: list[NewDeal]
    slots: list[tuple[Slot, Window, list[RouteSummary]]]
    serpapi_used_this_month: int
    serpapi_budget: int


def route_page_name(slot_id: str, origin: str, destination: str, seat: SeatClass) -> str:
    return f"routes/{slot_id}-{origin}-{destination}-{seat.value}.html"


def money(v: float) -> str:
    return f"{v:,.0f} €"


def _route_median(storage: Storage, slot_id: str, origin: str, dest: str, seat: SeatClass) -> tuple[list[Observation], float | None]:
    obs = storage.route_observations(slot_id, origin, dest, seat)
    prices = [o.offer.price_total for o in obs]
    return obs, (float(_median(prices)) if len(prices) >= 3 else None)


def build_report(storage: Storage, config: Config, now: datetime, last_run_id: int | None) -> ReportData:
    deal_offer_ids: set[int] = set()
    new_deals: list[NewDeal] = []
    if last_run_id is not None:
        for d in storage.deals_in_run(last_run_id):
            deal_offer_ids.add(d.offer_id)
            new_deals.append(NewDeal(d, storage.search_by_id(d.search_id), storage.offer_by_id(d.offer_id)))
        new_deals.sort(key=lambda n: -n.deal.score)

    bd = config.settings.bridge_days
    slots = []
    for slot in config.slots:
        if slot.end < now.date():
            continue
        window = free_window(slot, bd.before, bd.after)
        by_route: dict[tuple[str, str, SeatClass], list[Observation]] = {}
        for o in storage.latest_per_pair(slot.id):
            by_route.setdefault((o.search.origin, o.search.destination, o.search.seat), []).append(o)
        routes = []
        for (origin, dest, seat), obs in by_route.items():
            best = min(obs, key=lambda o: o.offer.price_total)
            _, med = _route_median(storage, slot.id, origin, dest, seat)
            destination = config.destinations.get(dest) or Destination(dest, dest, Cabin.ANY)  # removed from catalogue but still in history
            routes.append(RouteSummary(
                slot=slot, destination=destination, seat=seat, best=best, median=med,
                ratio=(best.offer.price_total / med) if med else None,
                is_deal=best.offer.id in deal_offer_ids,
                page=route_page_name(slot.id, origin, dest, seat)))
        routes.sort(key=lambda r: r.best.offer.price_total)
        slots.append((slot, window, routes))

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = storage.searches_by_provider_since(Provider.SERPAPI, month_start)
    return ReportData(now, new_deals, slots, used, config.settings.budget.serpapi_per_month)


def _env() -> Environment:
    env = Environment(loader=PackageLoader("vacation_planner", "templates"), autoescape=select_autoescape(["html"]))
    env.globals["money"] = money
    return env


def render(storage: Storage, config: Config, out_dir: Path, now: datetime, last_run_id: int | None = None) -> list[Path]:
    data = build_report(storage, config, now, last_run_id)
    env = _env()
    names = {s.id: s.name for s in config.slots}
    env.globals["slot_name"] = lambda sid: names.get(sid, sid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "routes").mkdir(exist_ok=True)
    written = []
    index = out_dir / "index.html"
    index.write_text(env.get_template("index.html").render(data=data))
    written.append(index)
    tpl = env.get_template("route.html")
    for slot, _window, routes in data.slots:
        for r in routes:
            obs, med = _route_median(storage, slot.id, r.best.search.origin, r.destination.code, r.seat)
            p = out_dir / r.page
            p.write_text(tpl.render(data=data, slot=slot, origin=r.best.search.origin, destination=r.destination,
                                    seat=r.seat, observations=obs, median=med))
            written.append(p)
    return written
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add vacation_planner/report.py vacation_planner/templates tests/test_report.py
git commit -m "feat: static HTML report with per-slot tables and route history"
```

---

### Task 13: Email notifier

**Files:**
- Create: `vacation_planner/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `Storage` (`pending_deals`, `search_by_id`, `offer_by_id`, `mark_notified`), `Config` (`settings.email.mode`, `secrets`, slot names).
- Produces:

```python
def compose(deals: list[NewDeal], config: Config, report_url: str | None) -> tuple[str, str, str]   # subject, text body, html body
def send_pending(storage: Storage, config: Config, now: datetime, report_url: str | None = None, smtp_factory: Callable[[str, int], smtplib.SMTP] = smtplib.SMTP) -> int   # number of deals notified
```

Behaviour: mode `never` → return 0 without touching anything. Mode `deals_only` with no pending deals → 0. Mode `always` with no deals sends a "no new deals" mail. Missing SMTP host, from, or recipients → log a warning and return 0 (deals remain pending). Sends one multipart mail (text + html) via STARTTLS with login when user is set. Marks deals notified only after `send_message` returns. Any `smtplib` or `OSError` failure → log, return 0, nothing marked.

- [ ] **Step 1: Write the failing tests**

`tests/test_notify.py`:
```python
from datetime import date, datetime, timezone

from vacation_planner.config import load_config
from vacation_planner.deals import detect_for_run
from vacation_planner.models import Offer, Provider, SearchRequest, SearchResult, SeatClass
from vacation_planner.notify import compose, send_pending
from vacation_planner.report import NewDeal
from vacation_planner.storage import Storage

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)
ENV = {"SMTP_HOST": "smtp.test", "SMTP_PORT": "2525", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
       "MAIL_FROM": "planner@test", "MAIL_TO": "me@test"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.sent, self.logged_in, self.tls = host, port, [], None, False
        FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): self.tls = True
    def login(self, u, p): self.logged_in = (u, p)
    def send_message(self, msg): self.sent.append(msg)


class FailingSMTP(FakeSMTP):
    def send_message(self, msg): raise OSError("smtp down")


def with_deal(config_dir, env=ENV):
    cfg = load_config(config_dir, env=env)
    db = Storage(":memory:")
    run = db.start_run(NOW, 1)
    req = SearchRequest("herbst-2026", "HAM", "BKK", date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)
    db.save_result(run, SearchResult(req, Provider.SERPAPI, [Offer(Provider.SERPAPI, 6000, "EUR", 2000, ["LH"], 1, 800, "", "", "low", None, None, "https://g/1", {})]), NOW)
    detect_for_run(db, run, cfg, NOW)
    return cfg, db


def test_compose_mentions_route_price_and_link(config_dir):
    cfg, db = with_deal(config_dir)
    d = db.pending_deals()[0]
    subject, text, html = compose([NewDeal(d, db.search_by_id(d.search_id), db.offer_by_id(d.offer_id))], cfg, "https://x/report")
    assert "1 new flight deal" in subject
    assert "HAM → BKK" in text and "6,000 €" in text and "https://g/1" in text and "https://x/report" in text
    assert "Herbstferien 2026" in html and "<a href=\"https://g/1\"" in html


def test_send_pending_sends_and_marks(config_dir):
    cfg, db = with_deal(config_dir)
    FakeSMTP.instances.clear()
    n = send_pending(db, cfg, NOW, smtp_factory=FakeSMTP)
    assert n == 1 and db.pending_deals() == []
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.tls, smtp.logged_in) == ("smtp.test", 2525, True, ("u", "p"))
    msg = smtp.sent[0]
    assert msg["To"] == "me@test" and msg["From"] == "planner@test"


def test_send_failure_keeps_deals_pending(config_dir):
    cfg, db = with_deal(config_dir)
    assert send_pending(db, cfg, NOW, smtp_factory=FailingSMTP) == 0
    assert len(db.pending_deals()) == 1


def test_missing_smtp_config_is_noop(config_dir):
    cfg, db = with_deal(config_dir, env={})
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert FakeSMTP.instances == [] and len(db.pending_deals()) == 1


def test_mode_never_and_always(config_dir):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("mode: deals_only", "mode: never"))
    cfg, db = with_deal(config_dir)
    FakeSMTP.instances.clear()
    assert send_pending(db, cfg, NOW, smtp_factory=FakeSMTP) == 0 and FakeSMTP.instances == []

    st.write_text(st.read_text().replace("mode: never", "mode: always"))
    cfg = load_config(config_dir, env=ENV)
    assert send_pending(Storage(":memory:"), cfg, NOW, smtp_factory=FakeSMTP) == 0
    assert "No new deals" in FakeSMTP.instances[0].sent[0]["Subject"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write notify.py**

```python
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from html import escape
from typing import Callable

from .config import Config
from .report import NewDeal, money
from .storage import Storage

log = logging.getLogger(__name__)


def compose(deals: list[NewDeal], config: Config, report_url: str | None) -> tuple[str, str, str]:
    names = {s.id: s.name for s in config.slots}
    if not deals:
        subject = "Vacation Planner: No new deals"
    else:
        subject = f"Vacation Planner: {len(deals)} new flight deal{'s' if len(deals) != 1 else ''}"

    lines, rows = [], []
    for d in deals:
        s, o = d.search, d.offer
        why = ", ".join(r.value.replace("_", " ") for r in d.reasons)
        lines.append(
            f"- {names.get(s.slot_id, s.slot_id)}: {s.origin} → {s.destination} ({s.seat.value}), "
            f"{s.outbound_date} – {s.return_date}: {money(o.price_total)} total, {money(o.per_person)} p.p., "
            f"{', '.join(o.airlines)}, {o.stops} stop(s). {why}. {o.google_url}")
        rows.append(
            f"<tr><td>{escape(names.get(s.slot_id, s.slot_id))}</td><td>{s.origin} → {s.destination} ({s.seat.value})</td>"
            f"<td>{s.outbound_date} – {s.return_date}</td><td align='right'>{money(o.price_total)}</td>"
            f"<td align='right'>{money(o.per_person)}</td><td>{escape(', '.join(o.airlines))}, {o.stops} stop(s)</td>"
            f"<td>{escape(why)}</td><td><a href=\"{escape(o.google_url, quote=True)}\">Google Flights</a></td></tr>")

    text = "\n".join(lines) if lines else "No new deals this run."
    if report_url:
        text += f"\n\nFull report: {report_url}"
    html = "<p>No new deals this run.</p>" if not rows else (
        "<table cellpadding='6' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
        "<tr><th>Slot</th><th>Route</th><th>Dates</th><th>Total</th><th>Per person</th><th>Flight</th><th>Why</th><th></th></tr>"
        + "".join(rows) + "</table>")
    if report_url:
        html += f"<p><a href=\"{escape(report_url, quote=True)}\">Full report</a></p>"
    return subject, text, html


def send_pending(storage: Storage, config: Config, now: datetime, report_url: str | None = None,
                 smtp_factory: Callable[[str, int], smtplib.SMTP] = smtplib.SMTP) -> int:
    mode = config.settings.email.mode
    if mode == "never":
        return 0
    pending = storage.pending_deals()
    if mode == "deals_only" and not pending:
        return 0
    sec = config.secrets
    if not (sec.smtp_host and sec.mail_from and sec.mail_to):
        log.warning("email not configured (SMTP_HOST, MAIL_FROM, MAIL_TO); %d deals stay pending", len(pending))
        return 0

    deals = [NewDeal(d, storage.search_by_id(d.search_id), storage.offer_by_id(d.offer_id)) for d in pending]
    deals.sort(key=lambda n: -n.deal.score)
    subject, text, html = compose(deals, config, report_url)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sec.mail_from, ", ".join(sec.mail_to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtp_factory(sec.smtp_host, sec.smtp_port) as smtp:
            smtp.starttls()
            if sec.smtp_user:
                smtp.login(sec.smtp_user, sec.smtp_password or "")
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("email failed: %s", e)
        return 0
    storage.mark_notified([d.deal.id for d in deals], now)
    return len(deals)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_notify.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add vacation_planner/notify.py tests/test_notify.py
git commit -m "feat: email notifier for pending deals"
```

---

### Task 14: CLI and end-to-end test

**Files:**
- Create: `vacation_planner/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: typer app `vacation_planner.cli:app` with commands `holidays`, `plan`, `scan`, `report`, `notify`, `run`. Shared options: `--config-dir` (default `config`), `--db` (default `data/planner.sqlite`), `--fake` (use `FakeFlightClient` for every provider; for local dry runs and the e2e test), `--today YYYY-MM-DD` (override the clock). `scan`/`run` take `--limit N` (cap planned searches). `run` takes `--report-url` (passed to the email).

```python
def build_clients(config: Config, fake: bool) -> dict[Provider, FlightClient]   # SerpApi needs secrets.serpapi_key, else raises typer.BadParameter unless fake
def do_scan(config, storage, today, now, limit, fake, raw_dir) -> tuple[ExecutionSummary, list[DetectedDeal]]
```

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
from pathlib import Path

from typer.testing import CliRunner

from vacation_planner.cli import app
from vacation_planner.storage import Storage

runner = CliRunner()


def common(config_dir: Path, tmp_path: Path):
    return ["--config-dir", str(config_dir), "--db", str(tmp_path / "p.sqlite"), "--today", "2026-09-21"]


def test_holidays_lists_windows(config_dir, tmp_path):
    r = runner.invoke(app, common(config_dir, tmp_path) + ["holidays"])
    assert r.exit_code == 0, r.output
    assert "Herbstferien 2026" in r.output and "2026-10-17" in r.output and "BKK" in r.output


def test_plan_is_dry(config_dir, tmp_path):
    r = runner.invoke(app, common(config_dir, tmp_path) + ["plan"])
    assert r.exit_code == 0, r.output
    assert "serpapi" in r.output and "HAM" in r.output and "BKK" in r.output
    assert not (tmp_path / "p.sqlite").exists() or Storage(tmp_path / "p.sqlite").searches_in_run(1) == []


def test_scan_requires_key_unless_fake(config_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    r = runner.invoke(app, common(config_dir, tmp_path) + ["scan"])
    assert r.exit_code != 0 and "SERPAPI_KEY" in r.output


def test_run_end_to_end_with_fake(config_dir, tmp_path):
    out = tmp_path / "site"
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("output_dir: docs/site", f"output_dir: {out}"))
    r = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r.exit_code == 0, r.output
    assert "searches: 5 ok" in r.output
    assert (out / "index.html").exists()
    db = Storage(tmp_path / "p.sqlite")
    assert len(db.searches_in_run(1)) == 5
    # second run: same prices -> no NEW_LOW, but UNDER_MAX deals still exist -> deals detected
    r2 = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r2.exit_code == 0 and "deals:" in r2.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write cli.py**

```python
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
```

Using a fixed `now` for the whole run is intentional so all searches in a run share a timestamp.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 4 PASSED. The e2e run uses `--limit 5` so the fake handles 3 herbst-2026 pairs (7, 8, 9 nights) and 2 weihnachten-2026 pairs; the fake price `1000 + 100*nights` gives 1,700–2,000 € total, roughly 570–670 € per person, under the BKK and MLE max prices, so `UNDER_MAX` deals appear in both runs.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Try it by hand**

Run: `uv run vacation-planner --today 2026-09-21 holidays && uv run vacation-planner plan && uv run vacation-planner --db /tmp/vp-fake.sqlite run --fake --limit 5 && open docs/site/index.html`
Expected: holiday list, ~18 planned searches all `serpapi`, a fake run summary, and the report opens in the browser. Then `git checkout -- docs/site 2>/dev/null; rm -rf docs/site` so the fake report is not committed (it is generated on CI).

- [ ] **Step 7: Commit**

```bash
git add vacation_planner/cli.py tests/test_cli.py
git commit -m "feat: CLI with plan/scan/report/notify/run"
```

---

### Task 15: GitHub Actions workflow and Pages

**Files:**
- Create: `.github/workflows/scan.yml`
- Modify: `README.md` (setup section), `.gitignore` (add `data/raw/` if not present)

**Interfaces:** none (CI only).

- [ ] **Step 1: Write the workflow**

`.github/workflows/scan.yml`:
```yaml
name: scan

on:
  schedule:
    - cron: "0 5 * * 1"   # Mondays 05:00 UTC = 4 runs/month, matches budget.runs_per_month
  workflow_dispatch:
    inputs:
      limit:
        description: "Max searches this run (empty = full plan)"
        required: false
        default: ""

concurrency:
  group: scan
  cancel-in-progress: false

permissions:
  contents: write
  pages: write
  id-token: write

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync --frozen
      - name: Run planner
        env:
          SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          MAIL_FROM: ${{ secrets.MAIL_FROM }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
        run: |
          LIMIT="${{ github.event.inputs.limit }}"
          ARGS=""
          if [ -n "$LIMIT" ]; then ARGS="--limit $LIMIT"; fi
          uv run vacation-planner run $ARGS --report-url "https://${{ github.repository_owner }}.github.io/${{ github.event.repository.name }}/" | tee run.log
      - name: Upload raw responses
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: raw-${{ github.run_id }}
          path: data/raw
          retention-days: 30
          if-no-files-found: ignore
      - name: Commit data and report
        run: |
          git config user.name "vacation-planner[bot]"
          git config user.email "vacation-planner@users.noreply.github.com"
          git add data/planner.sqlite docs/site
          if git diff --cached --quiet; then echo "no changes"; exit 0; fi
          SUMMARY=$(grep -m1 '^run ' run.log | sed 's/^run [0-9]*: //')
          git commit -m "scan: $(date -u +%F) ($SUMMARY)"
          git push
      - uses: actions/upload-pages-artifact@v3
        with:
          path: docs/site
  deploy:
    needs: scan
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Update README with the CI setup**

Append to `README.md`:
```markdown
## GitHub Actions

1. Push the repo to GitHub (public, so Pages is free).
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Secrets and variables → Actions: add `SERPAPI_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`.
4. Actions → scan → Run workflow (optionally with a limit) for a first run. The report appears at `https://<owner>.github.io/<repo>/`.

Runs happen Mondays 05:00 UTC. Change the cron and `budget.runs_per_month` together.

## Config

- `config/holidays.yaml` — the slots and, per slot, which destinations to search (`targets`). Only listed targets are ever searched.
- `config/destinations.yaml` — the catalogue; `cabin: business` for long haul, `cabin: any` for Europe.
- `config/settings.yaml` — origins, trip length, budget, providers, deal thresholds.
```

- [ ] **Step 3: Validate the workflow file**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/scan.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/scan.yml README.md .gitignore
git commit -m "ci: weekly scan workflow with Pages deploy"
```

---

### Task 16: Live verification and recorded fixture

This task needs a SerpApi key in `.env` and network access. It spends 1–2 of the 100 free monthly searches.

**Files:**
- Modify: `tests/fixtures/serpapi_ham_bkk.json` (replace synthetic with recorded), possibly `config/settings.yaml` (`price_is_total`), `tests/test_providers_serpapi.py` (expected numbers)

- [ ] **Step 1: One real SerpApi search**

Run: `uv run vacation-planner --db /tmp/vp-live.sqlite scan --limit 1 -v`
Expected: `run 1: searches: 1 ok ...`. Then inspect `data/raw/serpapi_HAM-BKK_*.json`:
- confirm `best_flights[].price` exists and `price_insights.price_level` is one of low/typical/high;
- open `search_metadata.google_flights_url` in a browser with the same passengers and compare: if the page's price equals `price` it is the **total** (keep `price_is_total: true`); if the page shows `price` per traveller, set `price_is_total: false` in `config/settings.yaml` and in the spec section 2.1.

- [ ] **Step 2: Replace the synthetic fixture**

Copy the raw file to `tests/fixtures/serpapi_ham_bkk.json`. Strip nothing but, if the file is over ~200 KB, delete `price_insights.price_history` and all but the first three `other_flights` entries. Update the numeric expectations in `test_parse_response_reads_both_lists_and_insights` and `test_search_filters_excluded_airlines_and_saves_raw` to the recorded values; if no `AI` itinerary is present the "dropped" assertion becomes an equality with the full list. Keep `test_airline_codes` pointing at `best_flights[0]`.

- [ ] **Step 3: One fast-flights search**

Run: `uv run python -c "
from datetime import date
from vacation_planner.config import load_config
from vacation_planner.models import SearchRequest, SeatClass
from vacation_planner.providers.fast_flights import FastFlightsClient
from pathlib import Path
cfg = load_config(Path('config'))
c = FastFlightsClient(cfg.settings)
r = c.search(SearchRequest('herbst-2026','HAM','BKK',date(2026,10,17),date(2026,10,31),SeatClass.BUSINESS,2,1))
for o in r.offers[:5]: print(o.price_total, o.per_person, o.airlines, o.stops, o.departs_at, o.arrives_at)
print(r.offers[0].google_url)
"`
Expected: a handful of Business offers with plausible prices in the same range as the SerpApi result, IATA codes as airlines, and a URL that opens the same search in a browser. If it raises `ProviderError` with a parse error, the library's page layout parsing has drifted; set `backup: null` in `config/settings.yaml`, note it in the README, and continue (the primary still works).

- [ ] **Step 4: Run the suite and commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add tests/fixtures/serpapi_ham_bkk.json tests/test_providers_serpapi.py config/settings.yaml
git commit -m "test: record real SerpApi fixture; verify price semantics"
```

- [ ] **Step 5: Clean up**

Run: `rm -rf data/raw /tmp/vp-live.sqlite` (raw dir is git-ignored anyway).

---

## Self-review notes

- Spec coverage: config (T3), calendar (T4), planner incl. per-slot targets and provider split (T6), SerpApi (T8), fast-flights (T9), executor with fallback and quota handling (T10), storage schema and views (T5), deals rules and renotify (T11), report index and route pages (T12), email (T13), CLI commands (T14), CI cron/commit/artifact/Pages (T15), live verification of price semantics (T16). Error-handling rules from spec §7 are implemented in T3 (config), T8/T9 (retries, quota), T10 (skipped/error/partial), T13 (email never fails the run).
- Not covered by design: hotels, web UI, multiple origins beyond the config list (the list is supported, nothing else).
- Type consistency checked: `Storage.latest_per_pair`, `route_observations`, `searches_in_run(run_id, status)`, `Observation(search, offer)`, `NewDeal(deal, search, offer)` and `money()` are used with the same names in T5, T11, T12, T13, T14.
