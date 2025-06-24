import mock
import opentelemetry
import opentelemetry.version
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.flask.test_flask_snapshot import flask_client  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_default_env  # noqa:F401


OTEL_VERSION = parse_version(opentelemetry.version.__version__)


def test_otel_compatible_meter_is_returned_by_meter_provider():
    ddtrace_meterprovider = opentelemetry.metrics.get_meter_provider()
    otel_compatible_meter = ddtrace_meterprovider.get_meter("some_meter")
    assert isinstance(otel_compatible_meter, opentelemetry.metrics.Meter)


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
def test_meter_with_flask_app(flask_client, oteltracer):  # noqa:F811
    resp = flask_client.get("/otel", headers=headers)

    assert resp.text == "otel"
    assert resp.status_code == 200
