import os
import signal
import subprocess
import sys
import time

import django
import pytest
import tenacity

from tests.webclient import Client


@pytest.fixture
def django_port():
    yield "8000"


@pytest.fixture
def django_wsgi_application():
    yield "application"


@pytest.fixture
def django_app():
    if django.VERSION >= (2, 0, 0):
        yield "django_app"
    else:
        yield "django1_app"


@pytest.fixture
def django_settings(django_app):
    yield "tests.contrib.django.%s.settings" % django_app


@pytest.fixture
def django_command(django_wsgi_application, django_port):
    cmd = [
        "ddtrace-run",
        "python",
        os.path.join(os.path.dirname(__file__), "manage.py"),
        "runserver",
        "8000",
    ]
    yield cmd


@pytest.fixture
def django_env():
    yield {}


@pytest.fixture
def django_client(django_command, django_settings, django_env, django_port):
    """Runs a django app hosted with a daphne webserver in a subprocess and
    returns a client which can be used to query it.

    Traces are flushed by invoking a tracer.shutdown() using a /shutdown-tracer route
    at the end of the testcase.
    """

    # Make sure to copy the environment as we need the PYTHONPATH.
    env = os.environ.copy()
    assert (
        "_DD_TRACE_WRITER_ADDITIONAL_HEADERS" in env
        and "X-Datadog-Test-Session-Token" in env["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"]
    ), "Need to ensure X-Datadog-Test-Session-Token is provided to the application for snapshots to be correlated"
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": django_settings,
            # Avoid noisy database spans being output on app startup/teardown.
            "DD_TRACE_SQLITE3_ENABLED": "0",
            # Middleware generates large snapshots with redundant info'
            # For readability we disable it but at least one test case should
            # override this value to ensure the middleware spans.
            "DD_DJANGO_INSTRUMENT_MIDDLEWARE": "0",
            # Database spans can be noisy as operations can happen unrelated
            # to the endpoint tested.
            # Tests which access the database should override this flag.
            "DD_DJANGO_INSTRUMENT_DATABASES": "0",
        }
    )
    env.update(django_env)

    # ddtrace-run python manage.py and other webservers might exec or fork into another
    # process, so we need to os.setsid() to create a process group (all of which will
    # listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        django_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://localhost:%s" % django_port)
        # Wait for the server to start up
        try:
            client.wait()
        except tenacity.RetryError:
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            raise TimeoutError(
                "Server failed to start\n======DJANGO STDOUT=====%s\n\n======DJANGO STDERR=====%s\n" % (stdout, stderr)
            )
        yield client
        resp = client.get_ignored("/shutdown-tracer")
        assert resp.status_code == 200, resp.text
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(0.2)
    finally:
        os.killpg(proc.pid, signal.SIGUSR1)
        proc.wait()


@pytest.mark.parametrize(
    "django_env",
    [
        {
            "DD_DJANGO_INSTRUMENT_MIDDLEWARE": "1",
        }
    ],
)
@pytest.mark.snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_wsgi_200(django_client, django_env):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert django_client.get("/").status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@pytest.mark.snapshot(variants={"21x": (2, 0) <= django.VERSION < (2, 2), "": django.VERSION >= (2, 2)})
def test_urlpatterns_include(django_client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert django_client.get("/include/test/").status_code == 200


@pytest.mark.snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_callable_view(django_client):
    # ensures that the internals are properly traced when using callable views
    assert django_client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(
    sys.version_info >= (3, 10, 0),
    reason=("func_name changed with Python 3.10 which changes the resource name." "TODO: new snapshot required."),
)
@pytest.mark.snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_partial_based_view(django_client):
    # ensures that the internals are properly traced when using a function views
    assert django_client.get("/partial-view/").status_code == 200


@pytest.mark.snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_404_exceptions(django_client):
    assert django_client.get("/404-view/").status_code == 404


@pytest.mark.snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    },
    ignores=["meta.out.host"],
)
@pytest.mark.parametrize(
    "django_env",
    [
        {
            "DD_DJANGO_INSTRUMENT_DATABASES": "1",
        }
    ],
)
def test_psycopg_query_default(django_client, django_env):
    """Execute a psycopg2 query on a Django database wrapper"""
    assert django_client.get("/psycopg_query_default/").status_code == 200


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@pytest.mark.snapshot(
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:application"],
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:channels_application"],
    ],
)
def test_asgi_200(django_client, django_command):
    resp = django_client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@pytest.mark.snapshot
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:channels_application"],
    ],
)
def test_asgi_200_simple_app(django_command, django_client):
    # The path simple-asgi-app/ routes to an ASGI Application that is not traced
    # This test should generate an empty snapshot
    resp = django_client.get("/simple-asgi-app/")
    assert resp.status_code == 200
    assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@pytest.mark.snapshot
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:channels_application"],
    ],
)
def test_asgi_200_traced_simple_app(django_client, django_command):
    resp = django_client.get("/traced-simple-asgi-app/")
    assert resp.status_code == 200
    assert resp.content == b"Hello World. It's me simple asgi app"


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="ASGI not supported in django<3")
@pytest.mark.snapshot(
    ignores=["meta.error.stack"],
    variants={
        "30": (3, 0, 0) <= django.VERSION < (3, 1, 0),
        "31": (3, 1, 0) <= django.VERSION < (3, 2, 0),
        "3x": django.VERSION >= (3, 2, 0),
    },
)
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:application"],
    ],
)
def test_asgi_500(django_client, django_command):
    resp = django_client.get("/error-500/")
    assert resp.status_code == 500


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@pytest.mark.snapshot(ignores=["meta.error.stack"])
@pytest.mark.parametrize("django_env", [{"DD_APPSEC_ENABLED": "true"}])
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:application"],
    ],
)
def test_appsec_enabled(django_client, django_env, django_command):
    resp = django_client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."


@pytest.mark.skipif(django.VERSION < (3, 2, 0), reason="Only want to test with latest Django")
@pytest.mark.parametrize("django_env", [{"DD_APPSEC_ENABLED": "true"}])
@pytest.mark.parametrize(
    "django_command",
    [
        ["ddtrace-run", "daphne", "-p", "8000", "tests.contrib.django.asgi:application"],
    ],
)
@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_appsec_enabled_attack(django_client, django_env, django_command):
    resp = django_client.get("/.git")
    assert resp.status_code == 404
