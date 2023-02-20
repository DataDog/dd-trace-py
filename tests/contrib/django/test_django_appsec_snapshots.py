from contextlib import contextmanager
import os
import subprocess

import django
import pytest

from ddtrace.internal.compat import PY3
import ddtrace.internal.constants as constants
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import _ALLOWED_IP
from tests.appsec.test_processor import _BLOCKED_IP
from tests.utils import snapshot
from tests.webclient import Client


SERVER_PORT = 8000


@contextmanager
def daphne_client(django_asgi, additional_env=None):
    """Runs a django app hosted with a daphne webserver in a subprocess and
    returns a client which can be used to query it.

    Traces are flushed by invoking a tracer.shutdown() using a /shutdown-tracer route
    at the end of the testcase.
    """

    # Make sure to copy the environment as we need the PYTHONPATH and _DD_TRACE_WRITER_ADDITIONAL_HEADERS (for the test
    # token) propagated to the new process.
    env = os.environ.copy()
    env.update(additional_env or {})
    assert "_DD_TRACE_WRITER_ADDITIONAL_HEADERS" in env, "Client fixture needs test token in headers"
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings",
        }
    )

    # ddtrace-run uses execl which replaces the process but the webserver process itself might spawn new processes.
    # Right now it doesn't but it's possible that it might in the future (ex. uwsgi).
    cmd = ["ddtrace-run", "daphne", "-p", str(SERVER_PORT), "tests.contrib.django.asgi:%s" % django_asgi]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )

    client = Client("http://localhost:%d" % SERVER_PORT)

    # Wait for the server to start up
    client.wait()

    try:
        yield client
    finally:
        resp = client.get_ignored("/shutdown-tracer")
        assert resp.status_code == 200
        proc.terminate()


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "meta.error.stack",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ]
)
def test_appsec_enabled():
    with daphne_client("application", additional_env={"DD_APPSEC_ENABLED": "true"}) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "meta.error.stack",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ]
)
def test_appsec_enabled_attack():
    with daphne_client("application", additional_env={"DD_APPSEC_ENABLED": "true"}) as client:
        resp = client.get("/.git")
        assert resp.status_code == 404


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_nomatch_200():
    with daphne_client(
        "application",
        additional_env={
            "DD_DJANGO_INSTRUMENT_TEMPLATES": "false",
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get("/", headers={"Via": _ALLOWED_IP})
        assert result.status_code == 200
        assert result.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_match_403():
    with daphne_client(
        "application",
        additional_env={
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get(
            "/",
            headers={
                "Via": _BLOCKED_IP,
                "Accept": "text/html",
            },
        )
        assert result.status_code == 403
        as_bytes = (
            bytes(constants.APPSEC_BLOCKED_RESPONSE_HTML, "utf-8") if PY3 else constants.APPSEC_BLOCKED_RESPONSE_HTML
        )
        assert result.content == as_bytes


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_match_403_json():
    with daphne_client(
        "application",
        additional_env={
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get(
            "/",
            headers={
                "Via": _BLOCKED_IP,
            },
        )
        assert result.status_code == 403
        as_bytes = (
            bytes(constants.APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.APPSEC_BLOCKED_RESPONSE_JSONP
        )
        assert result.content == as_bytes
