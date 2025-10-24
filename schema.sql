CREATE TABLE IF NOT EXISTS seen (
  url TEXT PRIMARY KEY,
  first_seen_utc TEXT,
  title TEXT,
  source TEXT
);