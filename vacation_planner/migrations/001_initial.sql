BEGIN;

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

INSERT INTO schema_version (version) VALUES (1);

COMMIT;
