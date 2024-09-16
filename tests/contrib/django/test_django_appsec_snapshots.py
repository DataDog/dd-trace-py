from contextlib import contextmanager
import os
import subprocess

import django
import pytest

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import FINGERPRINTING
import ddtrace.internal.constants as constants
import tests.appsec.rules as rules
from tests.utils import snapshot
from tests.webclient import Client


SERVER_PORT = 8000
APPSEC_JSON_TAG = f"meta.{APPSEC.JSON}"


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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        "error",
        "type",
        "meta.error.stack",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.rasp.duration",
        "metrics._dd.appsec.rasp.duration_ext",
        "metrics._dd.appsec.rasp.rule.eval",
        APPSEC_JSON_TAG,
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
        "error",
        "type",
        "meta.error.stack",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.response.headers.content-type",  # depends of the Django version
        "meta.http.useragent",
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.rasp.duration",
        "metrics._dd.appsec.rasp.duration_ext",
        "metrics._dd.appsec.rasp.rule.eval",
        APPSEC_JSON_TAG,
        "meta." + FINGERPRINTING.NETWORK,
        "meta." + FINGERPRINTING.HEADER,
        "meta." + FINGERPRINTING.ENDPOINT,
    ]
)
def test_appsec_enabled_attack():
    with daphne_client("application", additional_env={"DD_APPSEC_ENABLED": "true"}) as client:
        resp = client.get("/.git")
        assert resp.status_code == 404


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "error",
        "type",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.rasp.duration",
        "metrics._dd.appsec.rasp.duration_ext",
        "metrics._dd.appsec.rasp.rule.eval",
        APPSEC_JSON_TAG,
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_nomatch_200():
    with daphne_client(
        "application",
        additional_env={
            "DD_DJANGO_INSTRUMENT_TEMPLATES": "false",
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": rules.RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get("/", headers={"X-Real-Ip": rules._IP.DEFAULT})
        assert result.status_code == 200
        assert result.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.rasp.duration",
        "metrics._dd.appsec.rasp.duration_ext",
        "metrics._dd.appsec.rasp.rule.eval",
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_match_403():
    with daphne_client(
        "application",
        additional_env={
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": rules.RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get(
            "/",
            headers={
                "X-Real-Ip": rules._IP.BLOCKED,
                "Accept": "text/html",
            },
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_HTML, "utf-8")
        assert result.content == as_bytes


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.user-agent",
        "meta.http.useragent",
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "metrics._dd.appsec.rasp.duration",
        "metrics._dd.appsec.rasp.duration_ext",
        "metrics._dd.appsec.rasp.rule.eval",
        "metrics._dd.appsec.event_rules.loaded",
    ]
)
def test_request_ipblock_match_403_json():
    with daphne_client(
        "application",
        additional_env={
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": rules.RULES_GOOD_PATH,
        },
    ) as client:
        result = client.get(
            "/",
            headers={
                "X-Real-Ip": rules._IP.BLOCKED,
            },
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8")
        assert result.content == as_bytes
