import json
import sys

from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal import runtime
from ddtrace.internal.utils.http import parse_form_multipart
from tests.debugging.utils import create_snapshot_line_probe


class DummyProbeStatusLogger(ProbeStatusLogger):
    def __init__(self, *args, **kwargs):
        super(DummyProbeStatusLogger, self).__init__(*args, **kwargs)
        self._flush_queue = []

    def _write_payload(self, data: tuple[bytes, dict]):
        body, headers = data
        self._flush_queue.extend(parse_form_multipart(body.decode("utf-8"), headers)["event"])

    def clear(self):
        self._flush_queue[:] = []

    @property
    def queue(self) -> list:
        self.flush()
        return self._flush_queue


class AgentlessDummyProbeStatusLogger(ProbeStatusLogger):
    """ProbeStatusLogger subclass that captures payloads in agentless mode (no HTTP requests)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._flush_queue = []
        # Prevent any accidental HTTP request
        self._connect = lambda: (_ for _ in ()).throw(AssertionError("unexpected HTTP request"))

    def _write_payload(self, data: tuple[bytes, dict]):
        body, headers = data
        # Agentless sends JSON array directly
        self._flush_queue.extend(json.loads(body.decode("utf-8")))

    def clear(self):
        self._flush_queue[:] = []

    @property
    def queue(self) -> list:
        self.flush()
        return self._flush_queue


def test_probe_status_agentless(monkeypatch):
    """Test ProbeStatusLogger sends to /api/v2/debugger with API key in agentless CI mode."""
    from ddtrace.debugging._config import di_config

    monkeypatch.setattr(di_config, "_is_agentless", True)
    monkeypatch.setattr(di_config, "_api_key", "test-api-key")
    monkeypatch.setattr(di_config, "tags", "service:mysvc,env:staging,version:1.0")

    status_logger = AgentlessDummyProbeStatusLogger("test")

    # Agentless mode: endpoint should include tags in QS
    assert status_logger._endpoint.startswith("/api/v2/debugger?ddtags=")

    probe = create_snapshot_line_probe(
        probe_id="probe-agentless",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    status_logger.received(probe)

    (entry,) = status_logger.queue
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["status"] == "RECEIVED"


def test_probe_status_received():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    message = "Probe %s received" % probe.probe_id

    status_logger.received(probe, message)

    (entry,) = status_logger.queue
    assert entry["message"] == message
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["probeVersion"] == probe.version
    assert entry["debugger"]["diagnostics"]["runtimeId"] == runtime.get_runtime_id()
    assert entry["debugger"]["diagnostics"]["status"] == "RECEIVED"


def test_probe_status_installed():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        version=123,
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    message = "Probe %s installed" % probe.probe_id

    status_logger.installed(probe, message)

    (entry,) = status_logger.queue
    assert entry["message"] == message
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["probeVersion"] == probe.version
    assert entry["debugger"]["diagnostics"]["runtimeId"] == runtime.get_runtime_id()
    assert entry["debugger"]["diagnostics"]["status"] == "INSTALLED"


def test_probe_status_error():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )

    try:
        raise RuntimeError("Test error")
    except Exception:
        exc_type, exc, _ = sys.exc_info()
        status_logger.error(probe, (exc_type.__name__, str(exc)))

    (entry,) = status_logger.queue
    assert entry["message"] == "Failed to instrument probe probe-instance-method"
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["status"] == "ERROR"

    exc = entry["debugger"]["diagnostics"]["exception"]
    assert exc["type"] == "RuntimeError"
    assert exc["message"] == "Test error"
