ALTER TABLE file_instances ADD COLUMN folder TEXT NOT NULL DEFAULT '.';
CREATE INDEX files_folder ON file_instances(root_id,folder);
DROP INDEX one_scan_per_root;
CREATE UNIQUE INDEX one_scan_per_root ON scan_runs(root_id) WHERE state IN ('queued','running','paused');
