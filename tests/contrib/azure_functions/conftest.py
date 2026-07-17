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
        client.wait(delay=0.5, initial_wait=2.0)
    except Exception as e:
        stdout = _read_log(stdout_log)
        stderr = _read_log(stderr_log)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        stdout_log.close()
        stderr_log.close()
        raise TimeoutError(
            "Azure Functions host failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
        ) from e
    else:
        stdout_log.close()
        stderr_log.close()
    return proc, client


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    # Snapshot tests set a unique X-Datadog-Test-Session-Token per test via os.environ.
    # The func worker reads that token at startup, so we must start a fresh host per test.
    proc, client = _start_azure_functions_server(env_vars or None)
    try:
        yield client
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
