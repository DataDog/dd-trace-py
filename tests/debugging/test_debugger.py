from collections import Counter
import sys
from threading import Thread
from time import sleep

import mock
from mock.mock import call
import pytest

from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import MetricProbe
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.registry import _get_probe_location
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.utils.inspection import linenos
from tests.debugging.mocking import debugger
from tests.submod.stuff import Stuff
from tests.submod.stuff import modulestuff as imported_modulestuff


def good_probe():
    # DEV: We build this on demand to ensure that rate limiting gets reset.
    return LineProbe(
        probe_id="probe-instance-method",
        source_file="tests/submod/stuff.py",
        line=36,
        condition=None,
    )


def simple_debugger_test(probe, func):
    with debugger() as d:
        probe_id = probe.probe_id

        d.add_probes(probe)
        sleep(0.5)
        try:
            func()
        except Exception:
            pass
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
        LineProbe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=None,
        ),
        lambda: getattr(Stuff(), "instancestuff")(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in ("instancestuff(self=Stuff(), bar=None)", "instancestuff(bar=None, self=Stuff())")
    captures = snapshot["debugger.snapshot"]["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}
    assert captures["fields"] == {}
    assert snapshot["debugger.snapshot"]["duration"] is None


def test_debugger_line_probe_on_imported_module_function():

    lineno = min(linenos(imported_modulestuff))
    snapshots = simple_debugger_test(
        LineProbe(
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
    assert captures["fields"] == {}


@pytest.mark.parametrize(
    "probe, trigger",
    [
        (
            FunctionProbe(
                probe_id="probe-instance-method",
                module="tests.submod.stuff",
                func_qname="Stuff.instancestuff",
                rate=1000,
            ),
            lambda: getattr(Stuff(), "instancestuff")(42),
        ),
        (
            LineProbe(
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


@pytest.mark.parametrize(
    "probe, trigger",
    [
        (
            FunctionProbe(
                probe_id="probe-instance-method",
                module="tests.submod.stuff",
                func_qname="Stuff.instancestuff",
                rate=1000.0,
            ),
            lambda: Stuff().instancestuff(42),
        ),
        (
            LineProbe(
                probe_id="probe-instance-method",
                source_file="tests/submod/stuff.py",
                line=36,
                rate=1000.0,
            ),
            lambda: Stuff().instancestuff(42),
        ),
    ],
)
def test_debugger_probe_active_inactive(probe, trigger):
    global Stuff

    with debugger() as d:
        probe_id = probe.probe_id

        d.add_probes(probe)
        sleep(0.5)

        assert probe in d._probe_registry
        assert all(e["debugger"]["diagnostics"] for _ in d.uploader.payloads for e in _)
        d.uploader.queue[:] = []
        trigger()

        probe.active = False

        sleep(0.5)

        # Test that the probe was ejected
        assert probe in d._probe_registry

        assert d.uploader.queue

        trigger()

        assert d.uploader.queue
        (payload,) = d.uploader.payloads
        assert payload

        (snapshot,) = payload
        assert snapshot
        assert snapshot["debugger.snapshot"]["probe"]["id"] == probe_id


def test_debugger_function_probe_on_instance_method():
    snapshots = simple_debugger_test(
        FunctionProbe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="Stuff.instancestuff",
            condition=None,
        ),
        lambda: Stuff().instancestuff(42),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in (
        "Stuff.instancestuff(self=Stuff(), bar=42)",
        "Stuff.instancestuff(bar=42, self=Stuff())",
    )

    entry_capture = snapshot["debugger.snapshot"]["captures"]["entry"]
    assert set(entry_capture["arguments"].keys()) == {"self", "bar"}
    assert entry_capture["locals"] == {}
    assert entry_capture["fields"] == {}
    assert entry_capture["throwable"] is None

    return_capture = snapshot["debugger.snapshot"]["captures"]["return"]
    assert set(return_capture["arguments"].keys()) == {"self", "bar"}
    assert set(return_capture["locals"].keys()) == {"@return"}
    assert return_capture["fields"] == {}
    assert return_capture["throwable"] is None


def test_debugger_function_probe_on_function_with_exception():
    from tests.submod import stuff

    snapshots = simple_debugger_test(
        FunctionProbe(
            probe_id="probe-instance-method",
            module="tests.submod.stuff",
            func_qname="throwexcstuff",
            condition=None,
        ),
        lambda: stuff.throwexcstuff(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] == "throwexcstuff()"

    entry_capture = snapshot["debugger.snapshot"]["captures"]["entry"]
    assert entry_capture["arguments"] == {}
    assert entry_capture["locals"] == {}
    assert entry_capture["fields"] == {}
    assert entry_capture["throwable"] is None

    return_capture = snapshot["debugger.snapshot"]["captures"]["return"]
    assert return_capture["arguments"] == {}
    assert return_capture["locals"] == {}
    assert return_capture["fields"] == {}
    assert return_capture["throwable"]["message"] == "'Hello', 'world!', 42"
    assert return_capture["throwable"]["type"] == "Exception"


def test_debugger_invalid_condition():
    with debugger() as d:
        d.add_probes(
            LineProbe(
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
        LineProbe(
            probe_id="probe-instance-method",
            source_file="tests/submod/stuff.py",
            line=36,
            condition=dd_compile(True),
        ),
        lambda: getattr(Stuff(), "instancestuff")(),
    )

    (snapshot,) = snapshots
    assert snapshot["message"] in ("instancestuff(self=Stuff(), bar=None)", "instancestuff(bar=None, self=Stuff())")
    captures = snapshot["debugger.snapshot"]["captures"]["lines"]["36"]
    assert set(captures["arguments"].keys()) == {"self", "bar"}
    assert captures["locals"] == {}
    assert captures["fields"] == {}


def test_debugger_invalid_line():
    with debugger() as d:
        d.add_probes(
            LineProbe(
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
            LineProbe(
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
        LineProbe(
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
            LineProbe(
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
            LineProbe(
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
        LineProbe(
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
            LineProbe(probe_id="thread-test", source_file="tests/submod/stuff.py", line=40),
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


def test_debugger_metric_probe(mock_metrics):
    with debugger() as d:
        d.add_probes(
            MetricProbe(
                probe_id="metric-probe-test",
                source_file="tests/submod/stuff.py",
                line=36,
                kind=MetricProbeKind.COUNTER,
                name="test.counter",
                tags={"foo": "bar"},
            ),
        )
        sleep(0.5)

        Stuff().instancestuff()

        assert call("probe.test.counter", 1.0, ["foo:bar"]) in mock_metrics.increment.mock_calls


def test_debugger_multiple_function_probes_on_same_function():
    global Stuff

    probes = [
        FunctionProbe(
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
        FunctionProbe(
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

        (snapshot,) = d.test_queue
        assert snapshot.probe.probe_id == "probe-on-wrapped-function"


def test_debugger_wrapped_function_on_function_probe(stuff):
    probes = [
        FunctionProbe(
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

        (snapshot,) = d.test_queue
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
            LineProbe(
                probe_id="line-probe-wrapped-method",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=None,
            )
        )
        sleep(0.5)

        stuff.Stuff().instancestuff(42)

        (snapshot,) = d.test_queue
        assert snapshot.probe.probe_id == "line-probe-wrapped-method"


def test_probe_status_logging(monkeypatch):
    monkeypatch.setenv("DD_REMOTECONFIG_POLL_SECONDS", "0.1")
    RemoteConfig.disable()

    from ddtrace.internal.remoteconfig.client import RemoteConfigClient

    old_request = RemoteConfigClient.request

    def request(self, *args, **kwargs):
        for cb in self._products.values():
            cb(None, None)

    RemoteConfigClient.request = request

    try:
        with debugger(diagnostics_interval=0.5) as d:
            d.add_probes(
                LineProbe(
                    probe_id="line-probe-ok",
                    source_file="tests/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
                FunctionProbe(
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


@pytest.mark.parametrize("duration", [1e5, 1e6, 1e7])
def test_debugger_function_probe_duration(duration):
    from tests.submod.stuff import durationstuff

    with debugger(poll_interval=0.1) as d:
        d.add_probes(
            FunctionProbe(
                probe_id="duration-probe",
                module="tests.submod.stuff",
                func_qname="durationstuff",
            )
        )

        durationstuff(duration)

        (snapshot,) = d.test_queue
        assert 0.9 * duration <= snapshot.duration <= 2.5 * duration, snapshot


def test_debugger_condition_eval_then_rate_limit():
    from tests.submod.stuff import Stuff

    with debugger(upload_flush_interval=0.1) as d:
        d.add_probes(
            LineProbe(
                probe_id="foo",
                source_file="tests/submod/stuff.py",
                line=36,
                condition=dd_compile({"eq": ["#bar", 42]}),
            ),
        )

        # If the condition is evaluated before the rate limit, all the calls
        # before 42 won't use any of the probe quota. However, all the calls
        # after 42 won't be snapshotted because of the rate limiter.
        for i in range(100):
            Stuff().instancestuff(i)

        sleep(0.5)

        # We expect to see just the snapshot generated by the 42 call.
        (snapshots,) = d.uploader.payloads
        (snapshot,) = snapshots
        assert "42" == snapshot["debugger.snapshot"]["captures"]["lines"]["36"]["arguments"]["bar"]["value"], snapshot


def test_debugger_function_probe_eval_on_exit():
    from tests.submod.stuff import mutator

    with debugger() as d:
        d.add_probes(
            FunctionProbe(
                probe_id="duration-probe",
                module="tests.submod.stuff",
                func_qname="mutator",
                condition=dd_compile({"contains": ["#arg", 42]}),
            )
        )

        mutator(arg=[])

        (snapshot,) = d.test_queue
        assert snapshot, d.test_queue
