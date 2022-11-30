from contextlib import contextmanager
import os
import subprocess
import sys

import django
import pytest

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


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@snapshot(variants={"21x": (2, 0) <= django.VERSION < (2, 2), "": django.VERSION >= (2, 2)})
def test_urlpatterns_include(client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert client.get("/include/test/").status_code == 200


@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_callable_view(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(
    sys.version_info >= (3, 10, 0),
    reason=("func_name changed with Python 3.10 which changes the resource name." "TODO: new snapshot required."),
)
@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_partial_based_view(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.django_db
def test_safe_string_encoding(client, snapshot_context):
    """test_safe_string_encoding.
    If we use @snapshot decorator in a Django snapshot test, the first test adds DB creation traces. Until the
    first request is executed, the SQlite DB isn't create and Django executes the migrations and the snapshot
    raises: Received unmatched spans: 'sqlite.query'
    """
    client.get("/safe-template/")
    with snapshot_context(
        variants={
            "18x": django.VERSION < (1, 9),
            "111x": (1, 9) <= django.VERSION < (1, 12),
            "21x": (1, 12) < django.VERSION < (2, 2),
            "": django.VERSION >= (2, 2),
        }
    ):
        assert client.get("/safe-template/").status_code == 200


@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_404_exceptions(client):
    assert client.get("/404-view/").status_code == 404


@pytest.fixture()
def psycopg2_patched(transactional_db):
    from django.db import connections

    from ddtrace.contrib.psycopg.patch import patch
    from ddtrace.contrib.psycopg.patch import unpatch

    patch()

    # # force recreate connection to ensure psycopg2 patching has occurred
    del connections["postgres"]
    connections["postgres"].close()
    connections["postgres"].connect()

    yield

    unpatch()


@pytest.mark.django_db
def test_psycopg_query_default(client, snapshot_context, psycopg2_patched):
    """Execute a psycopg2 query on a Django database wrapper.

    If we use @snapshot decorator in a Django snapshot test, the first test adds DB creation traces
    """
    from django.db import connections
    from psycopg2.sql import SQL

    with snapshot_context(ignores=["meta.out.host"]):
        query = SQL("""select 'one' as x""")
        conn = connections["postgres"]
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
            assert len(rows) == 1, rows
            assert rows[0][0] == "one"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
    ignores=["meta.http.useragent"],
    token_override="tests.contrib.django.test_django_snapshots.test_asgi_200",
)
@pytest.mark.parametrize("django_asgi", ["application", "channels_application"])
def test_asgi_200(django_asgi):
    with daphne_client(django_asgi) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(ignores=["meta.http.useragent"])
def test_asgi_200_simple_app():
    # The path simple-asgi-app/ routes to an ASGI Application that is not traced
    # This test should generate an empty snapshot
    with daphne_client("channels_application") as client:
        resp = client.get("/simple-asgi-app/")
        assert resp.status_code == 200
        assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(ignores=["meta.http.useragent"])
def test_asgi_200_traced_simple_app():
    with daphne_client("channels_application") as client:
        resp = client.get("/traced-simple-asgi-app/")
        assert resp.status_code == 200
        assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=["meta.error.stack", "meta.http.useragent"],
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_asgi_500():
    with daphne_client("application") as client:
        resp = client.get("/error-500/")
        assert resp.status_code == 500


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


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=["meta.http.useragent"],
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_templates_enabled():
    """Default behavior to compare with disabled variant"""
    with daphne_client("application") as client:
        resp = client.get("/template-view/")
        assert resp.status_code == 200
        assert resp.content == b"some content\n"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=["meta.http.useragent"],
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_templates_disabled():
    """Template instrumentation disabled"""
    with daphne_client("application", additional_env={"DD_DJANGO_INSTRUMENT_TEMPLATES": "false"}) as client:
        resp = client.get("/template-view/")
        assert resp.status_code == 200
        assert resp.content == b"some content\n"
