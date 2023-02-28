import opentelemetry
import opentelemetry.version
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.flask.test_flask_snapshot import flask_client  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_default_env  # noqa:F401


OTEL_VERSION = parse_version(opentelemetry.version.__version__)


def test_otel_compatible_tracer_is_returned_by_tracer_provider():
    ddtrace_traceprovider = opentelemetry.trace.get_tracer_provider()
    otel_compatible_tracer = ddtrace_traceprovider.get_tracer("some_tracer")
    assert isinstance(otel_compatible_tracer, opentelemetry.trace.Tracer)


@pytest.mark.snapshot
def test_otel_start_span_with_default_args(oteltracer):
    otel_span = oteltracer.start_span("test-start-span")
    with pytest.raises(Exception):
        with opentelemetry.trace.use_span(
            otel_span,
            end_on_exit=False,
            record_exception=False,
            set_status_on_exception=False,
        ):
            otel_span.update_name("rename-start-span")
            raise Exception("Sorry Otel Span, I failed you")

    # set_status_on_exception is False
    assert otel_span._ddspan.error == 0
    # Since end_on_exit=False start_as_current_span should not call Span.end()
    assert otel_span.is_recording()
    otel_span.end()


@pytest.mark.snapshot
def test_otel_start_span_without_default_args(oteltracer):
    root = oteltracer.start_span("root-span")
    otel_span = oteltracer.start_span(
        "test-start-span",
        context=opentelemetry.trace.set_span_in_context(root),
        kind=opentelemetry.trace.SpanKind.CLIENT,
        attributes={"start_span_tag": "start_span_val"},
        links=None,
        start_time=0,
        record_exception=True,
        set_status_on_exception=True,
    )
    otel_span.update_name("rename-start-span")
    with pytest.raises(Exception):
        with opentelemetry.trace.use_span(
            otel_span,
            end_on_exit=False,
            record_exception=False,
            set_status_on_exception=False,
        ):
            raise Exception("Sorry Otel Span, I failed you")

    # set_status_on_exception is False
    assert otel_span._ddspan.error == 0
    assert otel_span.is_recording()
    assert root.is_recording()
    otel_span.end()
    root.end()


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_otel_start_span_ignore_exceptions(caplog, oteltracer):
    with pytest.raises(Exception):
        with oteltracer.start_span("otel-error-span", record_exception=False, set_status_on_exception=False):
            raise Exception("Sorry Friend, I failed you")


@pytest.mark.snapshot
def test_otel_start_current_span_with_default_args(oteltracer):
    with oteltracer.start_as_current_span("test-start-current-span-defaults") as otel_span:
        assert otel_span.is_recording()
        otel_span.update_name("rename-start-current-span")


@pytest.mark.snapshot
def test_otel_start_current_span_without_default_args(oteltracer):
    with oteltracer.start_as_current_span("root-span") as root:
        with oteltracer.start_as_current_span(
            "test-start-current-span-no-defualts",
            context=opentelemetry.trace.set_span_in_context(root),
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
            with pytest.raises(Exception):
                raise Exception("Exception message and stacktrace should not be set")

    # set_status_on_exception is False
    assert otel_span._ddspan.error == 0
    # Since end_on_exit=False start_as_current_span should not call Span.end()
    assert otel_span.is_recording()
    otel_span.end()


@pytest.mark.parametrize(
    "flask_wsgi_application,flask_env_arg,flask_port,flask_command",
    [
        (
            "tests.opentelemetry.flask_app:app",
            flask_default_env,
            "8000",
            ["ddtrace-run", "flask", "run", "-h", "0.0.0.0", "-p", "8000"],
        ),
        pytest.param(
            "tests.opentelemetry.flask_app:app",
            flask_default_env,
            "8001",
            ["opentelemetry-instrument", "flask", "run", "-h", "0.0.0.0", "-p", "8001"],
            marks=pytest.mark.skipif(
                OTEL_VERSION < (1, 12),
                reason="otel flask instrumentation is in beta and is unstable with earlier versions of the api",
            ),
        ),
    ],
    ids=[
        "with_ddtrace_run",
        "with_opentelemetry_instrument",
    ],
)
@pytest.mark.snapshot(ignores=["metrics.net.peer.port", "meta.traceparent", "meta.flask.version"])
def test_distributed_trace_with_flask_app(flask_client, oteltracer):  # noqa:F811
    with oteltracer.start_as_current_span("test-otel-distributed-trace") as otel_span:
        headers = {
            "traceparent": otel_span._ddspan.context._traceparent,
            "tracestate": otel_span._ddspan.context._tracestate,
        }
        resp = flask_client.get("/otel", headers=headers)

    assert resp.text == "otel"
    assert resp.status_code == 200
