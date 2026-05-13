import mock
import opentelemetry
from opentelemetry.trace import set_span_in_context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import pytest

from ddtrace.internal.opentelemetry.trace import OTEL_VERSION
from ddtrace.opentelemetry import SpanProcessor
from ddtrace.opentelemetry import SynchronousMultiSpanProcessor
from ddtrace.opentelemetry import TracerProvider
from tests.contrib.flask.test_flask_snapshot import flask_client  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_default_env  # noqa:F401


def test_otel_compatible_tracer_is_returned_by_tracer_provider():
    ddtrace_traceprovider = opentelemetry.trace.get_tracer_provider()
    otel_compatible_tracer = ddtrace_traceprovider.get_tracer("some_tracer")
    assert isinstance(otel_compatible_tracer, opentelemetry.trace.Tracer)


@pytest.mark.snapshot(wait_for_num_traces=2, ignores=["meta.error.stack"])
def test_otel_start_span_record_exception(oteltracer):
    # Avoid mocking time_ns when Span is created. This is a workaround to resolve a rate limit bug.
    raised_span = oteltracer.start_span("test-raised-exception")
    with pytest.raises(Exception, match="Sorry Otel Span, I failed you"):
        # Mock _add_event (Python-level) to inject a static timestamp when none is provided.
        # wall_clock_ns() is in Rust and cannot be reached via mock.patch on Time.time_ns.
        from ddtrace._trace.span import Span as DDSpan

        _original_add_event = DDSpan._add_event

        def _add_event_with_fixed_ts(self, name, attributes=None, time_unix_nano=None):
            if time_unix_nano is None:
                time_unix_nano = 1716560261227739000
            return _original_add_event(self, name, attributes, time_unix_nano)

        with mock.patch.object(DDSpan, "_add_event", _add_event_with_fixed_ts):
            with raised_span:
                raised_span.record_exception(ValueError("Invalid Operation 1"))
                raise Exception("Sorry Otel Span, I failed you")

    with oteltracer.start_span("test-recorded-exception") as not_raised_span:
        not_raised_span.record_exception(
            IndexError("Invalid Operation 2"), {"exception.stuff": "thing 2"}, 1716560281337739
        )
        not_raised_span.record_exception(
            Exception("Real Exception"),
            {
                "exception.type": "RandoException",
                "exception.message": "MoonEar Fire!!!",
                "exception.stacktrace": "Fake traceback",
                "exception.details": "This is FAKE, I overwrote the real exception details",
            },
            1716560271237812,
        )


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_start_span_without_default_args(oteltracer):
    root = oteltracer.start_span("root-span")
    otel_span = oteltracer.start_span(
        "test-start-span",
        context=set_span_in_context(root),
        kind=opentelemetry.trace.SpanKind.CLIENT,
        attributes={"start_span_tag": "start_span_val"},
        links=None,
        start_time=0,
        record_exception=False,
        set_status_on_exception=False,
    )
    with pytest.raises(Exception, match="Sorry Otel Span, I failed you"):
        with otel_span:
            otel_span.update_name("rename-start-span")
            raise Exception("Sorry Otel Span, I failed you")

    # set_status_on_exception is False
    assert otel_span._ddspan.error == 0
    assert otel_span.is_recording() is False
    assert root.is_recording()
    otel_span.end()
    root.end()


def test_otel_start_span_with_span_links(oteltracer):
    # create a span and generate an otel link object
    span1 = oteltracer.start_span("span-1")
    span2 = oteltracer.start_span("span-2")

    try:
        span1_context = span1.get_span_context()
        attributes1 = {"attr1": 1, "link.name": "moon"}
        link_from_span_1 = opentelemetry.trace.Link(span1_context, attributes1)
        span2_context = span2.get_span_context()
        attributes2 = {"attr2": 2, "link.name": "tree"}
        link_from_span_2 = opentelemetry.trace.Link(span2_context, attributes2)

        # create an otel span that links to span1 and span2
        with oteltracer.start_as_current_span("span-3", links=[link_from_span_1, link_from_span_2]) as span3:
            pass

        # assert that span3 has the expected links
        # DEV: link attributes are stored as strings (pre-encoding to wire format)
        ddspan3 = span3._ddspan
        expected_attrs = [{"attr1": "1", "link.name": "moon"}, {"attr2": "2", "link.name": "tree"}]
        for span_context, expected in zip((span1_context, span2_context), expected_attrs):
            [link, *others] = [link for link in ddspan3._get_links() if link.span_id == span_context.span_id]
            assert not others
            assert link.trace_id == span_context.trace_id
            assert link.span_id == span_context.span_id
            assert link.tracestate == span_context.trace_state.to_header()
            assert link.flags == span_context.trace_flags
            assert link.attributes == expected
    finally:
        span1.end()
        span2.end()


@pytest.mark.snapshot(wait_for_num_traces=1, ignores=["meta.error.stack"])
def test_otel_start_span_ignore_exceptions(caplog, oteltracer):
    with pytest.raises(Exception, match="Sorry Otel Span, I failed you"):
        with oteltracer.start_span("otel-error-span", record_exception=False, set_status_on_exception=False):
            raise Exception("Sorry Otel Span, I failed you")


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_start_current_span_with_default_args(oteltracer):
    with oteltracer.start_as_current_span("test-start-current-span-defaults") as otel_span:
        assert otel_span.is_recording()
        otel_span.update_name("rename-start-current-span")


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_start_current_span_without_default_args(oteltracer):
    with oteltracer.start_as_current_span("root-span") as root:
        with oteltracer.start_as_current_span(
            "test-start-current-span-no-defualts",
            context=set_span_in_context(root),
            kind=opentelemetry.trace.SpanKind.SERVER,
            attributes={"start_current_span_tag": "start_cspan_val"},
            links=[],
            start_time=0,
            record_exception=False,
            set_status_on_exception=False,
            end_on_exit=False,
        ) as otel_span:
            assert otel_span.is_recording()
            otel_span.update_name("rename-start-current-span")
            with pytest.raises(Exception, match="Exception message and stacktrace should not be set"):
                raise Exception("Exception message and stacktrace should not be set")

    # set_status_on_exception is False
    assert otel_span._ddspan.error == 0
    # Since end_on_exit=False start_as_current_span should not call Span.end()
    assert otel_span.is_recording()
    otel_span.end()


def test_otel_get_span_context_sets_sampling_decision(oteltracer):
    with oteltracer.start_span("otel-server") as otelspan:
        # Sampling priority is not set on span creation
        assert otelspan._ddspan.context.sampling_priority is None
        # Ensure the sampling priority is always consistent with traceflags
        span_context = otelspan.get_span_context()
        # Sampling priority is evaluated when the SpanContext is first accessed
        sp = otelspan._ddspan.context.sampling_priority
        assert sp is not None
        if sp > 0:
            assert span_context.trace_flags == 1
        else:
            assert span_context.trace_flags == 0
        # Ensure the sampling priority is always consistent
        for _ in range(1000):
            otelspan.get_span_context()
            assert otelspan._ddspan.context.sampling_priority == sp


def test_distributed_trace_inject(oteltracer):  # noqa:F811
    with oteltracer.start_as_current_span("test-otel-distributed-trace") as span:
        headers = {}
        TraceContextTextMapPropagator().inject(headers, set_span_in_context(span))
        sp = span.get_span_context()
        assert headers["traceparent"] == f"00-{sp.trace_id:032x}-{sp.span_id:016x}-{sp.trace_flags:02x}"
        assert headers["tracestate"] == sp.trace_state.to_header()


def test_distributed_trace_extract(oteltracer):  # noqa:F811
    headers = {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "tracestate": "congo=t61rcWkgMzE,dd=s:2",
    }
    context = TraceContextTextMapPropagator().extract(headers)
    with oteltracer.start_as_current_span("test-otel-distributed-trace", context=context) as span:
        sp = span.get_span_context()
        assert sp.trace_id == int("0af7651916cd43dd8448eb211c80319c", 16)
        assert span._ddspan.parent_id == int("b7ad6b7169203331", 16)
        assert sp.trace_flags == 1
        assert sp.trace_state.get("congo") == "t61rcWkgMzE"
        assert "s:2" in sp.trace_state.get("dd")
        assert sp.is_remote is False


def otel_flask_app_env(flask_wsgi_application):
    env = flask_default_env(flask_wsgi_application)
    env.update({"DD_TRACE_OTEL_ENABLED": "true"})
    return env


@pytest.mark.parametrize(
    "flask_wsgi_application,flask_env_arg,flask_port,flask_command",
    [
        (
            "tests.opentelemetry.flask_app:app",
            otel_flask_app_env,
            "8010",
            ["ddtrace-run", "flask", "run", "-h", "0.0.0.0", "-p", "8010"],
        ),
        pytest.param(
            "tests.opentelemetry.flask_app:app",
            otel_flask_app_env,
            "8011",
            ["opentelemetry-instrument", "flask", "run", "-h", "0.0.0.0", "-p", "8011"],
            marks=pytest.mark.skipif(
                OTEL_VERSION < (1, 16),
                reason="otel flask instrumentation is in beta and is unstable with earlier versions of the api",
            ),
        ),
    ],
    ids=[
        "with_ddtrace_run",
        "with_opentelemetry_instrument",
    ],
)
@pytest.mark.snapshot(
    wait_for_num_traces=1,
    ignores=["metrics.net.peer.port", "meta.traceparent", "meta.tracestate", "meta.flask.version"],
)
def test_distributed_trace_with_flask_app(flask_client, oteltracer):  # noqa:F811
    with oteltracer.start_as_current_span("test-otel-distributed-trace") as span:
        headers = {}
        TraceContextTextMapPropagator().inject(headers, set_span_in_context(span))
        resp = flask_client.get("/otel", headers=headers)

    assert resp.text == "otel"
    assert resp.status_code == 200


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_start_as_current_span_decorator(oteltracer):
    """Test that the tracer.start_as_current_span decorator works as expected"""

    @oteltracer.start_as_current_span("required-resource-name")
    def tracer_wrap_with_resource_name():
        pass

    @oteltracer.start_as_current_span("with-operation-name", attributes={"operation.name": "my-operation"})
    def tracer_wrap_with_operation_name():
        pass

    @oteltracer.start_as_current_span("with-service-name", attributes={"service.name": "my-service"})
    def tracer_wrap_with_service():
        pass

    @oteltracer.start_as_current_span("with-manual-keep", attributes={"manual.keep": None})
    def tracer_wrap_dd_keep_sampling():
        pass

    @oteltracer.start_as_current_span("with-child-spans")
    def tracer_wrap_outer_no_args():
        tracer_wrap_with_resource_name()
        tracer_wrap_with_operation_name()
        tracer_wrap_with_service()
        tracer_wrap_dd_keep_sampling()

    tracer_wrap_outer_no_args()


# ---------------------------------------------------------------------------
# SpanProcessor tests
# ---------------------------------------------------------------------------


class _RecordingProcessor(SpanProcessor):
    """Test processor that records every call made to it."""

    def __init__(self):
        self.started = []
        self.ended = []
        self.shutdown_called = False
        self.force_flush_called_with = []
        self._force_flush_return = True

    def on_start(self, span, parent_context=None):
        self.started.append((span, parent_context))

    def on_end(self, span):
        self.ended.append(span)

    def shutdown(self):
        self.shutdown_called = True

    def force_flush(self, timeout_millis=30000):
        self.force_flush_called_with.append(timeout_millis)
        return self._force_flush_return


def _make_provider_with_processor(processor):
    """Return a fresh TracerProvider with *processor* registered."""
    provider = TracerProvider()
    provider.add_span_processor(processor)
    return provider


def test_add_span_processor_on_start_called():
    processor = _RecordingProcessor()
    provider = _make_provider_with_processor(processor)
    tracer = provider.get_tracer(__name__)

    span = tracer.start_span("test-on-start")
    assert len(processor.started) == 1
    assert processor.started[0][0] is span
    span.end()


def test_add_span_processor_on_end_called():
    processor = _RecordingProcessor()
    provider = _make_provider_with_processor(processor)
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("test-on-end") as span:
        assert len(processor.ended) == 0

    assert len(processor.ended) == 1
    assert processor.ended[0] is span


def test_add_span_processor_on_start_not_recording_after_end():
    """Span passed to on_end should no longer be recording."""
    processor = _RecordingProcessor()
    provider = _make_provider_with_processor(processor)
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("test-ended-span"):
        pass

    ended_span = processor.ended[0]
    assert not ended_span.is_recording()


def test_add_span_processor_on_start_receives_parent_context():
    processor = _RecordingProcessor()
    provider = _make_provider_with_processor(processor)
    tracer = provider.get_tracer(__name__)

    from opentelemetry.trace import set_span_in_context

    with tracer.start_as_current_span("parent") as parent:
        parent_ctx = set_span_in_context(parent)
        child = tracer.start_span("child", context=parent_ctx)
        child.end()

    assert len(processor.started) == 2
    _parent_span, _parent_ctx = processor.started[0]
    _child_span, child_ctx = processor.started[1]
    assert child_ctx is parent_ctx


def test_add_span_processor_multiple_processors_called_in_order():
    p1 = _RecordingProcessor()
    p2 = _RecordingProcessor()
    provider = TracerProvider()
    provider.add_span_processor(p1)
    provider.add_span_processor(p2)
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("multi-processor"):
        pass

    assert len(p1.started) == 1
    assert len(p2.started) == 1
    assert len(p1.ended) == 1
    assert len(p2.ended) == 1
    # Both processors see the same span object
    assert p1.started[0][0] is p2.started[0][0]
    assert p1.ended[0] is p2.ended[0]


def test_add_span_processor_no_processor_no_error():
    """TracerProvider with no processors should work without errors."""
    provider = TracerProvider()
    tracer = provider.get_tracer(__name__)
    with tracer.start_as_current_span("no-processor"):
        pass


def test_force_flush_calls_all_processors():
    p1 = _RecordingProcessor()
    p2 = _RecordingProcessor()
    provider = TracerProvider()
    provider.add_span_processor(p1)
    provider.add_span_processor(p2)

    result = provider.force_flush(timeout_millis=5000)

    assert result is True
    assert len(p1.force_flush_called_with) == 1
    assert len(p2.force_flush_called_with) == 1
    # Each processor receives a non-negative timeout
    assert p1.force_flush_called_with[0] >= 0
    assert p2.force_flush_called_with[0] >= 0


def test_force_flush_returns_false_when_processor_fails():
    p1 = _RecordingProcessor()
    p1._force_flush_return = False
    p2 = _RecordingProcessor()
    provider = TracerProvider()
    provider.add_span_processor(p1)
    provider.add_span_processor(p2)

    result = provider.force_flush(timeout_millis=5000)

    assert result is False
    # p2 is not called after p1 returns False
    assert len(p2.force_flush_called_with) == 0


def test_force_flush_no_processors_returns_true():
    provider = TracerProvider()
    assert provider.force_flush(timeout_millis=5000) is True


def test_synchronous_multi_span_processor_add_and_call():
    multi = SynchronousMultiSpanProcessor()
    p1 = _RecordingProcessor()
    p2 = _RecordingProcessor()
    multi.add_span_processor(p1)
    multi.add_span_processor(p2)

    provider = TracerProvider()
    provider._active_span_processor = multi
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("multi-direct"):
        pass

    assert len(p1.started) == 1
    assert len(p2.started) == 1
    assert len(p1.ended) == 1
    assert len(p2.ended) == 1


def test_span_processor_base_class_defaults():
    """SpanProcessor base class methods should all be no-ops / return True."""

    class _MinimalProcessor(SpanProcessor):
        pass

    proc = _MinimalProcessor()
    proc.on_start(None)
    proc.on_end(None)
    proc.shutdown()
    assert proc.force_flush() is True
    assert proc.force_flush(timeout_millis=100) is True
