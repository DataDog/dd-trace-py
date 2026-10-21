from pathlib import Path
import subprocess
import sys
import tempfile
from textwrap import dedent

import pytest


# DEV: This test must pass ALWAYS. If this test fails, it means that something
# needs to be fixed somewhere. Please DO NOT skip this test under any
# circumstance!
@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
    ),
    parametrize={
        "DD_REMOTE_CONFIGURATION_ENABLED": ("1", "0"),
    },
)
def test_auto():
    import os
    import sys

    import ddtrace.auto  # noqa:F401

    assert "threading" not in sys.modules
    assert "socket" not in sys.modules
    assert "subprocess" not in sys.modules

    if os.getenv("DD_REMOTE_CONFIGURATION_ENABLED") == "0":
        # When unloading modules we must have the HTTP clients imported already
        assert "ddtrace.internal.http" in sys.modules

        # Verify that ddtrace's HTTP connections are backed by the native Rust
        # client and therefore have no Python socket dependency.  Even after
        # gevent nullifies socket primitives, the connections remain usable.
        import socket  # noqa:F401

        socket.create_connection = None
        socket.socket = None

        from ddtrace.internal.http import HTTPConnection  # noqa:F401

        conn = HTTPConnection("localhost")
        assert not hasattr(conn, "_create_connection"), (
            "HTTPConnection must not hold a reference to socket.create_connection (use the native Rust client instead)"
        )
        assert not hasattr(conn, "sock"), (
            "HTTPConnection must not hold a Python socket (use the native Rust client instead)"
        )


def test_pytest_with_gevent_and_ddtrace_auto():
    """
    Test that pytest works when a module imports ddtrace.auto and gevent is installed.

    This is a regression test for an issue where cleanup_loaded_modules() would delete
    modules from sys.modules while they were still being imported by pytest's assertion
    rewriter, causing a KeyError during import.

    The fix checks if a module's __spec__._initializing is True before deleting it.
    """
    pytest.importorskip("pytest")
    pytest.importorskip("gevent")

    # Create a temporary directory with test files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a module that imports ddtrace.auto
        foo_module = tmpdir_path / "foo.py"
        foo_module.write_text(
            """import ddtrace.auto
A = 1
"""
        )

        # Create a test file that imports the module
        test_file = tmpdir_path / "test_foo.py"
        test_file.write_text(
            """from foo import A

def test_foo():
    assert A == 1
"""
        )

        # Run pytest as a subprocess with the current ddtrace on PYTHONPATH
        import os

        import ddtrace

        ddtrace_path = str(Path(ddtrace.__file__).parent.parent)
        env = os.environ.copy()
        # Prepend ddtrace path to PYTHONPATH to ensure we use the local version
        env["PYTHONPATH"] = ddtrace_path + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env,
        )

        # The test should pass without KeyError
        assert result.returncode == 0, f"pytest failed:\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        assert "KeyError" not in result.stdout
        assert "KeyError" not in result.stderr
        assert "1 passed" in result.stdout


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_dataclasses_not_unloaded():
    import ddtrace  # noqa

    import dataclasses

    @dataclasses.dataclass
    class A:
        x: int

    import ddtrace.auto  # noqa

    # Reload the module (if it was unloaded)
    import dataclasses  # noqa

    @dataclasses.dataclass
    class B(A):
        y: int

    b = B(x=1, y=2)

    assert b


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_warnings_not_unloaded():
    """``logging.captureWarnings()`` must keep working after module cleanup.

    Regression test for #20875. ``logging`` is kept loaded (it is in
    ``KEEP_MODULES``) but ``warnings`` used to be unloaded, so
    ``logging.captureWarnings(True)`` patched a stale ``warnings`` module while
    ``warnings.warn()`` used the freshly re-imported one. Warnings then bypassed
    ``logging`` and were printed straight to stderr.
    """
    import ddtrace  # noqa

    import ddtrace.auto  # noqa  (runs cleanup_loaded_modules())

    import io
    import logging
    import sys
    import warnings

    assert logging.warnings is sys.modules["warnings"]

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("py.warnings")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    logging.captureWarnings(True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("captureWarnings regression")
    finally:
        logging.captureWarnings(False)
        logger.removeHandler(handler)

    assert "captureWarnings regression" in stream.getvalue()


# Remote config is disabled so that the service below is the only ddtrace worker
# that can import threading, which makes the race deterministic. In the original
# report the remote config poller imported threading while enabling Exception
# Replay from an APM_TRACING payload received during bootstrap.
@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
        DD_REMOTE_CONFIGURATION_ENABLED="0",
    ),
    timeout=20,
)
def test_threading_reimported_by_background_worker_does_not_hang_at_exit():
    """The interpreter must exit when a ddtrace worker re-imports threading first.

    Module cloning drops threading from sys.modules. On CPython < 3.13,
    threading binds _main_thread to whichever thread imports the module, so if a
    ddtrace background worker wins that race, the worker becomes the "main
    thread" and threading._shutdown() blocks on it forever at exit, before the
    atexit handlers that would stop the worker get a chance to run.
    """
    import sys

    import ddtrace.auto  # noqa:F401  (runs cleanup_loaded_modules())

    # Imported before cleanup, like the remote config poller, so that it keeps
    # using ddtrace's own copy of threading.
    from ddtrace.internal.periodic import PeriodicService

    assert "threading" not in sys.modules

    imported = []

    class Poller(PeriodicService):
        def periodic(self):
            if not imported:
                import threading  # noqa:F401

                imported.append(threading)

    # Never stopped explicitly: in the real case the worker is only stopped by
    # ddtrace's atexit handlers, which run after threading._shutdown().
    Poller(interval=0.01, no_wait_at_start=True).start()

    import time

    deadline = time.monotonic() + 10
    while not imported and time.monotonic() < deadline:
        time.sleep(0.01)
    assert imported, "background worker did not import threading"

    import threading

    assert threading is imported[0]
    assert threading.current_thread() is threading.main_thread(), (
        threading.current_thread(),
        threading.main_thread(),
    )


def test_uwsgi_gevent():
    """
    Test that we support uwsgi + gevent when threads are patched.
    """
    pytest.importorskip("gevent")

    # Create a temporary directory with test files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create the main app
        (tmpdir_path / "app.py").write_text(
            dedent(
                r"""
                import gevent.monkey

                gevent.monkey.patch_all()


                def app(environ, start_response):
                    status = "200 OK"
                    headers = [("Content-Type", "text/plain")]
                    start_response(status, headers)
                    return [b"Hello, World!\n"]
            """
            )
        )

        # Create the config
        (tmpdir_path / "config.ini").write_text(
            dedent(
                """
                [uwsgi]
                module = app:app
                master = true
                die-on-term = true
                post-buffering = true
                reload-mercy = 30
                worker-reload-mercy = 15
                need-app = true
                strict = true
                disable-write-exception = true
                single-interpreter = 1
                enable-threads = 1
                http = :8000
                chmod-socket = 666
                gevent = 10
                processes = 1
                lazy-apps = 1
                import = ddtrace.auto
            """
            )
        )

        proc = subprocess.Popen(
            ["uwsgi", "--ini", "config.ini"],
            cwd=tmpdir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # If we time out, it means uwsgi started successfully and is
            # running the app.
            proc.terminate()
            proc.wait()
        else:
            msg = "application is running successfully"
            raise AssertionError(msg)
