CREATE TABLE undo_jobs (id TEXT PRIMARY KEY, action_id TEXT NOT NULL REFERENCES actions(id), original_action TEXT NOT NULL UNIQUE REFERENCES actions(id), state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL);
CREATE TABLE undo_items (job_id TEXT NOT NULL REFERENCES undo_jobs(id), audit_id INTEGER NOT NULL REFERENCES audit_log(id), state TEXT NOT NULL DEFAULT 'pending', reason TEXT, PRIMARY KEY(job_id,audit_id));
CREATE INDEX undo_pending ON undo_items(job_id,state);
