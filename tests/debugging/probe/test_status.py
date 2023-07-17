import json
import sys
import time

from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal import runtime
from tests.debugging.utils import create_snapshot_line_probe


class DummyProbeStatusLogger(ProbeStatusLogger):
    def __init__(self, *args, **kwargs):
        super(DummyProbeStatusLogger, self).__init__(*args, **kwargs)
        self.queue = []

    def _write(self, probe, status, message, exc_info=None):
        payload = self._payload(probe, status, message, int(time.time() * 1e3), exc_info)
        self.queue.append(json.loads(payload))


def test_probe_status_received():
    status_logger = DummyProbeStatusLogger("test", "test")

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
    status_logger = DummyProbeStatusLogger("test", "test")

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
    status_logger = DummyProbeStatusLogger("test", "test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    message = "Probe %s installed" % probe.probe_id

    try:
        raise RuntimeError("Test error")
    except Exception:
        status_logger.error(probe, message, exc_info=sys.exc_info())

    (entry,) = status_logger.queue
    assert entry["message"] == message
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["status"] == "ERROR"

    exc = entry["debugger"]["diagnostics"]["exception"]
    assert exc["type"] == "RuntimeError"
    assert exc["message"] == "Test error"
    assert exc["stacktrace"][0]["function"] == "test_probe_status_error"
