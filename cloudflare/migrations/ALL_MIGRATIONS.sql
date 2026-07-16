-- schema_version bookkeeping
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- migration 1: baseline
INSERT INTO schema_version (version, name, applied_at) VALUES (1, 'baseline', datetime('now'));

-- migration 2: decision_provenance
ALTER TABLE decisions ADD COLUMN git_commit TEXT;
ALTER TABLE decisions ADD COLUMN config_hash TEXT;
ALTER TABLE decisions ADD COLUMN data_versions TEXT;
INSERT INTO schema_version (version, name, applied_at) VALUES (2, 'decision_provenance', datetime('now'));
