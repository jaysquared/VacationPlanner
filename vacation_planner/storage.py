from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

from .models import DealReason, Leg, Offer, Provider, SearchRequest, SearchResult, SeatClass


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed; pass a timezone-aware datetime")
    return dt.astimezone(timezone.utc).isoformat()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class RunRow:
    id: int
    started_at: datetime
    finished_at: datetime | None
    planned: int
    executed: int
    status: str

    @staticmethod
    def from_row(r: sqlite3.Row) -> "RunRow":
        return RunRow(id=r["id"], started_at=_dt(r["started_at"]), finished_at=_dt(r["finished_at"]),
                      planned=r["planned"], executed=r["executed"], status=r["status"])


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
    legs: list[Leg] = field(default_factory=list)

    @staticmethod
    def from_row(r: sqlite3.Row) -> "OfferRow":
        return OfferRow(
            id=r["id"], search_id=r["search_id"], provider=Provider(r["provider"]),
            price_total=r["price_total"], per_person=r["per_person"],
            airlines=json.loads(r["airlines_json"]), stops=r["stops"],
            duration_minutes=r["duration_minutes"], departs_at=r["departs_at"],
            arrives_at=r["arrives_at"], price_level=r["price_level"],
            typical_low=r["typical_low"], typical_high=r["typical_high"], google_url=r["google_url"],
            legs=[Leg(**leg) for leg in json.loads(r["legs_json"])],
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
_OFFER_COLS = "id, search_id, provider, price_total, per_person, airlines_json, stops, duration_minutes, departs_at, arrives_at, price_level, typical_low, typical_high, google_url, legs_json"


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
            # Each migration script owns its own explicit BEGIN/COMMIT (and writes its own
            # schema_version row) so the DDL and the version marker commit atomically together.
            # executescript() always flushes any pending transaction before it runs, so we cannot
            # rely on wrapping it in `with self.conn:` here -- and since sqlite3_exec does not
            # auto-rollback on a mid-script error, we roll back explicitly on failure so a failed
            # migration never leaves partially-applied DDL behind.
            try:
                self.conn.executescript(f.read_text())
            except Exception:
                self.conn.rollback()
                raise

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

    def run_info(self, run_id: int | None) -> RunRow | None:
        if run_id is None:
            return None
        r = self.conn.execute(
            "SELECT id, started_at, finished_at, planned, executed, status FROM runs WHERE id=?",
            (run_id,)).fetchone()
        return RunRow.from_row(r) if r else None

    def search_counts(self, run_id: int | None) -> dict[str, int]:
        """How many searches of a run ended in each status ('ok', 'error', 'skipped', ...)."""
        if run_id is None:
            return {}
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM searches WHERE run_id=? GROUP BY status", (run_id,))
        return {r["status"]: r["n"] for r in rows}

    def provider_counts(self, run_id: int | None) -> dict[Provider, int]:
        """How many successful searches of a run went to each provider."""
        if run_id is None:
            return {}
        rows = self.conn.execute(
            "SELECT provider, COUNT(*) AS n FROM searches WHERE run_id=? AND status='ok' GROUP BY provider",
            (run_id,))
        return {Provider(r["provider"]): r["n"] for r in rows}

    # ---- searches / offers ----
    def _insert_search(self, run_id: int, req: SearchRequest, provider: Provider, status: str,
                       now: datetime, error: str | None = None, raw_path: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO searches (run_id, slot_id, origin, destination, outbound_date, return_date,
               seat, adults, children, provider, requested_at, status, error, raw_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, req.slot_id, req.origin, req.destination, req.outbound_date.isoformat(),
             req.return_date.isoformat(), req.seat.value, req.adults, req.children,
             provider.value, _iso(now), status, error, raw_path))
        return cur.lastrowid

    def record_search(self, run_id: int, req: SearchRequest, provider: Provider, status: str,
                      now: datetime, error: str | None = None, raw_path: str | None = None) -> int:
        with self.conn:
            return self._insert_search(run_id, req, provider, status, now, error, raw_path)

    def _insert_offers(self, search_id: int, offers: list[Offer]) -> None:
        self.conn.executemany(
            """INSERT INTO offers (search_id, provider, price_total, currency, per_person, airlines_json,
               stops, duration_minutes, departs_at, arrives_at, price_level, typical_low, typical_high,
               google_url, flight_json, legs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(search_id, o.provider.value, o.price_total, o.currency, o.per_person,
              json.dumps(o.airlines), o.stops, o.duration_minutes, o.departs_at, o.arrives_at,
              o.price_level, o.typical_low, o.typical_high, o.google_url, json.dumps(o.raw, default=str),
              json.dumps([asdict(leg) for leg in o.legs]))
             for o in offers])

    def record_offers(self, search_id: int, offers: list[Offer]) -> None:
        with self.conn:
            self._insert_offers(search_id, offers)

    def save_result(self, run_id: int, result: SearchResult, now: datetime) -> int:
        # Single transaction: search + offers commit or roll back together.
        with self.conn:
            sid = self._insert_search(run_id, result.request, result.provider, "ok", now, raw_path=result.raw_path)
            self._insert_offers(sid, result.offers)
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

    def best_price_for(self, slot_id: str, origin: str, destination: str, seat: SeatClass) -> float | None:
        """Cheapest current price on a route: the newest observation per date pair, minimised."""
        r = self.conn.execute(
            """SELECT MIN(c.price_total) AS p FROM searches s JOIN cheapest_per_search c ON c.search_id=s.id
               WHERE s.status='ok' AND s.slot_id=? AND s.origin=? AND s.destination=? AND s.seat=?
                 AND s.id = (
                   SELECT s2.id FROM searches s2 JOIN cheapest_per_search c2 ON c2.search_id=s2.id
                   WHERE s2.status='ok' AND s2.slot_id=s.slot_id AND s2.origin=s.origin
                     AND s2.destination=s.destination AND s2.seat=s.seat
                     AND s2.outbound_date=s.outbound_date AND s2.return_date=s.return_date
                   ORDER BY s2.requested_at DESC, s2.id DESC LIMIT 1)""",
            (slot_id, origin, destination, seat.value)).fetchone()
        return r["p"]

    def previous_best_price(self, slot_id: str, origin: str, destination: str, seat: SeatClass,
                            before_run_id: int | None) -> float | None:
        """The route's cheapest price in the most recent *earlier* run that observed it.

        Runs that only produced errors for this route are skipped, so "last week" means
        the last week with data, not a blank line after a failed scan.
        """
        if before_run_id is None:
            return None
        r = self.conn.execute(
            """SELECT MIN(c.price_total) AS p FROM searches s JOIN cheapest_per_search c ON c.search_id=s.id
               WHERE s.status='ok' AND s.slot_id=? AND s.origin=? AND s.destination=? AND s.seat=?
                 AND s.run_id = (
                   SELECT MAX(s2.run_id) FROM searches s2 JOIN cheapest_per_search c2 ON c2.search_id=s2.id
                   WHERE s2.status='ok' AND s2.slot_id=s.slot_id AND s2.origin=s.origin
                     AND s2.destination=s.destination AND s2.seat=s.seat AND s2.run_id < ?)""",
            (slot_id, origin, destination, seat.value, before_run_id)).fetchone()
        return r["p"]

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
