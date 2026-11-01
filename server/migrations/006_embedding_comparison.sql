CREATE TABLE comparison_samples (id TEXT PRIMARY KEY, members TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE prompt_packs (index_id TEXT NOT NULL REFERENCES embedding_indexes(id), version TEXT NOT NULL, prompts TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(index_id,version));
CREATE TABLE evaluations (id TEXT PRIMARY KEY, index_id TEXT NOT NULL REFERENCES embedding_indexes(id), sample_id TEXT NOT NULL REFERENCES comparison_samples(id), prompt_version TEXT NOT NULL, metrics TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE query_vectors (index_id TEXT NOT NULL REFERENCES embedding_indexes(id), query TEXT NOT NULL, vector BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(index_id,query));
