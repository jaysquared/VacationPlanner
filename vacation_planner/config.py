from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Literal, Mapping

import yaml
from pydantic import BaseModel, ValidationError, ValidationInfo, field_validator, model_validator

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
    origins: list[str] | None = None


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


class ProviderEntry(BaseModel):
    name: Provider
    monthly_budget: int | None = None   # None = unlimited, rate-limited by pause_seconds instead
    pause_seconds: float = 0.0


class ProviderSettings(BaseModel):
    """The providers to use, best first. Each search goes to the first one with budget left."""

    order: list[ProviderEntry]
    # Per provider: each is verified independently, so one flag for all of them would be a guess.
    price_is_total: dict[Provider, bool] = {}

    @field_validator("order")
    @classmethod
    def _non_empty_and_unique(cls, v: list[ProviderEntry]) -> list[ProviderEntry]:
        if not v:
            raise ValueError("list at least one provider")
        seen: set[Provider] = set()
        for e in v:
            if e.name in seen:
                raise ValueError(f"duplicate provider {e.name.value}")
            seen.add(e.name)
        return v

    @field_validator("price_is_total", mode="before")
    @classmethod
    def _expand_bare_bool(cls, v: object, info: ValidationInfo) -> object:
        if isinstance(v, bool):   # older config shape: one flag for every provider
            return {e.name: v for e in (info.data.get("order") or [])}
        return v

    @model_validator(mode="after")
    def _default_listed_providers_to_total(self) -> "ProviderSettings":
        for e in self.order:
            self.price_is_total.setdefault(e.name, True)
        return self

    def entry(self, p: Provider) -> ProviderEntry:
        for e in self.order:
            if e.name is p:
                return e
        raise KeyError(p)

    def budgeted(self) -> list[ProviderEntry]:
        return [e for e in self.order if e.monthly_budget is not None]


class BudgetSettings(BaseModel):
    runs_per_month: int
    max_searches_per_run: int
    max_pairs_per_route_per_run: int

    @field_validator("runs_per_month")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v


class LayoverSettings(BaseModel):
    """Limits on the stops of an itinerary (see `itinerary.passes_layover_rule`)."""

    max_minutes: int = 180
    #: Local-time window no stop may touch, e.g. ["23:00", "05:00"]. None disables the check.
    forbidden_window: tuple[str, str] | None = ("23:00", "05:00")

    @field_validator("forbidden_window")
    @classmethod
    def _parseable(cls, v: tuple[str, str] | None) -> tuple[str, str] | None:
        for text in v or ():
            try:
                hour, minute = text.split(":")
                time(int(hour), int(minute))
            except (ValueError, TypeError):
                raise ValueError(f"expected a HH:MM time, got {text!r}") from None
        return v


class DealSettings(BaseModel):
    median_ratio: float
    min_history_points: int
    renotify_drop_ratio: float
    lookahead_days: int


class AlternateOriginSettings(BaseModel):
    """How much cheaper a non-home origin has to be before it counts as a deal.

    Flying from Frankfurt costs a train ride and a day, so a FRA fare is only
    interesting if it beats the best Hamburg fare by both margins.
    """

    home: str = "HAM"
    min_saving_ratio: float = 0.20
    min_saving_total: float = 500

    def beats_home(self, price: float, home_price: float) -> bool:
        return (price <= (1 - self.min_saving_ratio) * home_price
                and price <= home_price - self.min_saving_total)


class ReportSettings(BaseModel):
    output_dir: str = "docs/site"


class EmailSettings(BaseModel):
    """`digest` mails every run, `deals_only` only when there are notifiable deals."""

    mode: Literal["digest", "deals_only", "never"] = "digest"

    @field_validator("mode", mode="before")
    @classmethod
    def _accept_legacy_always(cls, v: object) -> object:
        return "digest" if v == "always" else v   # pre-digest spelling of "send every run"


class Settings(BaseModel):
    origins: list[str]
    nights: Nights
    bridge_days: BridgeDays = BridgeDays()
    max_stops: int = 1
    layovers: LayoverSettings = LayoverSettings()
    excluded_airlines: list[str] = []
    providers: ProviderSettings
    budget: BudgetSettings
    deals: DealSettings
    alternate_origins: AlternateOriginSettings = AlternateOriginSettings()
    report: ReportSettings = ReportSettings()
    email: EmailSettings = EmailSettings()


# ---- runtime objects ----

@dataclass
class Secrets:
    serpapi_key: str | None
    searchapi_key: str | None
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
        searchapi_key=env.get("SEARCHAPI_KEY") or None,
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
        destinations[d.code] = Destination(
            d.code, d.name, d.cabin, d.max_price_per_person,
            tuple(d.origins) if d.origins is not None else None)

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
