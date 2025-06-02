from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys

from requests.exceptions import ConnectionError  # noqa: A004

from ddtrace.appsec._constants import IAST
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.retry import RetryError
from ddtrace.vendor import psutil
from tests.utils import _build_env
from tests.webclient import Client


FILE_PATH = Path(__file__).resolve().parent


@contextmanager
def gunicorn_server(
    appsec_enabled="true",
    iast_enabled="false",
    remote_configuration_enabled="true",
    tracer_enabled="true",
    apm_tracing_enabled="true",
    token=None,
    port=8000,
):
    cmd = ["gunicorn", "-w", "3", "-b", "0.0.0.0:%s" % port, "tests.appsec.app:app"]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        iast_enabled=iast_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        port=port,
    )


@contextmanager
def flask_server(
    python_cmd="python",
    appsec_enabled="false",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    apm_tracing_enabled=None,
    token=None,
    app="tests/appsec/app.py",
    env=None,
    port=8000,
    assert_debug=False,
    manual_propagation_debug=False,
):
    cmd = [python_cmd, app, "--no-reload"]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        iast_enabled=iast_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        env=env,
        port=port,
        assert_debug=assert_debug,
        manual_propagation_debug=manual_propagation_debug,
    )


def appsec_application_server(
    cmd,
    appsec_enabled="true",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    apm_tracing_enabled=None,
    token=None,
    env=None,
    port=8000,
    assert_debug=False,
    manual_propagation_debug=False,
):
    env = _build_env(env, file_path=FILE_PATH)
    env["DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] = "0.5"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = remote_configuration_enabled
    if token:
        env["_DD_REMOTE_CONFIGURATION_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:%s," % (token,)
        env["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:{}".format(token)
    if appsec_enabled is not None:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    if apm_tracing_enabled is not None:
        env["DD_APM_TRACING_ENABLED"] = apm_tracing_enabled
    if iast_enabled is not None and iast_enabled != "false":
        env[IAST.ENV] = iast_enabled
        env[IAST.ENV_REQUEST_SAMPLING] = "100"
        env["DD_IAST_DEDUPLICATION_ENABLED"] = "false"
        env[IAST.ENV_NO_DIR_PATCH] = "false"
        if assert_debug:
            env["_" + IAST.ENV_DEBUG] = iast_enabled
            env["_" + IAST.ENV_PROPAGATION_DEBUG] = iast_enabled
            env["DD_TRACE_DEBUG"] = iast_enabled
    if tracer_enabled is not None:
        env["DD_TRACE_ENABLED"] = tracer_enabled
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")
    env["FLASK_RUN_PORT"] = str(port)

    subprocess_kwargs = {
        "env": env,
        "start_new_session": True,
        "stdout": sys.stdout,
        "stderr": sys.stderr,
    }
    if assert_debug:
        if not manual_propagation_debug:
            subprocess_kwargs["stdout"] = subprocess.PIPE
            subprocess_kwargs["stderr"] = subprocess.PIPE
        subprocess_kwargs["text"] = True

    server_process = subprocess.Popen(cmd, **subprocess_kwargs)
    try:
        client = Client("http://0.0.0.0:%s" % port)

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
        if (assert_debug and PYTHON_VERSION_INFO >= (3, 10)) and (iast_enabled is not None and iast_enabled != "false"):
            process_output = server_process.stderr.read()
            assert "Return from " in process_output
            assert "Return value is tainted" in process_output
            assert "Tainted arguments:" in process_output
