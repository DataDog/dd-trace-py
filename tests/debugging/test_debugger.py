from collections import Counter
import os.path
import sys
from threading import Thread
from time import sleep

import mock
from mock.mock import call
import pytest

import ddtrace
from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.tracing import SPAN_NAME
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import DDExpression
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.registry import _get_probe_location
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.utils.inspection import linenos
from tests.debugging.mocking import debugger
from tests.debugging.utils import compile_template
from tests.debugging.utils import create_log_line_probe
from tests.debugging.utils import create_metric_line_probe
from tests.debugging.utils import create_snapshot_function_probe
from tests.debugging.utils import create_snapshot_line_probe
from tests.debugging.utils import create_span_function_probe
from tests.internal.remoteconfig import rcm_endpoint
from tests.submod.stuff import Stuff
from tests.submod.stuff import modulestuff as imported_modulestuff
from tests.utils import TracerTestCase
from tests.utils import call_program


def good_probe():
    # DEV: We build this on demand to ensure that rate limiting gets reset.
    return create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/submod/stuff.py",
        line=36,
    )


def simple_debugger_test(probe, func):
    with debugger() as d:
        probe_id = probe.probe_id

        d.add_probes(probe)
        sleep(0.2)
        try:
            func()
        except Exception:
            pass
        # wait for uploader to write snapshots
        sleep(0.2)

        assert d.uploader.queue
        payloads = list(d.uploader.payloads)
        assert payloads
        for snapshots in payloads:
            assert snapshots
            assert all(s["debugger.snapshot"]["probe"]["id"] == probe_id for s in snapshots)

        return snapshots


def test_debugger_line_probe_on_instance_method():
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=None,
        ),
        lambda: getattr(Stuff(), "instancestuff")(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in (
        "instancestuff(self=Stuff(), bar=None)",
        "instancestuff(bar=None, self=Stuff())",
    ), snapshot["message"]

    captures = snapshot["debugger.snapshot"]["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}
    assert snapshot["debugger.snapshot"]["duration"] is None


def test_debugger_line_probe_on_imported_module_function():

    lineno = min(linenos(imported_modulestuff))
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=lineno,
        ),
        lambda: imported_modulestuff(42),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] == "modulestuff(snafu=42)"
    captures = snapshot["debugger.snapshot"]["captures"]["lines"][str(lineno)]
    assert set(captures["arguments"].keys()) == {"snafu"}
    assert captures["locals"] == {}


@pytest.mark.parametrize(
    "probe, trigger",
    [
        (
            create_snapshot_function_probe(
                probe_id="probe-instance-method",
                module="tests.submod.stuff",
                func_qname="Stuff.instancestuff",
                rate=1000,
            ),
            lambda: getattr(Stuff(), "instancestuff")(42),
        ),
        (
            create_snapshot_line_probe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=36,
                rate=1000,
            ),
            lambda: getattr(Stuff(), "instancestuff")(42),
        ),
    ],
)
def test_debugger_probe_new_delete(probe, trigger):
    global Stuff

    with debugger() as d:
        probe_id = probe.probe_id
        d.add_probes(probe)
        sleep(0.5)

        assert probe in d._probe_registry
        assert _get_probe_location(probe) in sys.modules._locations

        trigger()

        d.remove_probes(probe)

        sleep(0.5)

        # Test that the probe was ejected
        assert probe not in d._probe_registry

        assert _get_probe_location(probe) not in sys.modules._locations

        trigger()

        # Unload and reload the module to ensure that the injection hook
        # has actually been removed.
        # DEV: Once we do this we need to ensure that tests import this
        # module again to refresh their references to objects.
        del sys.modules["tests.submod.stuff"]

        __import__("tests.submod.stuff")
        # Make Stuff refer to the reloaded class
        Stuff = sys.modules["tests.submod.stuff"].Stuff

        trigger()

        assert d.uploader.queue

        (payload,) = d.uploader.payloads
        assert payload

        (snapshot,) = payload
        assert snapshot
        assert snapshot["debugger.snapshot"]["probe"]["id"] == probe_id


def test_debugger_function_probe_on_instance_method():
    snapshots = simple_debugger_test(
        create_snapshot_function_probe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
            condition=None,
        ),
        lambda: Stuff().instancestuff(42),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in (
        "Stuff.instancestuff(self=Stuff(), bar=42)\n@return=42",
        "Stuff.instancestuff(bar=42, self=Stuff())\n@return=42",
    )

    snapshot_data = snapshot["debugger.snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "instancestuff"

    entry_capture = snapshot_data["captures"]["entry"]
    assert set(entry_capture["arguments"].keys()) == {"self", "bar"}
    assert entry_capture["locals"] == {}
    assert entry_capture["throwable"] is None

    return_capture = snapshot_data["captures"]["return"]
    assert set(return_capture["arguments"].keys()) == {"self", "bar"}
    assert set(return_capture["locals"].keys()) == {"@return"}
    assert return_capture["throwable"] is None


def test_debugger_function_probe_on_function_with_exception():
    from tests.submod import stuff

    snapshots = simple_debugger_test(
        create_snapshot_function_probe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="throwexcstuff",
            condition=None,
        ),
        lambda: stuff.throwexcstuff(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] == "throwexcstuff()"

    snapshot_data = snapshot["debugger.snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "throwexcstuff"

    entry_capture = snapshot_data["captures"]["entry"]
    assert entry_capture["arguments"] == {}
    assert entry_capture["locals"] == {}
    assert entry_capture["throwable"] is None

    return_capture = snapshot_data["captures"]["return"]
    assert return_capture["arguments"] == {}
    assert return_capture["locals"] == {}
    assert return_capture["throwable"]["message"] == "'Hello', 'world!', 42"
    assert return_capture["throwable"]["type"] == "Exception"


def test_debugger_invalid_condition():
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                condition="foobar+3",
            ),
            good_probe(),
        )
        sleep(0.5)
        Stuff().instancestuff()
        sleep(0.1)

        assert d.uploader.queue
        for snapshots in d.uploader.payloads[1:]:
            assert snapshots
            assert all(s["debugger.snapshot"]["probe"]["id"] != "foo" for s in snapshots)


def test_debugger_conditional_line_probe_on_instance_method():
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=DDExpression(dsl="True", callable=dd_compile(True)),
        ),
        lambda: getattr(Stuff(), "instancestuff")(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in ("instancestuff(self=Stuff(), bar=None)", "instancestuff(bar=None, self=Stuff())")

    snapshot_data = snapshot["debugger.snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "instancestuff"

    captures = snapshot_data["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}


def test_debugger_invalid_line():
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="invalidline",
                source_file="tests/submod/stuff.py",
                line=360000,
            ),
            good_probe(),
        )
        sleep(0.5)
        Stuff().instancestuff()
        sleep(0.1)

        assert d.uploader.queue
        for snapshots in d.uploader.payloads[1:]:
            assert all(s["debugger.snapshot"]["probe"]["id"] != "invalidline" for s in snapshots)
            assert snapshots


@mock.patch("ddtrace.debugging._debugger.log")
def test_debugger_invalid_source_file(log):
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="invalidsource",
                source_file="tests/submod/bonkers.py",
                line=36,
            ),
            good_probe(),
        )
        sleep(0.5)
        Stuff().instancestuff()
        sleep(0.1)

        log.error.assert_called_once_with(
            "Cannot inject probe %s: source file %s cannot be resolved", "invalidsource", None
        )

        assert d.uploader.queue
        for snapshots in d.uploader.payloads[1:]:
            assert all(s["debugger.snapshot"]["probe"]["id"] != "invalidsource" for s in snapshots)
            assert snapshots


def test_debugger_decorated_method():
    simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-decorated-method",
            source_file="tests/submod/stuff.py",
            line=48,
            condition=None,
        ),
        Stuff().decoratedstuff,
    )


@mock.patch("ddtrace.debugging._debugger.log")
def test_debugger_max_probes(mock_log):
    with debugger(max_probes=1) as d:
        d.add_probes(
            good_probe(),
        )
        assert len(d._probe_registry) == 1
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="probe-decorated-method",
                source_file="tests/submod/stuff.py",
                line=48,
                condition=None,
            ),
        )
        assert len(d._probe_registry) == 1
        mock_log.warning.assert_called_once_with("Too many active probes. Ignoring new ones.")


def test_debugger_tracer_correlation():
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=None,
            )
        )
        sleep(1)

        with d._tracer.trace("test-span") as span:
            trace_id = span.trace_id
            span_id = span.span_id
            sleep(1)
            Stuff().instancestuff()
            sleep(1)

        assert d.uploader.queue
        assert d.uploader.payloads
        for snapshots in d.uploader.payloads[1:]:
            assert snapshots
            assert all(snapshot["dd.trace_id"] == trace_id for snapshot in snapshots)
            assert all(snapshot["dd.span_id"] == span_id for snapshot in snapshots)


def test_debugger_captured_exception():
    from tests.submod import stuff

    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="captured-exception-test",
            source_file="tests/submod/stuff.py",
            line=96,
            condition=None,
        ),
        lambda: getattr(stuff, "excstuff")(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] == "excstuff()"
    captures = snapshot["debugger.snapshot"]["captures"]["lines"]["96"]
    assert captures["throwable"]["message"] == "'Hello', 'world!', 42"
    assert captures["throwable"]["type"] == "Exception"


def test_debugger_multiple_threads():
    with debugger() as d:
        d.add_probes(
            good_probe(),
            create_snapshot_line_probe(probe_id="thread-test", source_file="tests/submod/stuff.py", line=40),
        )
        sleep(0.5)

        callables = [Stuff().instancestuff, lambda: Stuff().propertystuff]
        threads = [Thread(target=callables[_ % len(callables)]) for _ in range(10)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        sleep(1.5)

        if any(len(snapshots) < len(callables) for snapshots in d.uploader.payloads):
            sleep(0.5)

        assert d.uploader.queue
        for snapshots in d.uploader.payloads[1:]:
            assert len(snapshots) >= len(callables)
            assert {s["debugger.snapshot"]["probe"]["id"] for s in snapshots} == {
                "probe-instance-method",
                "thread-test",
            }


@pytest.fixture
def mock_metrics():
    from ddtrace.debugging._debugger import _probe_metrics

    old_client = _probe_metrics._client
    try:
        client = _probe_metrics._client = mock.Mock()
        yield client
    finally:
        _probe_metrics._client = old_client


def create_stuff_line_metric_probe(kind, value=None):
    return create_metric_line_probe(
        probe_id="metric-probe-test",
        source_file="tests/submod/stuff.py",
        line=36,
        kind=kind,
        name="test.counter",
        tags={"foo": "bar"},
        value=DDExpression(dsl="test", callable=dd_compile(value)) if value is not None else None,
    )


def test_debugger_metric_probe_simple_count(mock_metrics):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.COUNTER))
        sleep(0.5)
        Stuff().instancestuff()
        assert call("probe.test.counter", 1.0, ["foo:bar"]) in mock_metrics.increment.mock_calls


def test_debugger_metric_probe_count_value(mock_metrics):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.COUNTER, {"ref": "bar"}))
        sleep(0.5)
        Stuff().instancestuff(40)
        assert call("probe.test.counter", 40.0, ["foo:bar"]) in mock_metrics.increment.mock_calls


def test_debugger_metric_probe_guage_value(mock_metrics):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.GAUGE, {"ref": "bar"}))
        sleep(0.5)
        Stuff().instancestuff(41)
        assert call("probe.test.counter", 41.0, ["foo:bar"]) in mock_metrics.gauge.mock_calls


def test_debugger_metric_probe_histogram_value(mock_metrics):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.HISTOGRAM, {"ref": "bar"}))
        sleep(0.5)
        Stuff().instancestuff(42)
        assert call("probe.test.counter", 42.0, ["foo:bar"]) in mock_metrics.histogram.mock_calls


def test_debugger_metric_probe_distribution_value(mock_metrics):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.DISTRIBUTION, {"ref": "bar"}))
        sleep(0.5)
        Stuff().instancestuff(43)
        assert call("probe.test.counter", 43.0, ["foo:bar"]) in mock_metrics.distribution.mock_calls


def test_debugger_multiple_function_probes_on_same_function():
    global Stuff

    probes = [
        create_snapshot_function_probe(
            probe_id="probe-instance-method-%d" % i,
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
        )
        for i in range(3)
    ]

    with debugger() as d:
        d.add_probes(*probes)
        sleep(0.5)

        assert Stuff.instancestuff.__dd_wrappers__ == {probe.probe_id: probe for probe in probes}
        Stuff().instancestuff(42)

        d.remove_probes(probes[1])

        sleep(2.5)

        assert "probe-instance-method-1" not in Stuff.instancestuff.__dd_wrappers__

        Stuff().instancestuff(42)

        assert Counter(s.probe.probe_id for s in d.test_queue) == {
            "probe-instance-method-0": 2,
            "probe-instance-method-2": 2,
            "probe-instance-method-1": 1,
        }

        d.remove_probes(probes[0], probes[2])

        sleep(2.1)

        Stuff().instancestuff(42)

        assert Counter(s.probe.probe_id for s in d.test_queue) == {
            "probe-instance-method-0": 2,
            "probe-instance-method-2": 2,
            "probe-instance-method-1": 1,
        }

        with pytest.raises(AttributeError):
            Stuff.instancestuff.__dd_wrappers__


# DEV: The following tests are to ensure compatibility with the tracer
import ddtrace.vendor.wrapt as wrapt  # noqa


def wrapper(wrapped, instance, args, kwargs):
    return wrapped(*args, **kwargs)


def test_debugger_function_probe_on_wrapped_function(stuff):
    probes = [
        create_snapshot_function_probe(
            probe_id="probe-on-wrapped-function",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
        )
    ]

    wrapt.wrap_function_wrapper(stuff, "Stuff.instancestuff", wrapper)

    with debugger() as d:
        d.add_probes(*probes)
        sleep(0.5)

        stuff.Stuff().instancestuff(42)

        with d.assert_single_snapshot() as snapshot:
            assert snapshot.probe.probe_id == "probe-on-wrapped-function"


def test_debugger_wrapped_function_on_function_probe(stuff):
    probes = [
        create_snapshot_function_probe(
            probe_id="wrapped-function-on-function-probe",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
        )
    ]
    f = stuff.Stuff.instancestuff
    code = f.__code__

    with debugger() as d:
        d.add_probes(*probes)
        sleep(0.5)

        wrapt.wrap_function_wrapper(stuff, "Stuff.instancestuff", wrapper)
        assert stuff.Stuff.instancestuff.__code__ is stuff.Stuff.instancestuff.__wrapped__.__code__
        assert stuff.Stuff.instancestuff.__code__ is not code
        assert stuff.Stuff.instancestuff.__wrapped__.__dd_wrapped__.__code__ is code
        assert stuff.Stuff.instancestuff is not f

        stuff.Stuff().instancestuff(42)

        with d.assert_single_snapshot() as snapshot:
            assert snapshot.probe.probe_id == "wrapped-function-on-function-probe"

    g = stuff.Stuff.instancestuff
    assert g.__code__ is code
    assert not hasattr(g, "__dd_wrappers__")
    assert not hasattr(g, "__dd_wrapped__")
    assert g is not f


def test_debugger_line_probe_on_wrapped_function(stuff):
    wrapt.wrap_function_wrapper(stuff, "Stuff.instancestuff", wrapper)

    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="line-probe-wrapped-method",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=None,
            )
        )
        sleep(0.5)

        stuff.Stuff().instancestuff(42)

        with d.assert_single_snapshot() as snapshot:
            assert snapshot.probe.probe_id == "line-probe-wrapped-method"


def test_probe_status_logging(monkeypatch):
    monkeypatch.setenv("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", "0.1")
    RemoteConfig.disable()

    from ddtrace.internal.remoteconfig.client import RemoteConfigClient

    old_request = RemoteConfigClient.request

    def request(self, *args, **kwargs):
        for cb in self._products.values():
            cb(None, None)

    RemoteConfigClient.request = request

    try:
        with rcm_endpoint(), debugger(diagnostics_interval=0.5) as d:
            d.add_probes(
                create_snapshot_line_probe(
                    probe_id="line-probe-ok",
                    source_file="tests/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
                create_snapshot_function_probe(
                    probe_id="line-probe-error",
                    module="tests.submod.stuff",
                    func_qname="foo",
                    condition=None,
                ),
            )

            queue = d.probe_status_logger.queue

            def count_status(queue):
                return Counter(_["debugger"]["diagnostics"]["status"] for _ in queue)

            sleep(0.2)
            assert count_status(queue) == {"INSTALLED": 1, "RECEIVED": 2, "ERROR": 1}

            sleep(0.5)
            assert count_status(queue) == {"INSTALLED": 2, "RECEIVED": 2, "ERROR": 2}

            sleep(0.5)
            assert count_status(queue) == {"INSTALLED": 3, "RECEIVED": 2, "ERROR": 3}
    finally:
        RemoteConfigClient.request = old_request


def test_probe_status_logging_reemit_on_modify(monkeypatch):
    monkeypatch.setenv("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", "0.1")
    RemoteConfig.disable()

    from ddtrace.internal.remoteconfig.client import RemoteConfigClient

    old_request = RemoteConfigClient.request

    def request(self, *args, **kwargs):
        for cb in self._products.values():
            cb(None, None)

    RemoteConfigClient.request = request

    try:
        with rcm_endpoint(), debugger(diagnostics_interval=0.5) as d:
            d.add_probes(
                create_snapshot_line_probe(
                    version=1,
                    probe_id="line-probe-ok",
                    source_file="tests/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
            )
            d.modify_probes(
                create_snapshot_line_probe(
                    version=2,
                    probe_id="line-probe-ok",
                    source_file="tests/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
            )

            queue = d.probe_status_logger.queue

            def count_status(queue):
                return Counter(_["debugger"]["diagnostics"]["status"] for _ in queue)

            def versions(queue, status):
                return [
                    _["debugger"]["diagnostics"]["probeVersion"]
                    for _ in queue
                    if _["debugger"]["diagnostics"]["status"] == status
                ]

            sleep(0.2)
            assert count_status(queue) == {"INSTALLED": 2, "RECEIVED": 1}
            assert versions(queue, "INSTALLED") == [1, 2]
            assert versions(queue, "RECEIVED") == [1]

            queue[:] = []

            sleep(0.5)
            assert count_status(queue) == {"INSTALLED": 1}
            assert versions(queue, "INSTALLED") == [2]

    finally:
        RemoteConfigClient.request = old_request


@pytest.mark.parametrize("duration", [1e5, 1e6, 1e7])
def test_debugger_function_probe_duration(duration):
    from tests.submod.stuff import durationstuff

    with debugger(poll_interval=0.1) as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="duration-probe",
                module="tests.submod.stuff",
                func_qname="durationstuff",
            )
        )

        durationstuff(duration)

        with d.assert_single_snapshot() as snapshot:
            assert 0.9 * duration <= snapshot.duration <= 10.0 * duration, snapshot


def test_debugger_condition_eval_then_rate_limit():
    from tests.submod.stuff import Stuff

    with debugger(upload_flush_interval=0.1) as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=DDExpression(dsl="bar == 42", callable=dd_compile({"eq": [{"ref": "bar"}, 42]})),
            ),
        )

        # If the condition is evaluated before the rate limit, all the calls
        # before 42 won't use any of the probe quota. However, all the calls
        # after 42 won't be snapshotted because of the rate limiter.
        for i in range(100):
            Stuff().instancestuff(i)

        sleep(0.5)

        # We expect to see just the snapshot generated by the 42 call.
        assert d.event_state_counter[CaptureState.SKIP_COND] == 99
        assert d.event_state_counter[CaptureState.DONE_AND_COMMIT] == 1

        (snapshots,) = d.uploader.payloads
        (snapshot,) = snapshots
        assert "42" == snapshot["debugger.snapshot"]["captures"]["lines"]["36"]["arguments"]["bar"]["value"], snapshot


def test_debugger_condition_eval_error_get_reported_once():
    from tests.submod.stuff import Stuff

    with debugger(upload_flush_interval=0.1) as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=DDExpression(dsl="foo == 42", callable=dd_compile({"eq": [{"ref": "foo"}, 42]})),
            ),
        )

        # all condition eval would fail
        for i in range(100):
            Stuff().instancestuff(i)

        sleep(0.5)

        # We expect to see just the snapshot with error only.
        assert d.event_state_counter[CaptureState.SKIP_COND_ERROR] == 99
        assert d.event_state_counter[CaptureState.COND_ERROR_AND_COMMIT] == 1

        (snapshots,) = d.uploader.payloads
        (snapshot,) = snapshots
        evaluationErrors = snapshot["debugger.snapshot"]["evaluationErrors"]
        assert 1 == len(evaluationErrors)
        assert "foo == 42" == evaluationErrors[0]["expr"]
        assert "'foo'" == evaluationErrors[0]["message"]


def test_debugger_function_probe_eval_on_enter():
    from tests.submod.stuff import mutator

    with debugger() as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="enter-probe",
                module="tests.submod.stuff",
                func_qname="mutator",
                evaluate_at=ProbeEvaluateTimingForMethod.ENTER,
                condition=DDExpression(
                    dsl="not(contains(arg,42))", callable=dd_compile({"not": {"contains": [{"ref": "arg"}, 42]}})
                ),
            )
        )

        mutator(arg=[])

        with d.assert_single_snapshot() as snapshot:
            assert snapshot, d.test_queue
            assert 0 == snapshot.entry_capture["arguments"]["arg"]["size"]
            assert 1 == snapshot.return_capture["arguments"]["arg"]["size"]


def test_debugger_run_module():
    # This is where the target module resides
    cwd = os.path.join(os.path.dirname(__file__), "run_module")

    # This is also where the sitecustomize resides, so we set the PYTHONPATH
    # accordingly. This is responsible for booting the test debugger
    env = os.environ.copy()
    env["PYTHONPATH"] = cwd

    out, err, status, _ = call_program(sys.executable, "-m", "target", cwd=cwd, env=env)

    assert out.strip() == b"OK", err.decode()
    assert status == 0


def test_debugger_function_probe_eval_on_exit():
    from tests.submod.stuff import mutator

    with debugger() as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="exit-probe",
                module="tests.submod.stuff",
                func_qname="mutator",
                evaluate_at=ProbeEvaluateTimingForMethod.EXIT,
                condition=DDExpression(dsl="contains(arg,42)", callable=dd_compile({"contains": [{"ref": "arg"}, 42]})),
            )
        )

        mutator(arg=[])

        with d.assert_single_snapshot() as snapshot:
            assert snapshot, d.test_queue
            assert not snapshot.entry_capture
            assert 1 == snapshot.return_capture["arguments"]["arg"]["size"]


def test_debugger_lambda_fuction_access_locals():
    from tests.submod.stuff import age_checker

    class Person(object):
        def __init__(self, age, name):
            self.age = age
            self.name = name

    with debugger() as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="lambda-probe",
                module="tests.submod.stuff",
                func_qname="age_checker",
                condition=DDExpression(
                    dsl="any(people, @it.name == name)",
                    callable=dd_compile(
                        {"any": [{"ref": "people"}, {"eq": [{"ref": "name"}, {"getmember": [{"ref": "@it"}, "name"]}]}]}
                    ),
                ),
            )
        )

        # should capture as alice is in people list
        age_checker(people=[Person(10, "alice"), Person(20, "bob"), Person(30, "charile")], age=18, name="alice")

        # should skip as david is not in people list
        age_checker(people=[Person(10, "alice"), Person(20, "bob"), Person(30, "charile")], age=18, name="david")

        assert d.event_state_counter[CaptureState.SKIP_COND] == 1
        assert d.event_state_counter[CaptureState.DONE_AND_COMMIT] == 1

        with d.assert_single_snapshot() as snapshot:
            assert snapshot, d.test_queue


def test_debugger_log_live_probe_generate_messages():
    from tests.submod.stuff import Stuff

    with debugger(upload_flush_interval=0.1) as d:
        d.add_probes(
            create_log_line_probe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                **compile_template(
                    "hello world ",
                    {"dsl": "foo", "json": {"ref": "foo"}},
                    " ",
                    {"dsl": "bar", "json": {"ref": "bar"}},
                    "!",
                )
            ),
        )

        Stuff().instancestuff(123)
        Stuff().instancestuff(456)

        sleep(0.5)

        (msgs,) = d.uploader.payloads
        msg1, msg2 = msgs
        assert "hello world ERROR 123!" == msg1["message"], msg1
        assert "hello world ERROR 456!" == msg2["message"], msg2

        assert "foo" == msg1["debugger.snapshot"]["evaluationErrors"][0]["expr"], msg1
        # not amazing error message for a missing variable
        assert "'foo'" == msg1["debugger.snapshot"]["evaluationErrors"][0]["message"], msg1

        assert not msg1["debugger.snapshot"]["captures"]


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

    def test_debugger_span_probe(self):
        from tests.submod.stuff import mutator

        with debugger() as d:
            d.add_probes(
                create_span_function_probe(
                    probe_id="span-probe", module="tests.submod.stuff", func_qname="mutator", tags={"tag": "value"}
                )
            )

            mutator(arg=[])

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == SPAN_NAME
            assert span.resource == "mutator"
            tags = span.get_tags()
            assert tags["debugger.probeid"] == "span-probe"
            assert tags["tag"] == "value"

    def test_debugger_span_not_created_when_condition_was_false(self):
        from tests.submod.stuff import mutator

        with debugger() as d:
            d.add_probes(
                create_span_function_probe(
                    probe_id="span-probe",
                    module="tests.submod.stuff",
                    func_qname="mutator",
                    condition=DDExpression(
                        dsl="not(contains(arg,42))", callable=dd_compile({"not": {"contains": [{"ref": "arg"}, 42]}})
                    ),
                )
            )

            mutator(arg=[42])  # should not trigger span

            self.assert_span_count(0)

            mutator(arg=[])  # should trigger span

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == SPAN_NAME
            assert span.resource == "mutator"
            assert span.get_tags()["debugger.probeid"] == "span-probe"

    def test_debugger_snap_probe_linked_to_parent_span(self):
        from tests.submod.stuff import mutator

        with debugger() as d:
            d.add_probes(
                create_span_function_probe(probe_id="exit-probe", module="tests.submod.stuff", func_qname="mutator")
            )

            with self.tracer.trace("parent_span"):
                mutator(arg=[])

            self.assert_span_count(2)
            root, span = self.get_spans()

            assert root.name == "parent_span"

            assert span.name == SPAN_NAME
            assert span.resource == "mutator"
            assert span.get_tag("debugger.probeid") == "exit-probe"

            assert span.parent_id == root.span_id

    def test_debugger_snap_probe_root(self):
        from tests.submod.stuff import caller

        @self.tracer.wrap("child")
        def child():
            pass

        with debugger() as d:
            d.add_probes(
                create_span_function_probe(
                    probe_id="root-dynamic-span-probe", module="tests.submod.stuff", func_qname="caller"
                )
            )

            caller(child)

            self.assert_span_count(2)
            root, span = self.get_spans()

            assert root.name == SPAN_NAME
            assert root.resource == "caller"
            assert root.get_tag("debugger.probeid") == "root-dynamic-span-probe"

            assert span.name == "child"

            assert span.parent_id is root.span_id


def test_debugger_modified_probe():
    from tests.submod.stuff import Stuff

    with debugger(upload_flush_interval=0.1) as d:
        d.add_probes(
            create_log_line_probe(
                probe_id="foo",
                version=1,
                source_file="tests/submod/stuff.py",
                line=36,
                **compile_template("hello world")
            )
        )

        Stuff().instancestuff()

        sleep(0.2)

        ((msg,),) = d.uploader.payloads
        assert "hello world" == msg["message"], msg
        assert msg["debugger.snapshot"]["probe"]["version"] == 1, msg

        d.modify_probes(
            create_log_line_probe(
                probe_id="foo",
                version=2,
                source_file="tests/submod/stuff.py",
                line=36,
                **compile_template("hello brave new world")
            )
        )

        Stuff().instancestuff()

        sleep(0.2)

        _, (msg,) = d.uploader.payloads
        assert "hello brave new world" == msg["message"], msg
        assert msg["debugger.snapshot"]["probe"]["version"] == 2, msg


def test_debugger_continue_wrapping_after_first_failure():
    with debugger() as d:
        probe_nok = create_snapshot_function_probe(
            probe_id="function-probe-nok",
            module="tests.submod.stuff",
            func_qname="nonsense",
        )
        probe_ok = create_snapshot_function_probe(
            probe_id="function-probe-ok",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
        )
        d.add_probes(probe_nok, probe_ok)

        assert probe_nok in d._probe_registry
        assert probe_ok in d._probe_registry

        assert not d._probe_registry[probe_nok.probe_id].installed
        assert d._probe_registry[probe_ok.probe_id].installed
