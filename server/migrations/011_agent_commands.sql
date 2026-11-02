CREATE TABLE agent_commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', message TEXT, created REAL NOT NULL);
CREATE UNIQUE INDEX one_open_agent_command ON agent_commands(kind) WHERE state IN ('queued','running');
