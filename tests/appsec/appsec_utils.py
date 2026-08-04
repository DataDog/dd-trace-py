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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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


def _describe_server_output(server_process) -> str:
    stdout = getattr(server_process, "stdout", None)
    stderr = getattr(server_process, "stderr", None)
    if stdout is None and stderr is None:
        return "The server inherited the test runner's stdout/stderr; its output is in the surrounding log."
    return (
        "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
        "\n=== Captured STDERR ===\n%s=== End of captured STDERR ===" % (stdout, stderr)
    )


def _dump_server_greenlets(client) -> None:
    """Ask a hung gevent server to describe its own greenlets, over a second HTTP request.

    This works precisely because the hang does not stop the hub: on a real reproduction the main
    thread was in gevent's hub run(), still turning the event loop, so a second connection is still
    accepted and served while /shutdown is stuck. That makes it the only way to see the stack of the
    suspended greenlet, which faulthandler cannot reach.

    Best effort by design. If the hub is wedged after all, this request times out too, and that
    negative result is itself worth printing.
    """
    if os.environ.get("_DD_TEST_ABORT_HUNG_SERVER", "") != "1":
        return
    print("=== Asking the hung server for its greenlet stacks ===", flush=True)
    try:
        response = client.get_ignored("/debug/greenlets", timeout=10)
    except Exception as exc:
        print("=== The hung server did not answer /debug/greenlets either: %r ===" % (exc,), flush=True)
        print("=== That means the hub itself is not turning, not just one greenlet ===", flush=True)
        return
    print("=== /debug/greenlets responded %s ===" % response.status_code, flush=True)
    print(response.text, flush=True)


def _describe_core_dump_environment() -> None:
    """Print why a core dump would or would not appear, so a missing core is never a mystery.

    A reproduction produced faulthandler stacks but no core at all, and the artifact upload then had
    nothing to pick up. core_pattern is host-wide and a container cannot change it, so it has to be
    read rather than assumed.
    """
    try:
        with open("/proc/sys/kernel/core_pattern") as core_pattern:
            print("core_pattern: %r" % core_pattern.read().strip(), flush=True)
    except Exception as exc:
        print("core_pattern: unreadable (%r)" % (exc,), flush=True)
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        print("RLIMIT_CORE: soft=%s hard=%s" % (soft, hard), flush=True)
    except Exception as exc:
        print("RLIMIT_CORE: unreadable (%r)" % (exc,), flush=True)


def _dump_hung_server(server_process, known_worker_pids=()) -> None:
    """Dump every stack of a server that would not shut down, so the hang can be diagnosed.

    Opt-in through _DD_TEST_ABORT_HUNG_SERVER because it is expensive. The server runs with
    PYTHONFAULTHANDLER=1, so a fatal signal makes it print every thread's Python stack to stderr and
    leave a core file behind for the native backtrace — the only way to see the Rust/Tokio threads.

    The signal has to be SIGSEGV, not SIGABRT. Gunicorn's worker installs its own Python-level
    handle_abort over faulthandler's SIGABRT handler (gunicorn/workers/base.py init_signals), and
    that handler just calls sys.exit(1): the worker dies quietly with no stacks and no core, which is
    what CI showed. Gunicorn does not touch SIGSEGV, so faulthandler still services it.

    Attaching gdb to the live process is not an option either: kernel.yama.ptrace_scope is 1 on the
    runners and gdb would be a sibling of the target, not an ancestor.

    What this can and cannot show, measured on a real reproduction (job 1921342140): faulthandler
    dumps one stack per OS thread, and during this hang the main thread is sitting in the gevent
    hub's run(). The greenlet that was serving /shutdown is suspended, so it appears nowhere. Use
    _dump_server_greenlets for that side; this function is for the OS threads, including the ones
    with no Python frame at all, which are only identifiable from the native backtrace.

    Only the workers are signalled; killing the arbiter would reap them mid-dump.
    """
    if os.environ.get("_DD_TEST_ABORT_HUNG_SERVER", "") != "1":
        return
    try:
        parent = psutil.Process(server_process.pid)
        workers = [proc for proc in parent.children(recursive=True) if proc.is_running()]
    except Exception:
        workers = []
    if not workers:
        # The arbiter may already have reaped and replaced them; fall back to the pids seen at startup.
        for pid in known_worker_pids:
            try:
                workers.append(psutil.Process(pid))
            except Exception:
                pass

    aborted = []
    for proc in workers:
        print("=== SIGSEGV on hung worker %s (faulthandler python stacks follow) ===" % proc.pid, flush=True)
        try:
            cwd = proc.cwd()
        except Exception:
            cwd = os.getcwd()
        try:
            proc.send_signal(signal.SIGSEGV)
        except Exception:
            continue
        aborted.append((proc.pid, cwd))
    # Let the faulthandler output reach stderr, then wait for the kernel to finish writing. A worker
    # with the native extensions loaded dumps hundreds of MB, so a fixed sleep can easily glob a core
    # that is still growing and hand gdb a truncated file.
    time.sleep(5)
    _wait_for_cores(aborted)
    for pid, _ in aborted:
        try:
            if psutil.Process(pid).is_running():
                # Nothing dumped, so record that rather than let the caller assume the probe worked.
                print("=== Worker %s survived SIGSEGV; no stacks were produced ===" % pid, flush=True)
        except Exception:
            pass
    _collect_cores(aborted)


def _find_core(pid, cwd):
    """Return the path of the core the given worker left in cwd, or None.

    core_pattern is core.%p on some hosts and a bare "core" on others, so both names are candidates.
    """
    for name in ("core.%d" % pid, "core"):
        candidate = os.path.join(cwd, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _wait_for_cores(aborted, timeout=180.0) -> None:
    """Block until every core stops growing, so gdb is never handed a half-written file.

    Size stability is the only signal available: the kernel gives no notification that a dump is
    complete, and the file appears at full inode size immediately on some filesystems.
    """
    deadline = time.time() + timeout
    for pid, cwd in aborted:
        previous = -1
        stable_for = 0
        while time.time() < deadline:
            path = _find_core(pid, cwd)
            if path is None:
                time.sleep(1)
                continue
            try:
                current = os.path.getsize(path)
            except OSError:
                time.sleep(1)
                continue
            if current == previous and current > 0:
                stable_for += 1
                if stable_for >= 3:
                    break
            else:
                stable_for = 0
            previous = current
            time.sleep(1)
        path = _find_core(pid, cwd)
        if path is None:
            print("=== No core appeared for worker %s within %.0fs ===" % (pid, timeout), flush=True)
        else:
            print("=== Core for worker %s settled at %d bytes ===" % (pid, os.path.getsize(path)), flush=True)


def _collect_cores(aborted) -> None:
    """Move the cores the aborted workers left behind where .gitlab/scripts/generate-core-backtraces.sh looks.

    That script globs core.* in CI_PROJECT_DIR, which assumes kernel.core_pattern = core.%p. Some
    hosts use a bare "core" instead, and the worker's cwd is not necessarily the project directory.
    """
    target_dir = os.environ.get("CI_PROJECT_DIR") or os.getcwd()
    for pid, cwd in aborted:
        source = _find_core(pid, cwd)
        if source is None:
            print("No core dump found for worker %s in %s" % (pid, cwd), flush=True)
            continue
        destination = os.path.join(target_dir, "core.%d" % pid)
        try:
            if source != destination:
                os.replace(source, destination)
            print("Core dump of worker %s available at %s" % (pid, destination), flush=True)
        except Exception as exc:
            print("Could not move core dump %s: %r" % (source, exc), flush=True)


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
) -> _t.Iterator[tuple[_t.Union[subprocess.Popen, multiprocessing.Process], Client, _t.Optional[int]]]:
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
        except RetryError as exc:
            raise AssertionError("Server failed to start. %s" % _describe_server_output(server_process)) from exc
        except Exception as exc:
            raise AssertionError("Server FAILED: %r\n%s" % (exc, _describe_server_output(server_process))) from exc

        # If we run a Gunicorn application, we want to get the child's pid, see test_flask_remoteconfig.py
        # Obtain child PID tree for gunicorn when possible
        parent = psutil.Process(server_process.pid)
        children = parent.children(recursive=True)

        worker_pids = [child.pid for child in children]

        yield server_process, client, (children[1].pid if len(children) > 1 else None)
        try:
            client.get_ignored("/shutdown", timeout=10)
        except ConnectionError:
            pass
        except Exception as exc:
            # Order matters: the greenlet dump needs a live worker, and the signal below kills it.
            _dump_server_greenlets(client)
            _describe_core_dump_environment()
            _dump_hung_server(server_process, worker_pids)
            raise AssertionError(
                "The application server did not answer /shutdown: %r\n%s"
                % (exc, _describe_server_output(server_process))
            ) from exc
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
                try:
                    _, stderr_output = server_process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    server_process.kill()
                    _, stderr_output = server_process.communicate()
                if (assert_debug and PYTHON_VERSION_INFO >= (3, 10)) and (
                    iast_enabled is not None and iast_enabled != "false"
                ):
                    assert "Return from " in stderr_output
                    assert "Return value is tainted" in stderr_output
                    assert "Tainted arguments:" in stderr_output
        finally:
            pass


def _mp_target(_cmd: list[str], _env: dict) -> None:
    """Child process entrypoint that prepares the session and execs the server command.

    This makes the child PID equal to the server PID, so signals from the parent terminate the server cleanly.
    This function must be at module level to be picklable for multiprocessing on Python 3.14+.
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
