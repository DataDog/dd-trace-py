import os
import signal
import subprocess
import tempfile
import time

import pytest

from tests.webclient import Client


AZURE_FUNCTIONS_PORT = 7071
AZURE_FUNCTION_APP_DIR = os.path.join(os.path.dirname(__file__), "azure_function_app")


def _read_log(log_file):
    # type: (object) -> str
    log_file.seek(0)
    return log_file.read().decode("utf-8", errors="replace")


def _start_azure_functions_server(extra_env=None):
    # type: (dict | None) -> tuple[subprocess.Popen, Client]
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    port = AZURE_FUNCTIONS_PORT
    env["AZURE_FUNCTIONS_TEST_PORT"] = str(port)
    env["DD_TRACE_STATS_COMPUTATION_ENABLED"] = "False"  # disable stats computation to avoid potential flakes in tests

    # Use temp files instead of PIPE to capture logs without deadlocking when func is verbose.
    stdout_log = tempfile.TemporaryFile(mode="w+b")
    stderr_log = tempfile.TemporaryFile(mode="w+b")

    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        ["func", "start", "--port", str(port)],
        stdout=stdout_log,
        stderr=stderr_log,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=AZURE_FUNCTION_APP_DIR,
    )
    client = Client("http://0.0.0.0:%d" % port)
    try:
        client.wait(delay=0.5)
    except Exception:
        stdout = _read_log(stdout_log)
        stderr = _read_log(stderr_log)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise TimeoutError(
            "Azure Functions host failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
        )
    return proc, client


@pytest.fixture(scope="session")
def azure_functions_server():
    proc, _client = _start_azure_functions_server()
    try:
        yield AZURE_FUNCTIONS_PORT
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    if env_vars:
        proc, client = _start_azure_functions_server(env_vars)
        try:
            yield client
            time.sleep(1)
        finally:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return

    port = request.getfixturevalue("azure_functions_server")
    client = Client("http://0.0.0.0:%d" % port)
    yield client
    # At this point the traces have been sent to the test agent
    # but the test agent hasn't necessarily finished processing
    # the traces (race condition) so wait just a bit for that
    # processing to complete.
    time.sleep(1)


def pytest_collection_modifyitems(session, config, items):
    # The disabled distributed tracing variant needs its own func host on port 7071.
    # Run it before any test that starts the session-scoped host.
    disabled_distributed_tracing = []
    other = []
    for item in items:
        if "test_http_get_distributed_tracing[disabled]" in item.nodeid:
            disabled_distributed_tracing.append(item)
        else:
            other.append(item)
    items[:] = disabled_distributed_tracing + other
