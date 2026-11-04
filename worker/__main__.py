import argparse
import os
import sqlite3
import time

from server import pipeline
from server.db import Database
from server.review import process_bulk, process_undo
from worker import preparer
from worker.exporter import process_export
from worker.scanner import scan


def tick(db, hash_mib_per_second=32):
    with db.read() as c:
        row = c.execute(
            "SELECT id FROM scan_runs WHERE state='queued' OR state='running' AND expires<? ORDER BY started_at LIMIT 1",
            (time.time(),),
        ).fetchone()
    if row:
        scan(db, row[0], hash_mib_per_second=hash_mib_per_second)
        return True
    return process_undo(db) or process_bulk(db) or process_export(db)


def main():
    parser = argparse.ArgumentParser(
        description="PhotoSift Zima inventory and durable bulk worker"
    )
    parser.add_argument(
        "--db", default=os.environ.get("PHOTOSIFT_DB", ".data/photosift.sqlite3")
    )
    parser.add_argument(
        "--once", action="store_true", help="Drain currently queued work, then exit"
    )
    parser.add_argument(
        "--hash-mib-per-second",
        type=float,
        default=32,
        help="Per-process source read cap (configuration, not a measured rate)",
    )
    args = parser.parse_args()
    if args.hash_mib_per_second <= 0:
        parser.error("--hash-mib-per-second must be positive")
    db = Database(args.db)
    if not args.once:
        preparer.start(db)
    slimming = True
    try:
        while True:
            worked = tick(db, args.hash_mib_per_second)
            if not worked:
                if args.once:
                    break
                if slimming:
                    try:
                        slimming = pipeline.slim_results(db) > 0
                    except sqlite3.OperationalError:
                        pass  # The database is busy; the next pass carries on.
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
