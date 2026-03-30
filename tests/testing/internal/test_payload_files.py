"""Tests for payload-files mode in TestOptWriter and TestCoverageWriter."""

from __future__ import annotations

import itertools
import json
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
import ddtrace.testing.internal.offline_mode as offline_module
import ddtrace.testing.internal.writer as writer_module
from ddtrace.testing.internal.writer import Event
from ddtrace.testing.internal.writer import TestCoverageWriter
from ddtrace.testing.internal.writer import TestOptWriter
from ddtrace.testing.internal.writer import _write_payload_file


@pytest.fixture(autouse=True)
def reset_offline_singleton(monkeypatch):
    """Reset the offline mode singleton before each test."""
    monkeypatch.setattr(offline_module, "_offline_mode", None)


@pytest.fixture(autouse=True)
def reset_payload_counter(monkeypatch):
    """Reset the global payload file counter before each test for predictable filenames."""
    monkeypatch.setattr(writer_module, "_payload_file_counter", itertools.count())


def make_opt_writer() -> TestOptWriter:
    return TestOptWriter(connector_setup=NoOpBackendConnectorSetup())


def make_cov_writer() -> TestCoverageWriter:
    return TestCoverageWriter(connector_setup=NoOpBackendConnectorSetup())


# ---------------------------------------------------------------------------
# _write_payload_file helper
# ---------------------------------------------------------------------------


class TestWritePayloadFile:
    def test_creates_json_file(self, tmp_path):
        payload = {"version": 1, "events": [{"type": "test"}]}
        _write_payload_file(str(tmp_path), payload)

        files = list(tmp_path.glob("payload_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data == payload

    def test_creates_output_dir_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        payload = {"x": 1}
        _write_payload_file(str(nested), payload)
        assert nested.exists()
        assert len(list(nested.glob("*.json"))) == 1

    def test_increments_counter_across_calls(self, tmp_path):
        _write_payload_file(str(tmp_path), {"n": 0})
        _write_payload_file(str(tmp_path), {"n": 1})
        _write_payload_file(str(tmp_path), {"n": 2})
        files = sorted(tmp_path.glob("payload_*.json"))
        assert len(files) == 3
        assert files[0].name == "payload_0.json"
        assert files[1].name == "payload_1.json"
        assert files[2].name == "payload_2.json"

    def test_no_tmp_file_left_behind(self, tmp_path):
        _write_payload_file(str(tmp_path), {"data": "value"})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# TestOptWriter payload-files mode
# ---------------------------------------------------------------------------


class TestTestOptWriterPayloadFilesMode:
    def test_send_events_writes_json_file(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_opt_writer()
        events: list[Event] = [{"type": "test", "content": {"meta": {"test.name": "my_test"}}}]
        writer._send_events(events)

        tests_dir = output_dir / "payloads" / "tests"
        files = list(tests_dir.glob("payload_*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["version"] == 1
        assert data["events"] == events
        assert "metadata" in data

    def test_send_events_skips_http_in_payload_files_mode(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_opt_writer()
        # connector is a NoOpBackendConnector — track that request() is never called.
        writer.connector = Mock()

        writer._send_events([{"type": "test", "content": {}}])

        writer.connector.request.assert_not_called()

    def test_send_events_uses_http_when_not_in_payload_files_mode(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_opt_writer()
        mock_connector = Mock()
        mock_connector.request.return_value = Mock(elapsed_seconds=0.01, error_type=None)
        writer.connector = mock_connector

        with patch("ddtrace.testing.internal.writer.TelemetryAPI") as mock_telemetry_cls:
            mock_telemetry = Mock()
            mock_telemetry_cls.get.return_value = mock_telemetry
            writer._send_events([{"type": "test", "content": {}}])

        mock_connector.request.assert_called_once()

    def test_multiple_flushes_write_separate_files(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_opt_writer()
        writer._send_events([{"type": "test", "n": 1}])
        writer._send_events([{"type": "test", "n": 2}])

        tests_dir = output_dir / "payloads" / "tests"
        files = sorted(tests_dir.glob("payload_*.json"))
        assert len(files) == 2


# ---------------------------------------------------------------------------
# TestCoverageWriter payload-files mode
# ---------------------------------------------------------------------------


class TestTestCoverageWriterPayloadFilesMode:
    def test_send_events_writes_json_file(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_cov_writer()
        events: list[Event] = [{"test_session_id": 1, "files": []}]
        writer._send_events(events)

        cov_dir = output_dir / "payloads" / "coverage"
        files = list(cov_dir.glob("payload_*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["version"] == 2
        assert data["coverages"] == events

    def test_send_events_skips_http_in_payload_files_mode(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        writer = make_cov_writer()
        writer.connector = Mock()
        writer._send_events([{"test_session_id": 1, "files": []}])
        writer.connector.post_files.assert_not_called()

    def test_coverage_and_test_payloads_go_to_separate_dirs(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        opt_writer = make_opt_writer()
        cov_writer = make_cov_writer()

        opt_writer._send_events([{"type": "test"}])
        cov_writer._send_events([{"files": []}])

        assert (output_dir / "payloads" / "tests").exists()
        assert (output_dir / "payloads" / "coverage").exists()
        assert len(list((output_dir / "payloads" / "tests").glob("*.json"))) == 1
        assert len(list((output_dir / "payloads" / "coverage").glob("*.json"))) == 1
