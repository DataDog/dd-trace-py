import os
import subprocess
import time

import django
import pytest
import requests
import six
import tenacity

from tests.utils import snapshot


SERVER_PORT = 8000


class Client(object):
    """HTTP Client for making requests to a local http server."""

    def __init__(self, base_url):
        # type: (str) -> None
        self._base_url = base_url
        self._session = requests.Session()

    def _url(self, path):
        # type: (str) -> str
        return six.moves.urllib.parse.urljoin(self._base_url, path)

    def wait(self, path="/", max_tries=100, delay=0.1):
        # type: (str, int, float) -> None
        """Wait for the server to start by repeatedly http `get`ting `path` until a 200 is received."""

        @tenacity.retry(stop=tenacity.stop_after_attempt(max_tries), wait=tenacity.wait_fixed(delay))
        def ping():
            r = requests.get(self._url(path))
            assert r.status_code == 200

        ping()

    def get(self, path, **kwargs):
        return self._session.get(self._url(path), **kwargs)

    def post(self, path, *args, **kwargs):
        return self._session.post(self._url(path), *args, **kwargs)

    def request(self, method, path, *args, **kwargs):
        return self._session.request(method, self._url(path), *args, **kwargs)


@pytest.fixture(scope="module")
def daphne_client():
    """Runs a django app hosted with a daphne webserver in a subprocess and
    returns a client which can be used to query it.

    Note that test cases using this fixture will have to wait for the traces
    to be flushed to the test agent.
    """
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings",
            # Make the writer write much more frequently as to not wait for long after a test case.
            # It would be nice to use the synchronous writer but this isn't currently configurable
            # via env var.
            # Note that even with a synchronous writer it would still be required to wait after a test
            # case for the traces to come in because the trace wraps around the request and hence can
            # only be sent _after_ the request has been completed.
            "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.001",
        }
    )

    # ddtrace-run uses execl which replaces the process but the webserver process itself might spawn new processes.
    # Right now it doesn't but it's possible that it might in the future (ex. uwsgi).
    cmd = ["ddtrace-run", "daphne", "-p", str(SERVER_PORT), "tests.contrib.django.asgi:application"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )

    client = Client("http://localhost:%d" % SERVER_PORT)

    # TODO: process might fail to start, handle this and forward the output to help debug
    client.wait()

    try:
        yield client
    finally:
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
@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_safe_string_encoding(client):
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


@snapshot(ignores=["meta.out.host"])
@pytest.mark.django_db
def test_psycopg_query_default(client, psycopg2_patched):
    """Execute a psycopg2 query on a Django database wrapper"""
    from django.db import connections
    from psycopg2.sql import SQL

    query = SQL("""select 'one' as x""")
    conn = connections["postgres"]
    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    async_mode=False,
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        # "32": (3, 2, 0) <= django.VERSION < (3, 3, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_asgi_200(daphne_client):
    resp = daphne_client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."
    time.sleep(0.1)


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    async_mode=False,
    ignores=["meta.error.stack"],
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        # "32": (3, 2, 0) <= django.VERSION < (3, 3, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_asgi_500(daphne_client):
    resp = daphne_client.get("/error-500")
    assert resp.status_code == 500
    time.sleep(0.1)
