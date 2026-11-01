-- Review screens filter by effective group and open duplicate sets one at a time; at 50k photos
-- both were full scans repeated on every page. Indexes only, so no stored data changes.
CREATE INDEX group_effective ON group_assignments(COALESCE(manual_group,ai_group));
CREATE INDEX photos_duplicate_set ON photos(duplicate_set);
