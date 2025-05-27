from collections import Counter
from decimal import Decimal
import os.path
import sys
from threading import Thread

import mock
from mock.mock import call
import pytest

import ddtrace
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.debugging._debugger import DebuggerWrappingContext
from ddtrace.debugging._probe.model import DDExpression
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.model import SpanDecoration
from ddtrace.debugging._probe.model import SpanDecorationTag
from ddtrace.debugging._probe.model import SpanDecorationTargetSpan
from ddtrace.debugging._probe.registry import _get_probe_location
from ddtrace.debugging._redaction import REDACTED_PLACEHOLDER as REDACTED
from ddtrace.debugging._redaction import dd_compile_redacted as dd_compile
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.snapshot import _EMPTY_CAPTURED_CONTEXT
from ddtrace.debugging._signal.tracing import SPAN_NAME
from ddtrace.debugging._signal.utils import redacted_value
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.internal.utils.inspection import linenos
from tests.debugging.mocking import debugger
from tests.debugging.utils import compile_template
from tests.debugging.utils import create_log_function_probe
from tests.debugging.utils import create_log_line_probe
from tests.debugging.utils import create_metric_line_probe
from tests.debugging.utils import create_snapshot_function_probe
from tests.debugging.utils import create_snapshot_line_probe
from tests.debugging.utils import create_span_decoration_function_probe
from tests.debugging.utils import create_span_function_probe
from tests.debugging.utils import ddexpr
from tests.debugging.utils import ddstrtempl
from tests.internal.remoteconfig import rcm_endpoint
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

        # Check that we can still hash the code object
        assert hash(func.__code__)

        try:
            func()
        except Exception:
            pass

        snapshots = d.uploader.wait_for_payloads()
        assert all(s["debugger"]["snapshot"]["probe"]["id"] == probe_id for s in snapshots)

        return snapshots


def test_debugger_line_probe_on_instance_method(stuff):
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=None,
        ),
        stuff.Stuff().instancestuff,
    )

    (snapshot,) = snapshots
    captures = snapshot["debugger"]["snapshot"]["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}
    assert snapshot["debugger"]["snapshot"]["duration"] is None


def test_debugger_line_probe_on_imported_module_function(stuff):
    lineno = min(linenos(stuff.modulestuff))
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=lineno,
        ),
        lambda: stuff.modulestuff(42),
    )

    (snapshot,) = snapshots
    captures = snapshot["debugger"]["snapshot"]["captures"]["lines"][str(lineno)]
    assert set(captures["arguments"].keys()) == {"snafu"}
    assert captures["locals"] == {}


@pytest.mark.parametrize(
    "probe",
    [
        (
            create_snapshot_function_probe(
                probe_id="probe-instance-method",
                module="tests.submod.stuff",
                func_qname="Stuff.instancestuff",
                rate=1000,
            )
        ),
        (
            create_snapshot_line_probe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=36,
                rate=1000,
            )
        ),
    ],
)
def test_debugger_probe_new_delete(probe, stuff):
    with debugger() as d:
        probe_id = probe.probe_id
        d.add_probes(probe)

        assert probe in d._probe_registry
        assert _get_probe_location(probe) in d.__watchdog__._instance._locations

        stuff.Stuff().instancestuff(42)

        d.remove_probes(probe)

        # Test that the probe was ejected
        assert probe not in d._probe_registry

        assert _get_probe_location(probe) not in d.__watchdog__._instance._locations

        stuff.Stuff().instancestuff(42)

        # Unload and reload the module to ensure that the injection hook
        # has actually been removed.
        # DEV: Once we do this we need to ensure that tests import this
        # module again to refresh their references to objects.
        del sys.modules["tests.submod.stuff"]

        __import__("tests.submod.stuff")
        # Make Stuff refer to the reloaded class
        stuff.Stuff = sys.modules["tests.submod.stuff"].Stuff

        stuff.Stuff().instancestuff(42)

        (snapshot,) = d.uploader.wait_for_payloads()
        assert snapshot["debugger"]["snapshot"]["probe"]["id"] == probe_id


def test_debugger_function_probe_on_instance_method(stuff):
    snapshots = simple_debugger_test(
        create_snapshot_function_probe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
            condition=None,
        ),
        lambda: stuff.Stuff().instancestuff(42),
    )

    (snapshot,) = snapshots
    snapshot_data = snapshot["debugger"]["snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "instancestuff"

    assert snapshot_data["captures"]["entry"] == _EMPTY_CAPTURED_CONTEXT

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
        stuff.throwexcstuff,
    )

    (snapshot,) = snapshots
    snapshot_data = snapshot["debugger"]["snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "throwexcstuff"
    assert snapshot_data["stack"][0]["lineNumber"] == 110

    entry_capture = snapshot_data["captures"]["entry"]
    assert entry_capture["arguments"] == {}
    assert entry_capture["locals"] == {}
    assert entry_capture["throwable"] is None

    return_capture = snapshot_data["captures"]["return"]
    assert return_capture["arguments"] == {}
    assert return_capture["locals"] == {
        "@exception": {
            "type": "Exception",
            "fields": {
                "args": {
                    "type": "tuple",
                    "elements": [
                        {"type": "str", "value": "'Hello'"},
                        {"type": "str", "value": "'world!'"},
                        {"type": "int", "value": "42"},
                    ],
                    "size": 3,
                },
                "__cause__": {"type": "NoneType", "isNull": True},
                "__context__": {"type": "NoneType", "isNull": True},
                "__suppress_context__": {"type": "bool", "value": "False"},
            },
        }
    }
    assert return_capture["throwable"]["message"] == "'Hello', 'world!', 42"
    assert return_capture["throwable"]["type"] == "Exception"


def test_debugger_invalid_condition(stuff):
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
        stuff.Stuff().instancestuff()

        assert all(s["debugger"]["snapshot"]["probe"]["id"] != "foo" for s in d.uploader.wait_for_payloads())


def test_debugger_conditional_line_probe_on_instance_method(stuff):
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=DDExpression(dsl="True", callable=dd_compile(True)),
        ),
        lambda: stuff.Stuff().instancestuff(),
    )

    (snapshot,) = snapshots
    snapshot_data = snapshot["debugger"]["snapshot"]
    assert snapshot_data["stack"][0]["fileName"].endswith("stuff.py")
    assert snapshot_data["stack"][0]["function"] == "instancestuff"

    captures = snapshot_data["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}


@mock.patch("ddtrace.debugging._debugger.log")
def test_debugger_line_probe_on_class_method(mock_log, stuff):
    from tests.debugging.mocking import PayloadWaitTimeout

    raised = False
    try:
        simple_debugger_test(
            create_snapshot_line_probe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=173,
            ),
            lambda: stuff.TestClass().fake_get(),
        )
    except PayloadWaitTimeout:
        mock_log.error.assert_called_once()
        call_args = mock_log.error.call_args[0]
        assert "Cannot install probe probe-instance-method: function at line 173" in call_args[0]
        raised = True

    assert not raised


def test_debugger_invalid_line(stuff):
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="invalidline",
                source_file="tests/submod/stuff.py",
                line=360000,
            ),
            good_probe(),
        )
        stuff.Stuff().instancestuff()

        assert all(s["debugger"]["snapshot"]["probe"]["id"] != "invalidline" for s in d.uploader.wait_for_payloads())


@mock.patch("ddtrace.debugging._debugger.log")
def test_debugger_invalid_source_file(log, stuff):
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="invalidsource",
                source_file="tests/submod/bonkers.py",
                line=36,
            ),
            good_probe(),
        )
        stuff.Stuff().instancestuff()

        log.error.assert_called_once_with(
            "Cannot inject probe %s: source file %s cannot be resolved", "invalidsource", "tests/submod/bonkers.py"
        )

        assert all(s["debugger"]["snapshot"]["probe"]["id"] != "invalidsource" for s in d.uploader.wait_for_payloads())


def test_debugger_decorated_method(stuff):
    simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="probe-decorated-method",
            source_file="tests/submod/stuff.py",
            line=48,
            condition=None,
        ),
        stuff.Stuff().decoratedstuff,
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


def test_debugger_tracer_correlation(stuff):
    with debugger() as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=None,
            )
        )

        with d._tracer.trace("test-span") as span:
            trace_id = format_trace_id(span.trace_id)
            span_id = str(span.span_id)
            stuff.Stuff().instancestuff()

        snapshots = d.uploader.wait_for_payloads()
        assert all(snapshot["dd"]["trace_id"] == trace_id for snapshot in snapshots)
        assert all(snapshot["dd"]["span_id"] == span_id for snapshot in snapshots)


def test_debugger_captured_exception(stuff):
    snapshots = simple_debugger_test(
        create_snapshot_line_probe(
            probe_id="captured-exception-test",
            source_file="tests/submod/stuff.py",
            line=96,
            condition=None,
        ),
        lambda: stuff.excstuff(),
    )

    (snapshot,) = snapshots
    captures = snapshot["debugger"]["snapshot"]["captures"]["lines"]["96"]
    assert captures["throwable"]["message"] == "'Hello', 'world!', 42"
    assert captures["throwable"]["type"] == "Exception"


def test_debugger_multiple_threads(stuff):
    with debugger() as d:
        probes = [
            good_probe(),
            create_snapshot_line_probe(probe_id="thread-test", source_file="tests/submod/stuff.py", line=40),
        ]
        d.add_probes(*probes)

        callables = [stuff.Stuff().instancestuff, lambda: stuff.Stuff().propertystuff]
        threads = [Thread(target=callables[_ % len(callables)]) for _ in range(10)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        q = d.collector.wait(cond=lambda q: len(q) >= len(callables))
        assert {_.probe.probe_id for _ in q} == {p.probe_id for p in probes}


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
        tags={"foo": "bar", "debugger.probeid": "metric-probe-test"},
        value=DDExpression(dsl="test", callable=dd_compile(value)) if value is not None else None,
    )


def test_debugger_metric_probe_simple_count(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.COUNTER))
        stuff.Stuff().instancestuff()
        assert (
            call("probe.test.counter", 1.0, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.increment.mock_calls
        )


def test_debugger_metric_probe_decimal(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.COUNTER, value=Decimal(value := 3.14)))
        stuff.Stuff().instancestuff()
        assert (
            call("probe.test.counter", value, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.increment.mock_calls
        )


def test_debugger_metric_probe_count_value(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.COUNTER, {"ref": "bar"}))
        stuff.Stuff().instancestuff(40)
        assert (
            call("probe.test.counter", 40.0, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.increment.mock_calls
        )


def test_debugger_metric_probe_guage_value(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.GAUGE, {"ref": "bar"}))
        stuff.Stuff().instancestuff(41)
        assert (
            call("probe.test.counter", 41.0, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.gauge.mock_calls
        )


def test_debugger_metric_probe_histogram_value(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.HISTOGRAM, {"ref": "bar"}))
        stuff.Stuff().instancestuff(42)
        assert (
            call("probe.test.counter", 42.0, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.histogram.mock_calls
        )


def test_debugger_metric_probe_distribution_value(mock_metrics, stuff):
    with debugger() as d:
        d.add_probes(create_stuff_line_metric_probe(MetricProbeKind.DISTRIBUTION, {"ref": "bar"}))
        stuff.Stuff().instancestuff(43)
        assert (
            call("probe.test.counter", 43.0, ["foo:bar", "debugger.probeid:metric-probe-test"])
            in mock_metrics.distribution.mock_calls
        )


def test_debugger_multiple_function_probes_on_same_function(stuff):
    probes = [
        create_snapshot_function_probe(
            probe_id="probe-instance-method-%d" % i,
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
            rate=float("inf"),
        )
        for i in range(3)
    ]

    with debugger() as d:
        d.add_probes(*probes)

        wrapping_context = DebuggerWrappingContext.extract(stuff.Stuff.instancestuff)
        assert wrapping_context.probes == {probe.probe_id: probe for probe in probes}
        stuff.Stuff().instancestuff(42)

        d.collector.wait(
            lambda q: Counter(s.probe.probe_id for s in q)
            == {
                "probe-instance-method-0": 1,
                "probe-instance-method-1": 1,
                "probe-instance-method-2": 1,
            }
        )

        d.remove_probes(probes[1])

        assert "probe-instance-method-1" not in wrapping_context.probes

        stuff.Stuff().instancestuff(42)

        d.collector.wait(
            lambda q: Counter(s.probe.probe_id for s in q)
            == {
                "probe-instance-method-0": 2,
                "probe-instance-method-2": 2,
                "probe-instance-method-1": 1,
            }
        )

        d.remove_probes(probes[0], probes[2])

        stuff.Stuff().instancestuff(42)

        assert Counter(s.probe.probe_id for s in d.test_queue) == {
            "probe-instance-method-0": 2,
            "probe-instance-method-2": 2,
            "probe-instance-method-1": 1,
        }

        with pytest.raises(AttributeError):
            stuff.Stuff.instancestuff.__dd_wrappers__


def test_debugger_multiple_function_probes_on_same_lazy_module():
    probes = [
        create_snapshot_function_probe(
            probe_id="probe-instance-method-%d" % i,
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
            rate=float("inf"),
        )
        for i in range(3)
    ]

    with debugger() as d:
        d.add_probes(*probes)

        import tests.submod.stuff  # noqa:F401

        assert len(d._probe_registry) == len(probes)
        assert all(_.error_type is None for _ in d._probe_registry.values())


# DEV: The following tests are to ensure compatibility with the tracer
import wrapt  # noqa:E402,F401


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

        wrapt.wrap_function_wrapper(stuff, "Stuff.instancestuff", wrapper)

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

        stuff.Stuff().instancestuff(42)

        with d.assert_single_snapshot() as snapshot:
            assert snapshot.probe.probe_id == "line-probe-wrapped-method"


def test_probe_status_logging(remote_config_worker, stuff):
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with rcm_endpoint(), debugger(diagnostics_interval=float("inf"), enabled=True) as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="line-probe-ok",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=None,
                rate=float("inf"),
            ),
            create_snapshot_function_probe(
                probe_id="line-probe-error",
                module="tests.submod.stuff",
                func_qname="foo",
                condition=None,
            ),
        )

        # Call the function multiple times to ensure that we emit only once.
        for _ in range(10):
            stuff.Stuff().instancestuff(42)

        logger = d.probe_status_logger

        def count_status(queue):
            return Counter(_["debugger"]["diagnostics"]["status"] for _ in queue)

        logger.wait(lambda q: count_status(q) == {"INSTALLED": 1, "RECEIVED": 2, "ERROR": 1, "EMITTING": 1})

        d.log_probe_status()
        logger.wait(lambda q: count_status(q) == {"INSTALLED": 1, "RECEIVED": 2, "ERROR": 2, "EMITTING": 2})

        d.log_probe_status()
        logger.wait(lambda q: count_status(q) == {"INSTALLED": 1, "RECEIVED": 2, "ERROR": 3, "EMITTING": 3})


def test_probe_status_logging_reemit_on_modify(remote_config_worker):
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with rcm_endpoint(), debugger(diagnostics_interval=float("inf"), enabled=True) as d:
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

        logger = d.probe_status_logger
        queue = logger.queue

        def count_status(queue):
            return Counter(_["debugger"]["diagnostics"]["status"] for _ in queue)

        def versions(queue, status):
            return [
                _["debugger"]["diagnostics"]["probeVersion"]
                for _ in queue
                if _["debugger"]["diagnostics"]["status"] == status
            ]

        logger.wait(lambda q: count_status(q) == {"INSTALLED": 2, "RECEIVED": 1})
        assert versions(queue, "INSTALLED") == [1, 2]
        assert versions(queue, "RECEIVED") == [1]

        d.log_probe_status()
        logger.wait(lambda q: count_status(q) == {"INSTALLED": 3, "RECEIVED": 1})
        assert versions(queue, "INSTALLED") == [1, 2, 2]


@pytest.mark.parametrize("duration", [1e5, 1e6, 1e7])
def test_debugger_function_probe_duration(duration):
    from tests.submod.stuff import durationstuff

    with debugger(poll_interval=0) as d:
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


def test_debugger_condition_eval_then_rate_limit(stuff):
    with debugger(upload_flush_interval=float("inf")) as d:
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
            stuff.Stuff().instancestuff(i)

        (snapshot,) = d.uploader.wait_for_payloads()

        # We expect to see just the snapshot generated by the 42 call.
        assert d.signal_state_counter[SignalState.SKIP_COND] == 99
        assert d.signal_state_counter[SignalState.DONE] == 1

        assert (
            "42" == snapshot["debugger"]["snapshot"]["captures"]["lines"]["36"]["arguments"]["bar"]["value"]
        ), snapshot


def test_debugger_condition_eval_error_get_reported_once(stuff):
    with debugger(upload_flush_interval=float("inf")) as d:
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
            stuff.Stuff().instancestuff(i)

        (snapshot,) = d.uploader.wait_for_payloads()

        # We expect to see just the snapshot with error only.
        assert d.signal_state_counter[SignalState.SKIP_COND_ERROR] == 99
        assert d.signal_state_counter[SignalState.COND_ERROR] == 1

        evaluationErrors = snapshot["debugger"]["snapshot"]["evaluationErrors"]
        assert 1 == len(evaluationErrors)
        assert "foo == 42" == evaluationErrors[0]["expr"]
        assert "No such local variable: 'foo'" == evaluationErrors[0]["message"]


def test_debugger_function_probe_eval_on_entry(stuff):
    with debugger() as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="enter-probe",
                module="tests.submod.stuff",
                func_qname="mutator",
                evaluate_at=ProbeEvalTiming.ENTRY,
                condition=DDExpression(
                    dsl="not(contains(arg,42))", callable=dd_compile({"not": {"contains": [{"ref": "arg"}, 42]}})
                ),
            )
        )

        stuff.mutator(arg=[])

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
    env["PYTHONPATH"] = os.pathsep.join((cwd, env.get("PYTHONPATH", "")))

    out, err, status, _ = call_program(sys.executable, "-m", "target", cwd=cwd, env=env)

    assert out.strip() == b"OK", err.decode()
    assert status == 0


def test_debugger_function_probe_eval_on_exit(stuff):
    with debugger() as d:
        d.add_probes(
            create_snapshot_function_probe(
                probe_id="exit-probe",
                module="tests.submod.stuff",
                func_qname="mutator",
                evaluate_at=ProbeEvalTiming.EXIT,
                condition=DDExpression(dsl="contains(arg,42)", callable=dd_compile({"contains": [{"ref": "arg"}, 42]})),
            )
        )

        stuff.mutator(arg=[])

        with d.assert_single_snapshot() as snapshot:
            assert snapshot, d.test_queue
            assert not snapshot.entry_capture
            assert 1 == snapshot.return_capture["arguments"]["arg"]["size"]


def test_debugger_lambda_fuction_access_locals(stuff):
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
        stuff.age_checker(people=[Person(10, "alice"), Person(20, "bob"), Person(30, "charile")], age=18, name="alice")

        # should skip as david is not in people list
        stuff.age_checker(people=[Person(10, "alice"), Person(20, "bob"), Person(30, "charile")], age=18, name="david")

        assert d.signal_state_counter[SignalState.SKIP_COND] == 1
        assert d.signal_state_counter[SignalState.DONE] == 1

        with d.assert_single_snapshot() as snapshot:
            assert snapshot, d.test_queue


def test_debugger_log_line_probe_generate_messages(stuff):
    with debugger(upload_flush_interval=float("inf")) as d:
        d.add_probes(
            create_log_line_probe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                rate=float("inf"),
                **compile_template(
                    "hello world ",
                    {"dsl": "foo", "json": {"ref": "foo"}},
                    " ",
                    {"dsl": "bar", "json": {"ref": "bar"}},
                    "!",
                ),
            ),
        )

        stuff.Stuff().instancestuff(123)
        stuff.Stuff().instancestuff(456)

        msg1, msg2 = d.uploader.wait_for_payloads(2)
        assert "hello world ERROR 123!" == msg1["message"], msg1
        assert "hello world ERROR 456!" == msg2["message"], msg2

        assert "foo" == msg1["debugger"]["snapshot"]["evaluationErrors"][0]["expr"], msg1
        assert "No such local variable: 'foo'" == msg1["debugger"]["snapshot"]["evaluationErrors"][0]["message"], msg1

        assert not msg1["debugger"]["snapshot"]["captures"]


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
            assert tags[_ORIGIN_KEY] == "di"

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

    def test_debugger_function_probe_ordering(self):
        from tests.submod.stuff import mutator

        with debugger() as d:
            d.add_probes(
                create_log_function_probe(
                    probe_id="log-probe", module="tests.submod.stuff", func_qname="mutator", template="", segments=[]
                ),
                create_span_decoration_function_probe(
                    probe_id="span-decoration",
                    module="tests.submod.stuff",
                    func_qname="mutator",
                    evaluate_at=ProbeEvalTiming.EXIT,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[SpanDecorationTag(name="test.tag", value=ddstrtempl(["test.value"]))],
                        )
                    ],
                ),
                create_span_function_probe(
                    probe_id="span-probe", module="tests.submod.stuff", func_qname="mutator", tags={"tag": "value"}
                ),
            )

            with self.tracer.trace("outer"):
                mutator(arg=[])

            self.assert_span_count(2)
            (outer, span) = self.get_spans()

            (log,) = d.test_queue
            assert log.trace_context.trace_id == span.trace_id
            assert log.trace_context.span_id == span.span_id

            assert outer.name == "outer"
            assert outer.get_tag("test.tag") is None

            assert span.name == SPAN_NAME
            assert span.resource == "mutator"
            assert span.get_tag("test.tag") == "test.value"


def test_debugger_modified_probe(stuff):
    with debugger(upload_flush_interval=float("inf")) as d:
        d.add_probes(
            create_log_line_probe(
                probe_id="foo",
                version=1,
                source_file="tests/submod/stuff.py",
                line=36,
                **compile_template("hello world"),
            )
        )

        stuff.Stuff().instancestuff()

        (msg,) = d.uploader.wait_for_payloads()
        assert "hello world" == msg["message"], msg
        assert msg["debugger"]["snapshot"]["probe"]["version"] == 1, msg

        d.modify_probes(
            create_log_line_probe(
                probe_id="foo",
                version=2,
                source_file="tests/submod/stuff.py",
                line=36,
                **compile_template("hello brave new world"),
            )
        )

        stuff.Stuff().instancestuff()

        _, msg = d.uploader.wait_for_payloads(2)
        assert "hello brave new world" == msg["message"], msg
        assert msg["debugger"]["snapshot"]["probe"]["version"] == 2, msg


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


def test_debugger_redacted_identifiers():
    import tests.submod.stuff as stuff

    with debugger(upload_flush_interval=float("inf")) as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="foo",
                version=1,
                source_file="tests/submod/stuff.py",
                line=169,
                **compile_template(
                    "token=",
                    {"dsl": "token", "json": {"ref": "token"}},
                    " answer=",
                    {"dsl": "answer", "json": {"ref": "answer"}},
                    " pii_dict=",
                    {"dsl": "pii_dict", "json": {"ref": "pii_dict"}},
                    " pii_dict['jwt']=",
                    {"dsl": "pii_dict['jwt']", "json": {"index": [{"ref": "pii_dict"}, "jwt"]}},
                ),
            ),
            create_snapshot_function_probe(
                probe_id="function-probe",
                module="tests.submod.stuff",
                func_qname="sensitive_stuff",
                evaluate_at=ProbeEvalTiming.EXIT,
            ),
        )

        stuff.sensitive_stuff("top secret")

        msg_line, msg_func = d.uploader.wait_for_payloads(2)

        assert (
            msg_line["message"] == f"token={REDACTED} answer=42 "
            f"pii_dict={{'jwt': '{REDACTED}', 'password': '{REDACTED}', 'username': 'admin'}} "
            f"pii_dict['jwt']={REDACTED}"
        )

        assert msg_line["debugger"]["snapshot"]["captures"]["lines"]["169"] == {
            "arguments": {"pwd": redacted_value(str())},
            "locals": {
                "token": redacted_value(str()),
                "answer": {"type": "int", "value": "42"},
                "data": {
                    "type": "SensitiveData",
                    "fields": {"password": {"notCapturedReason": "redactedIdent", "type": "str"}},
                },
                "pii_dict": {
                    "type": "dict",
                    "entries": [
                        [{"type": "str", "value": "'jwt'"}, redacted_value(str())],
                        [{"type": "str", "value": "'password'"}, redacted_value(str())],
                        [{"type": "str", "value": "'username'"}, {"type": "str", "value": "'admin'"}],
                    ],
                    "size": 3,
                },
            },
            "staticFields": {},
            "throwable": None,
        }

        assert msg_func["debugger"]["snapshot"]["captures"] == {
            "entry": {"arguments": {}, "locals": {}, "staticFields": {}, "throwable": None},
            "return": {
                "arguments": {"pwd": {"type": "str", "notCapturedReason": "redactedIdent"}},
                "locals": {
                    "token": {"type": "str", "notCapturedReason": "redactedIdent"},
                    "answer": {"type": "int", "value": "42"},
                    "data": {
                        "type": "SensitiveData",
                        "fields": {"password": {"type": "str", "notCapturedReason": "redactedIdent"}},
                    },
                    "pii_dict": {
                        "type": "dict",
                        "entries": [
                            [{"type": "str", "value": "'jwt'"}, {"type": "str", "notCapturedReason": "redactedIdent"}],
                            [
                                {"type": "str", "value": "'password'"},
                                {"type": "str", "notCapturedReason": "redactedIdent"},
                            ],
                            [{"type": "str", "value": "'username'"}, {"type": "str", "value": "'admin'"}],
                        ],
                        "size": 3,
                    },
                    "@return": {"type": "str", "value": "'top secret'"},  # TODO: Ouch!
                },
                "staticFields": {},
                "throwable": None,
            },
        }


def test_debugger_redaction_excluded_identifiers():
    import tests.submod.stuff as stuff

    with debugger(upload_flush_interval=float("inf"), redaction_excluded_identifiers=frozenset(["token"])) as d:
        d.add_probes(
            create_snapshot_line_probe(
                probe_id="foo",
                version=1,
                source_file="tests/submod/stuff.py",
                line=169,
                **compile_template(
                    "token=",
                    {"dsl": "token", "json": {"ref": "token"}},
                    " answer=",
                    {"dsl": "answer", "json": {"ref": "answer"}},
                    " pii_dict=",
                    {"dsl": "pii_dict", "json": {"ref": "pii_dict"}},
                    " pii_dict['jwt']=",
                    {"dsl": "pii_dict['jwt']", "json": {"index": [{"ref": "pii_dict"}, "jwt"]}},
                ),
            ),
            create_snapshot_function_probe(
                probe_id="function-probe",
                module="tests.submod.stuff",
                func_qname="sensitive_stuff",
                evaluate_at=ProbeEvalTiming.EXIT,
            ),
        )

        stuff.sensitive_stuff("top secret")

        msg_line, msg_func = d.uploader.wait_for_payloads(2)

        # Only the token value be visible now because it's explicitly excluded from redaction
        assert (
            msg_line["message"] == f"token='deadbeef' answer=42 "
            f"pii_dict={{'jwt': '{REDACTED}', 'password': '{REDACTED}', 'username': 'admin'}} "
            f"pii_dict['jwt']={REDACTED}"
        )


def test_debugger_exception_conditional_function_probe():
    """
    Test that we can have a condition on the exception on a function probe when
    the condition is evaluated on exit.
    """
    from tests.submod import stuff

    snapshots = simple_debugger_test(
        create_snapshot_function_probe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="throwexcstuff",
            evaluate_at=ProbeEvalTiming.EXIT,
            condition=DDExpression(
                dsl="expr.__class__.__name__ == 'Exception'",
                callable=dd_compile(
                    {
                        "and": [
                            {"isDefined": "@exception"},
                            {
                                "eq": [
                                    {"getmember": [{"getmember": [{"ref": "@exception"}, "__class__"]}, "__name__"]},
                                    "Exception",
                                ]
                            },
                        ]
                    }
                ),
            ),
        ),
        lambda: stuff.throwexcstuff(),
    )

    (snapshot,) = snapshots
    snapshot_data = snapshot["debugger"]["snapshot"]
    return_capture = snapshot_data["captures"]["return"]
    assert return_capture["throwable"]["message"] == "'Hello', 'world!', 42"
    assert return_capture["throwable"]["type"] == "Exception"
