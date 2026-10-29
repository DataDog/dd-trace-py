"""OTLP snapshots of Flask server spans with Datadog and OpenTelemetry semantics."""

import functools

import pytest

from ddtrace.contrib.internal.flask.patch import flask_version
from tests.contrib.flask.test_flask_snapshot import flask_client  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_command  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_default_env
from tests.contrib.flask.test_flask_snapshot import flask_port  # noqa:F401
from tests.contrib.flask.test_flask_snapshot import flask_wsgi_application  # noqa:F401
from tests.webclient import Client


# The Flask version only changes the child spans; test_flask_snapshot covers older versions.
pytestmark = pytest.mark.skipif(flask_version < (2, 2, 0), reason="Snapshots are recorded with Flask 2.2+")

# A fixed user agent keeps the snapshots independent of the installed requests version.
_HEADERS = {"User-Agent": "test-agent"}


# Settings inherited from the parent environment would change the snapshots.
_UNSET = (
    "DD_ENV",
    "DD_VERSION",
    "DD_TRACE_CLIENT_IP_ENABLED",
    "DD_TRACE_HTTP_SERVER_ERROR_STATUSES",
)


def _env(wsgi_application: str, otel_semantics: str, **settings: str) -> dict[str, str]:
    env = flask_default_env(wsgi_application)
    for name in _UNSET:
        env.pop(name, None)
    env.update(
        {
            "DD_SERVICE": "otel-http-server-snapshots",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "DD_TRACE_OTEL_SEMANTICS_ENABLED": otel_semantics,
            **settings,
        }
    )
    return env


def _semantics(**settings: str):
    modes = ("false", "true")
    return pytest.mark.parametrize(
        "flask_env_arg", [functools.partial(_env, otel_semantics=mode, **settings) for mode in modes], ids=modes
    )


# The Flask version differs across test environments and stack traces carry line numbers.
_SNAPSHOT = pytest.mark.snapshot(
    ignores=["meta.tracestate", "meta.telemetry.sdk.version", "meta.flask.version", "meta.error.stack"],
)


# The cases are split across two tests to keep each snapshot file within the repository's file size limit.
@_SNAPSHOT
@_semantics(DD_TRACE_CLIENT_IP_ENABLED="true")
def test_server_span_attributes(flask_client: Client) -> None:  # noqa:F811
    # Route, obfuscated query, user agent and both client addresses.
    flask_client.get(
        "/checkuser/42?token=secret&page=2",
        headers={**_HEADERS, "X-Forwarded-For": "203.0.113.10"},
    )
    # With OTel semantics a span without a route is named after the method only, never the URL path.
    flask_client.get("/no/such/path", headers=_HEADERS)
    # With OTel semantics an unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    flask_client.request("PROPFIND", "/items", headers=_HEADERS)


@_SNAPSHOT
@_semantics()
def test_server_span_errors(flask_client: Client) -> None:  # noqa:F811
    # Server spans are errors from 500; with OTel semantics error.type is the status code.
    flask_client.get("/redirect", headers=_HEADERS, allow_redirects=False)
    flask_client.get("/missing", headers=_HEADERS)
    flask_client.get("/broken", headers=_HEADERS)
    # An unhandled exception answers 500: the server span reports the status code and the view spans the exception type.
    flask_client.get("/boom", headers=_HEADERS)
