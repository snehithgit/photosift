import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid.uuid4().hex


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def read(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.rollback()
            connection.close()

    @contextmanager
    def write(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self):
        with closing(self.connect()) as connection:
            # SQLite's initial journal-mode transition can return BUSY immediately even
            # with busy_timeout. Retry this bootstrap step before opening transactions.
            deadline = time.monotonic() + 15
            while True:
                try:
                    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if mode != "wal":
                        raise RuntimeError(
                            "SQLite WAL is unavailable; use supported local disk storage"
                        )
                    break
                except sqlite3.OperationalError as exc:
                    if (
                        getattr(exc, "sqlite_errorcode", 0) & 0xFF
                        not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.05)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                sql = path.read_text(encoding="utf-8")
                digest = hashlib.sha256(sql.encode()).hexdigest()
                # Serialize the ledger check too: API and worker can start on a fresh DB together.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    applied = connection.execute(
                        "SELECT sha256 FROM schema_migrations WHERE name=?",
                        (path.name,),
                    ).fetchone()
                    if applied:
                        if applied[0] != digest:
                            raise RuntimeError(
                                f"Applied migration changed: {path.name}"
                            )
                    else:
                        statement = ""
                        for line in sql.splitlines(keepends=True):
                            statement += line
                            if sqlite3.complete_statement(statement):
                                connection.execute(statement)
                                statement = ""
                        if statement.strip():
                            raise RuntimeError(f"Incomplete migration: {path.name}")
                        connection.execute(
                            "INSERT INTO schema_migrations VALUES (?,?,?)",
                            (path.name, digest, now()),
                        )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise


def revision(connection):
    return connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[
        0
    ]


def bump(connection):
    connection.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
    return revision(connection)


def action(connection, label, scope, undo_of=None):
    identifier = uid()
    connection.execute(
        "INSERT INTO actions VALUES (?,?,?,?,?)",
        (identifier, label, scope, now(), undo_of),
    )
    return identifier


def audit(connection, action_id, content_id, kind, before, after, version=None):
    connection.execute(
        "INSERT INTO audit_log(action_id,content_id,kind,before_json,after_json,applied_version,at) VALUES (?,?,?,?,?,?,?)",
        (action_id, content_id, kind, packed(before), packed(after), version, now()),
    )
