from contextlib import contextmanager
import os
import signal
import subprocess
import sys

from requests.exceptions import ConnectionError

from ddtrace.internal.utils.retry import RetryError
from ddtrace.vendor import psutil
from tests.webclient import Client


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env(env=None):
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    if env:
        for k, v in env.items():
            environ[k] = v
    return environ


@contextmanager
def gunicorn_server(appsec_enabled="true", remote_configuration_enabled="true", tracer_enabled="true", token=None):
    cmd = ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "tests.appsec.app:app"]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
    )


@contextmanager
def flask_server(
    appsec_enabled="true",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    token=None,
    app="tests/appsec/app.py",
    env=None,
):
    cmd = ["python", app, "--no-reload"]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        iast_enabled=iast_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        env=env,
    )


def appsec_application_server(
    cmd,
    appsec_enabled="true",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    token=None,
    env=None,
):
    env = _build_env(env)
    env["DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] = "0.5"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = remote_configuration_enabled
    if token:
        env["_DD_REMOTE_CONFIGURATION_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:%s," % (token,)
    if appsec_enabled is not None:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    if iast_enabled is not None and iast_enabled != "false":
        env["DD_IAST_ENABLED"] = iast_enabled
        env["DD_IAST_REQUEST_SAMPLING"] = "100"
        env["_DD_APPSEC_DEDUPLICATION_ENABLED"] = "false"
    if tracer_enabled is not None:
        env["DD_TRACE_ENABLED"] = tracer_enabled
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")

    server_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        start_new_session=True,
    )
    try:
        client = Client("http://0.0.0.0:8000")

        try:
            print("Waiting for server to start")
            client.wait(max_tries=120, delay=0.1, initial_wait=1.0)
            print("Server started")
        except RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
        except Exception:
            raise AssertionError(
                "Server FAILED, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
        # If we run a Gunicorn application, we want to get the child's pid, see test_flask_remoteconfig.py
        parent = psutil.Process(server_process.pid)
        children = parent.children(recursive=True)

        yield server_process, client, (children[1].pid if len(children) > 1 else None)
        try:
            client.get_ignored("/shutdown")
        except ConnectionError:
            pass
        except Exception:
            raise AssertionError(
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
    finally:
        os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
        server_process.terminate()
        server_process.wait()
