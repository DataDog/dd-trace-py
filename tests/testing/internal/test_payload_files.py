"""Tests for payload-files mode writers and _write_payload_file helper."""

from __future__ import annotations

import itertools
import json
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
import ddtrace.testing.internal.offline_mode as offline_module
from ddtrace.testing.internal.offline_mode import write_payload_file
from ddtrace.testing.internal.writer import Event
from ddtrace.testing.internal.writer import PayloadFileCoverageWriter
from ddtrace.testing.internal.writer import PayloadFileTestOptWriter
from ddtrace.testing.internal.writer import TestOptWriter


_noop = NoOpBackendConnectorSetup()


@pytest.fixture(autouse=True)
def reset_payload_counter(monkeypatch):
    """Reset the global payload file counter before each test for predictable filenames."""
    monkeypatch.setattr(offline_module, "_payload_file_counter", itertools.count())


# ---------------------------------------------------------------------------
# _write_payload_file helper
# ---------------------------------------------------------------------------


class TestWritePayloadFile:
    def test_creates_json_file(self, tmp_path):
        payload = {"version": 1, "events": [{"type": "test"}]}
        write_payload_file(str(tmp_path), payload, kind="tests")

        files = list(tmp_path.glob("tests-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data == payload

    def test_creates_output_dir_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        payload = {"x": 1}
        write_payload_file(str(nested), payload, kind="tests")
        assert nested.exists()
        assert len(list(nested.glob("*.json"))) == 1

    def test_increments_counter_across_calls(self, tmp_path):
        write_payload_file(str(tmp_path), {"n": 0}, kind="tests")
        write_payload_file(str(tmp_path), {"n": 1}, kind="tests")
        write_payload_file(str(tmp_path), {"n": 2}, kind="tests")
        files = sorted(tmp_path.glob("tests-*.json"))
        assert len(files) == 3

    def test_filename_matches_go_pattern(self, tmp_path):
        """Filenames follow {kind}-{timestamp_ns}-{pid}-{seq}.json pattern."""
        write_payload_file(str(tmp_path), {"n": 0}, kind="tests")
        files = list(tmp_path.glob("tests-*.json"))
        assert len(files) == 1
        # Verify the filename has the expected structure: tests-{ts}-{pid}-{seq}.json
        parts = files[0].stem.split("-")
        assert parts[0] == "tests"
        assert len(parts) == 4  # kind, timestamp, pid, seq

    def test_coverage_kind_uses_coverage_prefix(self, tmp_path):
        write_payload_file(str(tmp_path), {"n": 0}, kind="coverage")
        files = list(tmp_path.glob("coverage-*.json"))
        assert len(files) == 1

    def test_no_tmp_file_left_behind(self, tmp_path):
        write_payload_file(str(tmp_path), {"data": "value"}, kind="tests")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# PayloadFileTestOptWriter
# ---------------------------------------------------------------------------


class TestPayloadFileTestOptWriter:
    def test_send_events_writes_json_file(self, tmp_path):
        tests_dir = tmp_path / "tests"
        writer = PayloadFileTestOptWriter(connector_setup=_noop, output_dir=str(tests_dir))
        events: list[Event] = [{"type": "test", "content": {"meta": {"test.name": "my_test"}}}]
        writer._send_events(events)

        files = list(tests_dir.glob("tests-*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["version"] == 1
        assert data["events"] == events
        assert "metadata" in data

    def test_send_events_skips_http(self, tmp_path):
        tests_dir = tmp_path / "tests"
        writer = PayloadFileTestOptWriter(connector_setup=_noop, output_dir=str(tests_dir))
        writer.connector = Mock()

        writer._send_events([{"type": "test", "content": {}}])

        writer.connector.request.assert_not_called()

    def test_multiple_flushes_write_separate_files(self, tmp_path):
        tests_dir = tmp_path / "tests"
        writer = PayloadFileTestOptWriter(connector_setup=_noop, output_dir=str(tests_dir))
        writer._send_events([{"type": "test", "n": 1}])
        writer._send_events([{"type": "test", "n": 2}])

        files = sorted(tests_dir.glob("tests-*.json"))
        assert len(files) == 2


class TestOnlineTestOptWriter:
    def test_send_events_uses_http(self):
        writer = TestOptWriter(connector_setup=_noop)
        mock_connector = Mock()
        mock_connector.request.return_value = Mock(elapsed_seconds=0.01, error_type=None)
        writer.connector = mock_connector

        with patch("ddtrace.testing.internal.writer.TelemetryAPI") as mock_telemetry_cls:
            mock_telemetry_cls.get.return_value = Mock()
            writer._send_events([{"type": "test", "content": {}}])

        mock_connector.request.assert_called_once()


# ---------------------------------------------------------------------------
# PayloadFileCoverageWriter
# ---------------------------------------------------------------------------


class TestPayloadFileCoverageWriter:
    def test_send_events_writes_json_file(self, tmp_path):
        cov_dir = tmp_path / "coverage"
        writer = PayloadFileCoverageWriter(connector_setup=_noop, output_dir=str(cov_dir))
        events: list[Event] = [{"test_session_id": 1, "files": []}]
        writer._send_events(events)

        files = list(cov_dir.glob("coverage-*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["version"] == 2
        assert data["coverages"] == events

    def test_send_events_skips_http(self, tmp_path):
        cov_dir = tmp_path / "coverage"
        writer = PayloadFileCoverageWriter(connector_setup=_noop, output_dir=str(cov_dir))
        writer.connector = Mock()
        writer._send_events([{"test_session_id": 1, "files": []}])
        writer.connector.post_files.assert_not_called()

    def test_coverage_and_test_payloads_go_to_separate_dirs(self, tmp_path):
        tests_dir = tmp_path / "tests"
        cov_dir = tmp_path / "coverage"

        opt_writer = PayloadFileTestOptWriter(connector_setup=_noop, output_dir=str(tests_dir))
        cov_writer = PayloadFileCoverageWriter(connector_setup=_noop, output_dir=str(cov_dir))

        opt_writer._send_events([{"type": "test"}])
        cov_writer._send_events([{"files": []}])

        assert tests_dir.exists()
        assert cov_dir.exists()
        assert len(list(tests_dir.glob("*.json"))) == 1
        assert len(list(cov_dir.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# _write_payload_file telemetry naming
# ---------------------------------------------------------------------------


class TestTelemetryPayloadFileNaming:
    def test_telemetry_uses_ordinal_first_naming(self, tmp_path):
        """Telemetry files use ordinal-first naming for deterministic replay ordering."""
        write_payload_file(str(tmp_path), {"seq": 1}, kind="telemetry")
        write_payload_file(str(tmp_path), {"seq": 2}, kind="telemetry")

        files = sorted(tmp_path.glob("telemetry-*.json"))
        assert len(files) == 2
        # Ordinal is zero-padded to 20 digits, followed by PID
        assert files[0].name.startswith("telemetry-00000000000000000000-")
        assert files[1].name.startswith("telemetry-00000000000000000001-")
        # Lexicographic sort preserves emission order
        assert files[0].name < files[1].name


# ---------------------------------------------------------------------------
# PayloadFileTelemetryAPI
# ---------------------------------------------------------------------------


class TestPayloadFileTelemetryAPI:
    def test_telemetry_metrics_written_to_file_on_finish(self, tmp_path):
        telemetry_dir = tmp_path / "telemetry"

        from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
        from ddtrace.testing.internal.telemetry import PayloadFileTelemetryAPI

        api = PayloadFileTelemetryAPI(connector_setup=NoOpBackendConnectorSetup(), output_dir=str(telemetry_dir))
        api.add_count_metric("test_metric", 1, {"tag": "value"})
        api.add_distribution_metric("test_dist", 42.5)
        api.finish()

        files = list(telemetry_dir.glob("telemetry-*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert "metrics" in data
        assert len(data["metrics"]) == 2
        assert data["metrics"][0]["type"] == "count"
        assert data["metrics"][0]["metric"] == "test_metric"
        assert data["metrics"][0]["value"] == 1
        assert data["metrics"][1]["type"] == "distribution"
        assert data["metrics"][1]["metric"] == "test_dist"
        assert data["metrics"][1]["value"] == 42.5

    def test_no_telemetry_file_when_no_metrics(self, tmp_path):
        telemetry_dir = tmp_path / "telemetry"

        from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
        from ddtrace.testing.internal.telemetry import PayloadFileTelemetryAPI

        api = PayloadFileTelemetryAPI(connector_setup=NoOpBackendConnectorSetup(), output_dir=str(telemetry_dir))
        api.finish()

        assert not telemetry_dir.exists()

    def test_online_telemetry_api_does_not_write_files(self, tmp_path):
        from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
        from ddtrace.testing.internal.telemetry import TelemetryAPI

        api = TelemetryAPI(connector_setup=NoOpBackendConnectorSetup())
        api.add_count_metric("test_metric", 1)
        api.finish()

        # Base TelemetryAPI should never create payload files
        assert not (tmp_path / "telemetry").exists()
