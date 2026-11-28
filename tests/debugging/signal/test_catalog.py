from dataclasses import dataclass
import os
import subprocess
import sys
import threading

import pytest

from ddtrace.debugging._metrics import probe_metrics
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import SpanDecorationFunctionProbe
from ddtrace.debugging._probe.model import SpanDecorationLineProbe
from ddtrace.debugging._probe.model import SpanFunctionProbe
from ddtrace.debugging._probe.model import TriggerFunctionProbe
from ddtrace.debugging._probe.model import TriggerLineProbe
from ddtrace.debugging._probe.remoteconfig import PROBE_FACTORY
from ddtrace.debugging._signal.catalog import signal_for_probe
from ddtrace.debugging._signal.metric_sample import MetricSample
from ddtrace.debugging._signal.model import _lookup_signal_type
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.debugging._signal.tracing import DynamicSpan
from ddtrace.debugging._signal.tracing import SpanDecoration
from ddtrace.debugging._signal.trigger import Trigger
from tests.debugging.utils import create_metric_function_probe
from tests.debugging.utils import create_span_decoration_line_probe


EXPECTED_SIGNALS = {
    LogFunctionProbe: Snapshot,
    LogLineProbe: Snapshot,
    MetricFunctionProbe: MetricSample,
    MetricLineProbe: MetricSample,
    SpanFunctionProbe: DynamicSpan,
    SpanDecorationFunctionProbe: SpanDecoration,
    SpanDecorationLineProbe: SpanDecoration,
    TriggerFunctionProbe: Trigger,
    TriggerLineProbe: Trigger,
}


def test_every_probe_type_has_a_signal():
    # Every probe type that remote configuration can build must be handled by a
    # signal registered through the catalog.
    probe_types = {
        probe_type
        for factory in PROBE_FACTORY.values()
        for probe_type in (factory.__line_class__, factory.__function_class__)
        if probe_type is not None
    }

    assert probe_types == set(EXPECTED_SIGNALS)
    for probe_type in probe_types:
        assert _lookup_signal_type(probe_type) is EXPECTED_SIGNALS[probe_type]


def test_probe_subclass_uses_registered_ancestor():
    class CustomLogLineProbe(LogLineProbe):
        pass

    assert _lookup_signal_type(CustomLogLineProbe) is Snapshot


def test_unsupported_probe_type():
    class UnknownProbe:
        pass

    with pytest.raises(TypeError, match="Unsupported probe type"):
        _lookup_signal_type(UnknownProbe)


def test_duplicate_registration_raises():
    with pytest.raises(TypeError, match="LogLineProbe is already handled by"):

        @dataclass
        class OtherSnapshot(Snapshot, probes=(LogLineProbe,)):
            pass

    assert _lookup_signal_type(LogLineProbe) is Snapshot


def test_registration_is_not_inherited():
    @dataclass
    class SnapshotSubclass(Snapshot):
        pass

    assert _lookup_signal_type(LogLineProbe) is Snapshot
    assert _lookup_signal_type(LogFunctionProbe) is Snapshot


def test_signal_for_probe_metric_sample_uses_meter():
    probe = create_metric_function_probe(
        probe_id="metric-probe",
        module="tests.submod.stuff",
        func_qname="mutator",
        kind="COUNTER",
        name="test.counter",
    )
    meter = probe_metrics.get_meter("test")

    signal = signal_for_probe(probe, sys._getframe(), threading.current_thread(), None, meter)

    assert isinstance(signal, MetricSample)
    assert signal.meter is meter


def test_signal_for_probe_span_decoration_drops_trace_context():
    probe = create_span_decoration_line_probe(
        probe_id="span-decoration-probe",
        source_file="tests/submod/stuff.py",
        line=1,
    )

    signal = signal_for_probe(
        probe, sys._getframe(), threading.current_thread(), object(), probe_metrics.get_meter("test")
    )

    assert isinstance(signal, SpanDecoration)
    assert signal.trace_context is None


@pytest.mark.subprocess()
def test_signal_package_import_loads_no_submodules():
    # Submodule imports in the package __init__ can deadlock with threads that
    # import a submodule directly (see ddtrace.debugging._signal.catalog).
    import sys

    import ddtrace.debugging._signal  # noqa: F401

    loaded = [m for m in sys.modules if m.startswith("ddtrace.debugging._signal.")]
    assert loaded == [], loaded


CONCURRENT_COLD_IMPORT = """
import importlib
import sys
import threading

import ddtrace  # noqa: F401

assert "ddtrace.debugging._signal" not in sys.modules, "signal package already imported"

barrier = threading.Barrier(2)
errors = []


def run(module):
    barrier.wait()
    try:
        importlib.import_module(module)
    except BaseException as e:
        errors.append((module, repr(e)))


# Code origin and DI start reach the signal package through different submodules.
threads = [
    threading.Thread(target=run, args=("ddtrace.debugging._origin.span",)),
    threading.Thread(target=run, args=("ddtrace.debugging._debugger",)),
]
for t in threads:
    t.start()
for t in threads:
    t.join(30)

assert not any(t.is_alive() for t in threads), "import hung"
assert not errors, errors
"""


def test_concurrent_cold_imports_do_not_deadlock():
    # The race is timing dependent; before the fix it failed in most runs on
    # Python 3.14, so a handful of fresh interpreters is enough to catch it.
    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "false"
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"

    for _ in range(10):
        result = subprocess.run(
            [sys.executable, "-c", CONCURRENT_COLD_IMPORT],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
