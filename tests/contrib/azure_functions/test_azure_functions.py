import os
import signal
import subprocess
import time

import pytest

from ddtrace.internal.utils.retry import RetryError
from tests.webclient import Client


DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}


@pytest.fixture
def azure_functions_client():
    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        ["func", "start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=os.environ.copy(),
        preexec_fn=os.setsid,
        cwd=os.path.join(os.path.dirname(__file__), "azure_function_app"),
    )
    try:
        client = Client("http://0.0.0.0:7071")
        # Wait for the server to start up
        try:
            client.wait()
        except RetryError:
            # process failed
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            raise TimeoutError(
                "Server failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
            )
        yield client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.snapshot
def test_azure_function(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httptest", headers=DEFAULT_HEADERS).status_code == 200
