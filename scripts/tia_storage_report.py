#!/usr/bin/env python3
"""Report the local TIA database footprint without producing a CI artifact."""

from __future__ import annotations

from contextlib import closing
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


PREFIX = "[TIA-STORAGE] "


def measure(root: Path) -> dict[str, object]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    databases = [path for path in files if path.name == ".testmondata"]
    snapshot_bytes = 0
    gzip_bytes = 0
    errors = []

    with tempfile.TemporaryDirectory(prefix="tia-storage-") as temporary:
        for index, database in enumerate(databases):
            snapshot = Path(temporary) / f"{index}.db"
            compressed = Path(temporary) / f"{index}.db.gz"
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(snapshot)) as destination:
                        source.backup(destination)
                with snapshot.open("rb") as snapshot_reader, gzip.open(compressed, "wb", compresslevel=6) as target:
                    shutil.copyfileobj(snapshot_reader, target)
                snapshot_bytes += snapshot.stat().st_size
                gzip_bytes += compressed.stat().st_size
            except (OSError, sqlite3.Error) as error:
                errors.append(f"{database.relative_to(root)}: {error}")

    return {
        "raw_bytes": sum(path.stat().st_size for path in files),
        "raw_file_count": len(files),
        "database_count": len(databases),
        "snapshot_bytes": snapshot_bytes if not errors else None,
        "gzip_bytes": gzip_bytes if not errors else None,
        "snapshot_errors": errors,
    }


def main() -> None:
    shard = os.environ.get("CI_NODE_INDEX", "1")
    record: dict[str, object] = {
        "version": 1,
        "job_id": os.environ.get("CI_JOB_ID"),
        "commit": os.environ.get("CI_COMMIT_SHA"),
        "job_name": os.environ.get("CI_JOB_NAME"),
        "suite": os.environ.get("TEST_SUITE"),
        "python": "3.13",
        "environments": os.environ.get(f"TEST_ENVIRONMENTS_{shard}", "").split(),
    }
    try:
        record.update(measure(Path.cwd() / ".tia"))
    except (OSError, sqlite3.Error) as error:
        record["error"] = str(error)
    print(PREFIX + json.dumps(record, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
