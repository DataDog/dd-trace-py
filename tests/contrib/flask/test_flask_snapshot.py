import os
import signal
import subprocess
import time
from typing import Dict
from typing import Generator
from typing import List

import pytest
import tenacity

from ddtrace.contrib.flask.patch import flask_version
from tests.webclient import Client


DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}


@pytest.fixture
def flask_port():
    # type: () -> str
    return "8000"


@pytest.fixture
def flask_wsgi_application():
    # type: () -> str
    return "tests.contrib.flask.app:app"


@pytest.fixture
def flask_command(flask_wsgi_application, flask_port):
    # type: (str, str) -> List[str]
    cmd = "ddtrace-run flask run -h 0.0.0.0 -p %s" % (flask_port,)
    return cmd.split()


@pytest.fixture
def flask_env(flask_wsgi_application):
    # type: (str) -> Dict[str, str]
    env = os.environ.copy()
    env.update(
        {
            # Avoid noisy database spans being output on app startup/teardown.
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "FLASK_APP": flask_wsgi_application,
        }
    )
    return env


@pytest.fixture
def flask_client(flask_command, flask_env, flask_port):
    # type: (str, Dict[str, str], str) -> Generator[Client, None, None]
    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        flask_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=flask_env,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://0.0.0.0:%s" % flask_port)
        # Wait for the server to start up
        try:
            client.wait()
        except tenacity.RetryError:
            # if proc.returncode is not None:
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
        time.sleep(0.2)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.snapshot(
    ignores=["meta.flask.version"], variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)}
)
def test_flask_200(flask_client):
    # type: (Client) -> None
    assert flask_client.get("/", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot(
    ignores=["meta.flask.version"], variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)}
)
def test_flask_stream(flask_client):
    # type: (Client) -> None
    assert flask_client.get("/stream", headers=DEFAULT_HEADERS, stream=True).status_code == 200


@pytest.mark.snapshot(
    ignores=["meta.flask.version", "meta.http.useragent"],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
def test_flask_get_user(flask_client):
    # type: (Client) -> None
    assert flask_client.get("/identify").status_code == 200
