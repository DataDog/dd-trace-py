"""
Target module for testing Debugger._on_run_module.

Run with: ddtrace-run python -m target

ddtrace-run bootstraps the library before this module body executes.
The module enables TestDebugger here and registers a probe on `instrumented`.
When this module body finishes, _on_run_module fires and injects the pending probe.
The verify thread waits on an event signalled when the probe is installed,
then calls `instrumented` and asserts the snapshot.
"""

import inspect
from pathlib import Path
import threading

from tests.debugging.mocking import TestDebugger
from tests.debugging.utils import create_snapshot_line_probe


TestDebugger.enable()
_debugger = TestDebugger._instance
assert _debugger is not None

_probe_installed = threading.Event()

# Signal the event when our probe transitions to installed state.
_real_set_installed = _debugger._probe_registry.set_installed


def _set_installed(probe):
    _real_set_installed(probe)
    if probe.probe_id == "on_run_module_probe":
        _probe_installed.set()


_debugger._probe_registry.set_installed = _set_installed


def instrumented():
    value = 42  # probe target
    return value


_debugger.add_probes(
    create_snapshot_line_probe(
        probe_id="on_run_module_probe",
        source_file=str(Path(__file__).resolve()),
        line=inspect.getsourcelines(instrumented)[1] + 1,  # "value = 42"
        condition=None,
    )
)


def verify():
    assert _probe_installed.wait(timeout=5.0), "_on_run_module did not inject probe within 5 s"

    instrumented()

    queue = _debugger.test_queue
    assert len(queue) == 1, f"expected 1 snapshot, got {len(queue)}: {queue}"
    snapshot = queue[0]
    assert snapshot.probe.probe_id == "on_run_module_probe"
    assert snapshot.frame.f_locals["value"] == 42

    print("OK")


threading.Thread(target=verify).start()
