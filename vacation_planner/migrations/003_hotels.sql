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
