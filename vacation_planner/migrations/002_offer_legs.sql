BEGIN;

-- The legs of an itinerary (origin, destination, departure, arrival per leg), so the
-- layover rule can be re-checked from stored data without re-parsing the raw payload.
ALTER TABLE offers ADD COLUMN legs_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_version (version) VALUES (2);

COMMIT;
