from contextlib import contextmanager
import os
import signal
import subprocess
import sys

from ddtrace.internal.utils.retry import RetryError
from ddtrace.vendor import psutil
from tests.webclient import Client


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env():
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    return environ


@contextmanager
def gunicorn_server(appsec_enabled="true", remote_configuration_enabled="true", token=None):
    cmd = ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "tests.appsec.app:app"]
    for res in appsec_application_server(
        cmd, appsec_enabled=appsec_enabled, remote_configuration_enabled=remote_configuration_enabled, token=token
    ):
        yield res


@contextmanager
def flask_server(appsec_enabled="true", remote_configuration_enabled="true", token=None):
    cmd = ["python", "tests/appsec/app.py", "--no-reload"]
    for res in appsec_application_server(
        cmd, appsec_enabled=appsec_enabled, remote_configuration_enabled=remote_configuration_enabled, token=token
    ):
        yield res


def appsec_application_server(cmd, appsec_enabled="true", remote_configuration_enabled="true", token=None):
    env = _build_env()
    env["DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] = "0.5"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = remote_configuration_enabled
    if token:
        env["_DD_REMOTE_CONFIGURATION_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:%s," % (token,)
    if appsec_enabled:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")

    server_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://0.0.0.0:8000")

        try:
            print("Waiting for server to start")
            client.wait(max_tries=100, delay=0.1)
            print("Server started")
        except RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
        # If we run a Gunicorn application, we want to get the child's pid, see test_remoteconfiguration_e2e.py
        parent = psutil.Process(server_process.pid)
        children = parent.children(recursive=True)

        yield server_process, client, (children[1].pid if len(children) > 1 else None)
        try:
            client.get_ignored("/shutdown")
        except Exception:
            raise AssertionError(
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
    finally:
        os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
        server_process.wait()
