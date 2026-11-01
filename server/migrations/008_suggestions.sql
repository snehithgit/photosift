CREATE TABLE scoring_runs (id TEXT PRIMARY KEY, index_id TEXT NOT NULL REFERENCES embedding_indexes(id), prompt_version TEXT NOT NULL, policy TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE content_scores (run_id TEXT NOT NULL REFERENCES scoring_runs(id), content_id TEXT NOT NULL REFERENCES contents(id), scores TEXT NOT NULL, margin REAL NOT NULL, PRIMARY KEY(run_id,content_id));
CREATE TABLE suggestion_runs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, config TEXT NOT NULL, memberships TEXT NOT NULL, created_at TEXT NOT NULL);
ALTER TABLE event_members ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
