from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

import django
import pytest

from tests.utils import _build_env
from tests.utils import package_installed
from tests.utils import snapshot
from tests.webclient import Client


SERVER_PORT = 8000
# these tests behave nondeterministically with respect to rate limiting, which can cause the sampling decision to flap
# FIXME: db.name behaves unreliably for some of these tests
SNAPSHOT_IGNORES = ["metrics._sampling_priority_v1", "meta.db.name"]

FILE_PATH = Path(__file__).resolve().parent


@contextmanager
def daphne_client(django_asgi, additional_env=None):
    """Runs a django app hosted with a daphne webserver in a subprocess and
    returns a client which can be used to query it.

    Traces are flushed by invoking a tracer.shutdown() using a /shutdown-tracer route
    at the end of the testcase.
    """

    # Make sure to copy the environment as we need the PYTHONPATH and _DD_TRACE_WRITER_ADDITIONAL_HEADERS (for the test
    # token) propagated to the new process.
    env = _build_env(os.environ.copy(), file_path=FILE_PATH)
    env.update(additional_env or {})
    assert "_DD_TRACE_WRITER_ADDITIONAL_HEADERS" in env, "Client fixture needs test token in headers"
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings",
        }
    )
    # ddtrace-run uses execl which replaces the process but the webserver process itself might spawn new processes.
    # Right now it doesn't but it's possible that it might in the future (ex. uwsgi).
    cmd = [
        "python",
        "-m",
        "ddtrace.commands.ddtrace_run",
        "daphne",
        "-p",
        str(SERVER_PORT),
        "tests.contrib.django.asgi:%s" % django_asgi,
    ]
    subprocess_kwargs = {
        "env": env,
        "start_new_session": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "close_fds": True,
    }

    server_process = subprocess.Popen(cmd, **subprocess_kwargs)
    client = Client("http://localhost:%d" % SERVER_PORT)

    # Wait for the server to start up
    try:
        print("Waiting for server to start")
        client.wait(max_tries=120, delay=0.2, initial_wait=2.0)
        print("Server started")
    except Exception:
        raise AssertionError(
            "Server failed to start, see stdout and stderr logs"
            "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
            "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
            % (server_process.stdout.read(), server_process.stderr.read())
        )

    try:
        yield (client, server_process)
    finally:
        resp = client.get_ignored("/shutdown-tracer")
        assert resp.status_code == 200
        server_process.terminate()


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@snapshot(variants={"": django.VERSION >= (2, 2)}, ignores=SNAPSHOT_IGNORES)
def test_urlpatterns_include(client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert client.get("/include/test/", timeout=5).status_code == 200


@snapshot(
    variants={
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "": django.VERSION >= (2, 2),
    },
    ignores=SNAPSHOT_IGNORES,
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
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "": django.VERSION >= (2, 2),
    },
    ignores=SNAPSHOT_IGNORES,
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
            "111x": (1, 9) <= django.VERSION < (1, 12),
            "": django.VERSION >= (2, 2),
        },
        ignores=SNAPSHOT_IGNORES + ["metrics._dd.tracer_kr"],
    ):
        assert client.get("/safe-template/").status_code == 200


@snapshot(
    variants={
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "": django.VERSION >= (2, 2),
    },
    ignores=SNAPSHOT_IGNORES,
)
def test_404_exceptions(client):
    assert client.get("/404-view/").status_code == 404


@pytest.fixture()
def psycopg2_patched(transactional_db):
    from django.db import connections

    from ddtrace.contrib.internal.psycopg.patch import patch
    from ddtrace.contrib.internal.psycopg.patch import unpatch

    patch()

    # # force recreate connection to ensure psycopg2 patching has occurred
    del connections["postgres"]
    connections["postgres"].close()
    connections["postgres"].connect()

    yield

    unpatch()


@pytest.mark.django_db
def test_psycopg2_query_default(client, snapshot_context, psycopg2_patched):
    """Execute a psycopg2 query on a Django database wrapper.

    If we use @snapshot decorator in a Django snapshot test, the first test adds DB creation traces
    """
    if django.VERSION >= (4, 2, 0) and package_installed("psycopg"):
        # skip test if both versions are available as psycopg2.sql.SQL statement will cause an error from psycopg3
        pytest.skip(reason="Django versions over 4.2.0 use psycopg3 if both psycopg3 and psycopg2 are installed.")

    from django.db import connections
    from psycopg2.sql import SQL as SQL2

    with snapshot_context(ignores=SNAPSHOT_IGNORES + ["meta.out.host", "metrics._dd.tracer_kr"]):
        query = SQL2("""select 'one' as x""")
        conn = connections["postgres"]
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
            assert len(rows) == 1, rows
            assert rows[0][0] == "one"


@pytest.fixture()
def psycopg3_patched(transactional_db):
    # If Django version >= 4.2.0, check if psycopg3 is installed,
    # as we test Django>=4.2 with psycopg2 solely installed and not psycopg3 to ensure both work.
    if django.VERSION < (4, 2, 0):
        pytest.skip(reason="Psycopg3 not supported in django<4.2")
    else:
        from django.db import connections

        from ddtrace.contrib.internal.psycopg.patch import patch
        from ddtrace.contrib.internal.psycopg.patch import unpatch

        patch()

        # # force recreate connection to ensure psycopg3 patching has occurred
        del connections["postgres"]
        connections["postgres"].close()
        connections["postgres"].connect()

        yield

        unpatch()


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (4, 2, 0), reason="Psycopg3 not supported in django<4.2")
def test_psycopg3_query_default(client, snapshot_context, psycopg3_patched):
    """Execute a psycopg3 query on a Django database wrapper.

    If we use @snapshot decorator in a Django snapshot test, the first test adds DB creation traces
    """

    if not package_installed("psycopg"):
        # skip test if psycopg3 is not installed as we need to test psycopg2 standalone with Django>=4.2.0
        pytest.skip(reason="Psycopg3 not installed. Focusing on testing psycopg2 with Django>=4.2.0")

    from django.db import connections
    from psycopg.sql import SQL

    with snapshot_context(ignores=SNAPSHOT_IGNORES + ["meta.out.host", "metrics._dd.tracer_kr"]):
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
        "3x": django.VERSION >= (3, 2, 0),
    },
    ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"],
    token_override="tests.contrib.django.test_django_snapshots.test_asgi_200",
)
@pytest.mark.parametrize("django_asgi", ["application", "channels_application"])
def test_asgi_200(django_asgi):
    with daphne_client(django_asgi) as (client, _):
        resp = client.get("/", timeout=10)
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"])
def test_asgi_200_simple_app():
    # The path simple-asgi-app/ routes to an ASGI Application that is not traced
    # This test should generate an empty snapshot
    with daphne_client("channels_application") as (client, _):
        resp = client.get("/simple-asgi-app/")
        assert resp.status_code == 200
        assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"])
def test_asgi_200_traced_simple_app():
    with daphne_client("channels_application") as (client, _):
        resp = client.get("/traced-simple-asgi-app/", timeout=10)
        assert resp.status_code == 200
        assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=SNAPSHOT_IGNORES + ["meta.error.stack", "meta.http.useragent"],
    variants={
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_asgi_500():
    with daphne_client("application") as (client, _):
        resp = client.get("/error-500/")
        assert resp.status_code == 500


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"],
    variants={
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_templates_enabled():
    """Default behavior to compare with disabled variant"""
    with daphne_client("application") as (client, _):
        resp = client.get("/template-view/", timeout=10)
        assert resp.status_code == 200
        assert resp.content == b"some content\n"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@snapshot(
    ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"],
    variants={
        "3x": django.VERSION >= (3, 2, 0),
    },
)
def test_templates_disabled():
    """Template instrumentation disabled"""
    with daphne_client("application", additional_env={"DD_DJANGO_INSTRUMENT_TEMPLATES": "false"}) as (client, _):
        resp = client.get("/template-view/", timeout=10)
        assert resp.status_code == 200
        assert resp.content == b"some content\n"


@snapshot(ignores=SNAPSHOT_IGNORES)
def test_streamed_file(client):
    assert client.get("/stream-file/").status_code == 200


@snapshot(ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"])
@pytest.mark.skipif(django.VERSION > (3, 0, 0), reason="ASGI not supported in django<3")
def test_django_resource_handler():
    # regression test for: DataDog/dd-trace-py/issues/5711
    with daphne_client("application", additional_env={"DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT": "true"}) as (client, _):
        # Request a class based view
        assert client.get("simple/").status_code == 200


@pytest.mark.parametrize(
    "dd_trace_methods,error_expected",
    [("django_q.tasks[async_task]", True), ("django_q.tasks:async_task", False)],  # legacy syntax, updated syntax
)
@snapshot(ignores=SNAPSHOT_IGNORES + ["meta.http.useragent"])
def test_djangoq_dd_trace_methods(dd_trace_methods, error_expected):
    if error_expected is True:
        pytest.xfail()
    with daphne_client("application", additional_env={"DD_TRACE_METHODS": dd_trace_methods}) as (client, proc):
        assert client.get("simple/", timeout=10).status_code == 200

    _, stderr = proc.communicate(timeout=10)
    assert (b"error configuring Datadog tracing" in stderr) == error_expected
