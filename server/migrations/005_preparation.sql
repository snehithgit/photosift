CREATE TABLE stage_specs (fingerprint TEXT PRIMARY KEY, stage TEXT NOT NULL, config TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX active_preparation ON stage_specs(stage) WHERE stage='prepare' AND active=1;
ALTER TABLE stage_jobs ADD COLUMN source_instance INTEGER REFERENCES file_instances(id);
ALTER TABLE stage_jobs ADD COLUMN source_size INTEGER;
ALTER TABLE stage_jobs ADD COLUMN source_mtime_ns INTEGER;
CREATE TABLE preparation_outputs (content_id TEXT NOT NULL REFERENCES contents(id), fingerprint TEXT NOT NULL REFERENCES stage_specs(fingerprint), payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(content_id,fingerprint));
CREATE TABLE agent_contacts (owner TEXT PRIMARY KEY, last_contact REAL NOT NULL, stage TEXT NOT NULL, fingerprint TEXT NOT NULL);
