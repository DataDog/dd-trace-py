import pytest
import os
import signal
import subprocess

from ddtrace.internal.utils.retry import RetryError
from tests.webclient import Client

from ddtrace.contrib.azure import patch
from ddtrace.contrib.azure import unpatch
@pytest.mark.snapshot
def test_azure():
    pass


@pytest.fixture
def start_command():
    cmd = "func start --verbose"
    return cmd.split()


@pytest.fixture(autouse=True)
def patch_azure():
    patch()
    yield
    unpatch()

@pytest.fixture
def azure_client(start_command, flask_port, flask_wsgi_application, flask_env_arg):
    proc = subprocess.Popen(
        start_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        preexec_fn=os.setsid,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    )
    try:
        client = Client("http://0.0.0.0:%s" % 7071)
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
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()

def test_flask_200(azure_client):
    # type: (Client) -> None
    assert azure_client.get("/api/HttpExample").status_code == 200