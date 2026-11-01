ALTER TABLE export_runs ADD COLUMN owner TEXT;
ALTER TABLE export_runs ADD COLUMN token TEXT;
ALTER TABLE export_runs ADD COLUMN expires REAL;
ALTER TABLE export_operations ADD COLUMN temporary_path TEXT;
ALTER TABLE export_operations ADD COLUMN temporary_identity TEXT;
CREATE TABLE export_journal (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES export_runs(id), content_id TEXT, event TEXT NOT NULL, details TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE export_metadata (run_id TEXT NOT NULL REFERENCES export_runs(id), kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, identity TEXT NOT NULL, PRIMARY KEY(run_id,kind));
