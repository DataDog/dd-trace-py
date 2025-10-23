from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
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
    use_ddtrace_cmd: bool = True,
    appsec_enabled: str = "true",
    iast_enabled: str = "false",
    remote_configuration_enabled: str = "true",
    tracer_enabled: str = "true",
    apm_tracing_enabled: str = "true",
    token: str = "",
    port: int = 8000,
    workers: str = "1",
    use_threads: bool = False,
    use_gevent: bool = False,
    assert_debug: bool = False,
    env: dict = {},
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
    cmd = ["gunicorn", "-w", workers, "--log-level", "debug"]
    if use_ddtrace_cmd:
        cmd = ["python", "-m", "ddtrace.commands.ddtrace_run"] + cmd
        env["_USE_DDTRACE_COMMAND"] = "true"
    else:
        env["_USE_DDTRACE_COMMAND"] = ""
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
    python_cmd: str = "python",
    appsec_enabled: _t.Optional[str] = "false",
    remote_configuration_enabled: str = "true",
    iast_enabled: _t.Optional[str] = "false",
    tracer_enabled: _t.Optional[str] = "true",
    apm_tracing_enabled: _t.Optional[str] = "",
    token: _t.Optional[str] = None,
    app: str = "tests/appsec/app.py",
    env: dict = {},
    port: int = 8000,
    assert_debug: bool = False,
    manual_propagation_debug: bool = False,
    use_ddtrace_cmd: bool = True,
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
    cmd = [python_cmd, app, "--no-reload"]
    if use_ddtrace_cmd:
        cmd = [python_cmd, "-m", "ddtrace.commands.ddtrace_run"] + cmd
        env["_USE_DDTRACE_COMMAND"] = "true"
    else:
        env["_USE_DDTRACE_COMMAND"] = ""
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
    token: _t.Optional[str] = None,
    port: int = 8000,
    workers: str = "1",
    use_threads: bool = False,
    use_gevent: bool = False,
    assert_debug: bool = False,
    env: dict = {},
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
    if use_ddtrace_cmd:
        extra_env["_USE_DDTRACE_COMMAND"] = "true"
    else:
        extra_env["_USE_DDTRACE_COMMAND"] = ""
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
    python_cmd: str = "python",
    appsec_enabled: _t.Optional[str] = "false",
    remote_configuration_enabled: str = "true",
    iast_enabled: _t.Optional[str] = "false",
    tracer_enabled: _t.Optional[str] = "true",
    apm_tracing_enabled: _t.Optional[str] = None,
    token: _t.Optional[str] = None,
    port: int = 8000,
    env: _t.Optional[dict] = None,
    assert_debug: bool = False,
    manual_propagation_debug: bool = False,
    *args: _t.Any,
    **kwargs: _t.Any,
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
    python_cmd: str = "python",
    appsec_enabled: _t.Optional[str] = "false",
    remote_configuration_enabled: str = "true",
    iast_enabled: _t.Optional[str] = "false",
    tracer_enabled: _t.Optional[str] = "true",
    apm_tracing_enabled: str = "",
    token: str = "",
    app: str = "tests.appsec.integrations.fastapi_tests.app:app",
    env: _t.Optional[dict] = {},
    port: int = 8000,
    assert_debug: bool = False,
    manual_propagation_debug: bool = False,
    use_multiprocess: bool = False,
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
        use_multiprocess=use_multiprocess,
    )


def appsec_application_server(
    cmd: _t.Sequence[str],
    appsec_enabled: str = "true",
    remote_configuration_enabled: str = "true",
    iast_enabled: str = "false",
    tracer_enabled: str = "true",
    apm_tracing_enabled: str = "",
    token: str = "",
    env: _t.Optional[dict] = {},
    port: int = 8000,
    assert_debug: bool = False,
    manual_propagation_debug: bool = False,
    use_multiprocess: bool = False,
) -> _t.Iterator[_t.Tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
    if appsec_enabled:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    else:
        env["DD_APPSEC_ENABLED"] = ""

    if apm_tracing_enabled:
        env["DD_APM_TRACING_ENABLED"] = apm_tracing_enabled
    else:
        env["DD_APM_TRACING_ENABLED"] = ""

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
    else:
        env[IAST.ENV] = iast_enabled

    if tracer_enabled:
        env["DD_TRACE_ENABLED"] = tracer_enabled
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")
    env["FLASK_RUN_PORT"] = str(port)
    env["PYTHONFAULTHANDLER"] = "1"
    env["MALLOC_PERTURB_"] = "glibc.malloc.tcache_max=0"
    env["GLIBC_TUNABLES"] = "255"
    env["MALLOC_CHECK_"] = "3"

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

    if use_multiprocess:
        # Run the server command by replacing the child Python process with the target binary (exec),
        # ensuring signals/termination behave like the subprocess.Popen path.
        def _mp_target(_cmd: _t.List[str], _env: dict) -> None:
            """Child process entrypoint that prepares the session and execs the server command.

            This makes the child PID equal to the server PID, so signals from the parent terminate the server cleanly.
            """
            try:
                # Mirror start_new_session behavior
                if os.name == "posix":
                    try:
                        os.setsid()
                    except Exception:
                        pass
                # Apply optional resource/affinity limits
                preexec = _make_preexec()
                if preexec is not None:
                    try:
                        preexec()
                    except Exception:
                        pass
                # Replace the process image with the target command
                os.execvpe(_cmd[0], _cmd, _env)
            except Exception:
                # If exec fails for any reason, exit non-zero
                os._exit(1)

        # Build the environment for the child exec
        mp_env = dict(subprocess_kwargs["env"]) if "env" in subprocess_kwargs else os.environ.copy()
        server_process: _t.Union[subprocess.Popen, multiprocessing.Process]
        server_process = multiprocessing.Process(target=_mp_target, args=(cmd, mp_env), daemon=True)
        server_process.start()
    else:
        server_process = subprocess.Popen(cmd, **subprocess_kwargs)
    try:
        client = Client("http://0.0.0.0:%s" % port)

        try:
            print("Waiting for server to start...")
            print(f"* Command: {cmd}")
            print(f"* Environment {env}")
            print("* *****************************************")
            if use_multiprocess:
                # Socket-based readiness check similar to the provided fixture snippet
                max_attempts = 120
                attempt = 0
                while attempt < max_attempts:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2)
                            s.connect(("0.0.0.0", int(port)))
                            break
                    except (ConnectionRefusedError, OSError):
                        time.sleep(0.1)
                        attempt += 1
                else:
                    raise RetryError("Server failed to accept connections in time")
                print("Server started")
            else:
                client.wait(max_tries=120, delay=0.1, initial_wait=1.0)
                print("Server started")
        except RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (getattr(server_process, "stdout", None), getattr(server_process, "stderr", None))
            )
        except Exception:
            raise AssertionError(
                "Server FAILED, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (getattr(server_process, "stdout", None), getattr(server_process, "stderr", None))
            )

        # If we run a Gunicorn application, we want to get the child's pid, see test_flask_remoteconfig.py
        # Obtain child PID tree for gunicorn when possible
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
                % (getattr(server_process, "stdout", None), getattr(server_process, "stderr", None))
            )
    finally:
        try:
            if use_multiprocess:
                # server_process is a multiprocessing.Process that exec'd the server.
                # Send SIGTERM to the process group if possible, then ensure the process is stopped.
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
                except Exception:
                    pass
                try:
                    server_process.kill()
                except Exception:
                    pass
                server_process.join(timeout=5)
            else:
                os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
                server_process.terminate()
                server_process.wait()
                if (assert_debug and PYTHON_VERSION_INFO >= (3, 10)) and (
                    iast_enabled is not None and iast_enabled != "false"
                ):
                    process_output = server_process.stderr.read()
                    assert "Return from " in process_output
                    assert "Return value is tainted" in process_output
                    assert "Tainted arguments:" in process_output
        finally:
            pass


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
