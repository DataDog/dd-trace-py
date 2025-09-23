from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import typing as _t

from requests.exceptions import ConnectionError  # noqa: A004

from ddtrace.appsec._constants import IAST
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.retry import RetryError
from ddtrace.vendor import psutil
from tests.utils import _build_env
from tests.webclient import Client


FILE_PATH = Path(__file__).resolve().parent


@contextmanager
def gunicorn_flask_server(
    use_ddtrace_cmd=True,
    appsec_enabled="true",
    iast_enabled="false",
    remote_configuration_enabled="true",
    tracer_enabled="true",
    apm_tracing_enabled="true",
    token=None,
    port=8000,
    workers="1",
    use_threads=False,
    use_gevent=False,
    assert_debug=False,
    env=None,
):
    cmd = ["gunicorn", "-w", workers, "--log-level", "debug"]
    if use_ddtrace_cmd:
        cmd = ["python", "-m", "ddtrace.commands.ddtrace_run"] + cmd
    if use_threads:
        cmd += ["--threads", "1"]
    if use_gevent:
        cmd += ["-k", "gevent"]
    cmd += ["-b", "0.0.0.0:%s" % port, "tests.appsec.app:app"]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        iast_enabled=iast_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        env=env,
        port=port,
        assert_debug=assert_debug,
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
    use_ddtrace_cmd=True,
):
    cmd = [python_cmd, app, "--no-reload"]
    if use_ddtrace_cmd:
        cmd = [python_cmd, "-m", "ddtrace.commands.ddtrace_run"] + cmd
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


@contextmanager
def gunicorn_django_server(
    use_ddtrace_cmd: bool = True,
    appsec_enabled: str = "true",
    iast_enabled: str = "false",
    remote_configuration_enabled: str = "true",
    tracer_enabled: str = "true",
    apm_tracing_enabled: str = "true",
    token: str = None,
    port: int = 8000,
    workers: str = "1",
    use_threads: bool = False,
    use_gevent: bool = False,
    assert_debug: bool = False,
    env: dict = None,
):
    """Run the Django test application under Gunicorn.

    Uses the WSGI application at
    ``tests.appsec.integrations.django_tests.django_app.wsgi:application``.
    Mirrors options supported by gunicorn_flask_server.
    """
    cmd = ["gunicorn", "-w", workers, "--log-level", "debug"]
    if use_ddtrace_cmd:
        cmd = ["python", "-m", "ddtrace.commands.ddtrace_run"] + cmd
    if use_threads:
        cmd += ["--threads", "1"]
    if use_gevent:
        cmd += ["-k", "gevent"]
    cmd += [
        "-b",
        f"0.0.0.0:{port}",
        "tests.appsec.integrations.django_tests.django_app.wsgi:application",
    ]
    # Ensure Django settings are set for WSGI
    extra_env = {
        "DJANGO_SETTINGS_MODULE": "tests.appsec.integrations.django_tests.django_app.settings",
    }
    if env:
        extra_env.update(env)
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        iast_enabled=iast_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        env=extra_env,
        port=port,
        assert_debug=assert_debug,
    )


@contextmanager
def django_server(
    python_cmd="python",
    appsec_enabled="false",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    apm_tracing_enabled=None,
    token=None,
    port=8000,
    env=None,
    assert_debug=False,
    manual_propagation_debug=False,
    *args,
    **kwargs,
):
    """
    Context manager that runs a Django test server in a subprocess.

    This server uses the Django test application located in tests/appsec/integrations/django_tests/django_app.
    The server is started when entering the context and stopped when exiting.
    """
    manage_py = "tests/appsec/integrations/django_tests/django_app/manage.py"
    cmd = [
        python_cmd,
        "-m",
        "ddtrace.commands.ddtrace_run",
        python_cmd,
        manage_py,
        "runserver",
        f"0.0.0.0:{port}",
        "--noreload",
    ]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        iast_enabled=iast_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        port=port,
        env=env,
        assert_debug=assert_debug,
        manual_propagation_debug=manual_propagation_debug,
    )


@contextmanager
def uvicorn_server(
    python_cmd="python",
    appsec_enabled="false",
    remote_configuration_enabled="true",
    iast_enabled="false",
    tracer_enabled="true",
    apm_tracing_enabled=None,
    token=None,
    app="tests.appsec.integrations.fastapi_tests.app:app",
    env=None,
    port=8000,
    assert_debug=False,
    manual_propagation_debug=False,
):
    """
    Context manager that runs a FastAPI test server in a subprocess using Uvicorn.

    This server uses the FastAPI test application located in tests/appsec/integrations/fastapi_tests.
    The server is started when entering the context and stopped when exiting.
    """
    cmd = [
        python_cmd,
        "-m",
        "ddtrace.commands.ddtrace_run",
        "uvicorn",
        app,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--no-access-log",
    ]
    yield from appsec_application_server(
        cmd,
        appsec_enabled=appsec_enabled,
        apm_tracing_enabled=apm_tracing_enabled,
        remote_configuration_enabled=remote_configuration_enabled,
        iast_enabled=iast_enabled,
        tracer_enabled=tracer_enabled,
        token=token,
        port=port,
        env=env,
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
    """Start an application server subprocess for AppSec/IAST tests.

    This helper optionally applies CPU/memory limits to the spawned subprocess when the following
    environment variables are set (Linux/Unix only):
      - TEST_SUBPROC_MEM_MB: integer megabytes to cap address space (RLIMIT_AS)
      - TEST_SUBPROC_CPU_AFFINITY: comma-separated CPU indices for sched_setaffinity (Linux)
      - TEST_SUBPROC_NICE: integer niceness value to apply via os.nice()

    This is opt-in and introduces no behavior change unless the variables are provided.
    """
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
        env["_DD_IAST_PATCH_MODULES"] = "tests.appsec."
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

    # Only set preexec_fn on POSIX. It's ignored/unsupported on Windows.
    if os.name == "posix":
        preexec = _make_preexec()
        if preexec is not None:
            subprocess_kwargs["preexec_fn"] = preexec  # type: ignore[assignment]

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


def _make_preexec() -> _t.Optional[_t.Callable[[], None]]:
    """Create a preexec_fn that applies resource limits if configured.

    Returns None if no limits were requested.
    """
    mem_mb = os.environ.get("TEST_SUBPROC_MEM_MB")
    cpu_aff = os.environ.get("TEST_SUBPROC_CPU_AFFINITY")
    nice_val = os.environ.get("TEST_SUBPROC_NICE")
    if not any((mem_mb, cpu_aff, nice_val)):
        return None

    # Import inside to keep portability on Windows.
    try:
        import resource  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        resource = None  # type: ignore[assignment]

    def _preexec():  # pragma: no cover - exercised in integration tests
        # Set process group leader (already done via start_new_session)
        # Apply niceness first to reduce priority
        if nice_val is not None:
            try:
                os.nice(int(nice_val))
            except Exception:
                pass
        # CPU affinity (Linux only)
        if cpu_aff:
            try:
                cpus = {int(x) for x in cpu_aff.split(",") if x.strip() != ""}
                if hasattr(os, "sched_setaffinity") and cpus:
                    os.sched_setaffinity(0, cpus)  # type: ignore[attr-defined]
            except Exception:
                pass
        # Memory limit via RLIMIT_AS (virtual memory)
        if mem_mb and resource is not None:
            try:
                limit_bytes = int(mem_mb) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
            except Exception:
                # Fall back silently if not supported
                pass

    return _preexec
