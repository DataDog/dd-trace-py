"""Checks for the CI-only function-level TIA storage report."""

import json
import sqlite3

from scripts import tia_storage_report


def test_measure_sums_databases_and_live_wal_files(tmp_path):
    root = tmp_path / ".tia"
    databases = [root / "databases" / name / ".testmondata" for name in ("first", "second")]
    connections = []
    try:
        for database in databases:
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connections.append(connection)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE state (value TEXT)")
            connection.execute("INSERT INTO state VALUES ('recorded')")
            connection.commit()
        raw_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        report = tia_storage_report.measure(root)
    finally:
        for connection in connections:
            connection.close()

    assert report["database_count"] == 2
    assert report["raw_file_count"] >= 4
    assert report["raw_bytes"] == raw_bytes
    assert report["snapshot_bytes"] > 0
    assert report["gzip_bytes"] > 0
    assert report["snapshot_errors"] == []


def test_invalid_database_keeps_raw_size_but_marks_snapshot_unknown(tmp_path):
    root = tmp_path / ".tia"
    database = root / "databases" / "env" / ".testmondata"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not sqlite")

    report = tia_storage_report.measure(root)

    assert report["raw_bytes"] == len(b"not sqlite")
    assert report["snapshot_bytes"] is None
    assert report["gzip_bytes"] is None
    assert len(report["snapshot_errors"]) == 1


def test_main_emits_one_line_even_without_database(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_JOB_ID", "123")
    monkeypatch.setenv("CI_JOB_STATUS", "failed")
    monkeypatch.setenv("TEST_SUITE", "internal")
    monkeypatch.setenv("TEST_ENVIRONMENTS_1", "first second")

    tia_storage_report.main()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(tia_storage_report.PREFIX)
    report = json.loads(lines[0].removeprefix(tia_storage_report.PREFIX))
    assert report["job_id"] == "123"
    assert report["job_status"] == "failed"
    assert report["environments"] == ["first", "second"]
    assert report["database_count"] == report["raw_bytes"] == 0
    assert not (tmp_path / ".tia").exists()
