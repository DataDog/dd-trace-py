from importlib.metadata import version
import os
import pathlib
import re
import signal
import subprocess
from subprocess import TimeoutExpired
import sys
import time
from typing import IO
from typing import TYPE_CHECKING
from typing import Callable
from typing import Generator
from typing import List
from typing import Optional

import pytest

from tests.contrib.uwsgi import run_uwsgi
from tests.profiling.collector import pprof_utils


if TYPE_CHECKING:
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]


# uwsgi is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)
THREADS_MSG = (
    "ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true, or a positive "
    "number of threads must be set"
)

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")


@pytest.fixture
def uwsgi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> Generator[Callable[..., subprocess.Popen[str]], None, None]:
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = str(tmp_path / "uwsgi.sock")

    cmd = [
        "uwsgi",
        "--need-app",
        "--die-on-term",
        "--socket",
        socket_name,
        "--wsgi-file",
        uwsgi_app,
        "--import",
        "ddtrace.auto",
    ]

    try:
        yield run_uwsgi(cmd)
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi: Callable[..., subprocess.Popen[str]]) -> None:
    proc = uwsgi()
    try:
        stdout, _ = proc.communicate(timeout=1)
    except TimeoutExpired:
        proc.terminate()
        stdout, _ = proc.communicate()
    assert THREADS_MSG in stdout


def test_uwsgi_threads_number_set(uwsgi: Callable[..., subprocess.Popen[str]]) -> None:
    proc = uwsgi("--threads", "1")
    try:
        stdout, _ = proc.communicate(timeout=1)
    except TimeoutExpired:
        proc.terminate()
        stdout, _ = proc.communicate()
    assert THREADS_MSG not in stdout


def test_uwsgi_threads_enabled(
    uwsgi: Callable[..., subprocess.Popen[str]], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads")
    worker_pids = _get_worker_pids(proc.stdout, 1)
    # Give some time to the process to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() != 0
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}")
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary(
    uwsgi: Callable[..., subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = uwsgi("--enable-threads", "--processes", "2")
    try:
        stdout, _ = proc.communicate(timeout=3)
    except TimeoutExpired:
        # proc.terminate()
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait()
        assert proc.stdout is not None
        stdout, _ = proc.stdout.read(1), None
    assert (
        "ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout: Optional[IO[str]], num_worker: int, num_app_started: int = 1) -> List[int]:
    worker_pids = []
    started = 0
    while True:
        assert stdout is not None
        line = stdout.readline().strip()
        if not line:
            break
        elif "WSGI app 0 (mountpoint='') ready" in line:
            started += 1
        else:
            m: Optional[re.Match[str]] = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line)
            if m:
                worker_pids.append(int(m.group(1)))

        if len(worker_pids) == num_worker and num_app_started == started:
            break

    return worker_pids


def _wait_for_profile_samples(
    filename_prefix: str, pid: int, value_type: str, timeout: float = 10.0, interval: float = 0.1
) -> List[pprof_pb2.Sample]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            profile = pprof_utils.parse_newest_profile(
                f"{filename_prefix}.{pid}",
                assert_samples=False,
                allow_penultimate=True,
            )
        except (IndexError, FileNotFoundError):
            time.sleep(interval)
            continue

        samples = pprof_utils.get_samples_with_value_type(profile, value_type)
        if samples:
            return samples
        time.sleep(interval)

    assert False, f"Timed out waiting for {value_type} samples for pid {pid}"


def test_uwsgi_threads_processes_primary(
    uwsgi: Callable[..., subprocess.Popen[str]], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--py-call-uwsgi-fork-hooks", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    proc.wait()
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}")
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_primary_lazy_apps(
    uwsgi: Callable[..., subprocess.Popen[str]], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup and output a profile
    time.sleep(3)
    proc.terminate()
    proc.wait()
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}")
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary_lazy_apps(
    uwsgi: Callable[..., subprocess.Popen[str]], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    assert len(worker_pids) == 2

    # Give some time to child to actually startup before terminating the master
    time.sleep(3)

    # Send Ctrl+C to the process group
    # os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    # proc.wait()

    # for pid in worker_pids:
    #     utils.check_pprof_file(f"{filename}.{pid}")

    # Kill master process
    parent_pid = worker_pids[0]
    os.kill(parent_pid, signal.SIGTERM)

    # Wait for master to exit
    res_pid, res_status = os.waitpid(parent_pid, 0)
    print("")
    print(f"INFO: Master process {parent_pid} exited with status {res_status} and pid {res_pid}")

    # Attempt to kill worker proc once
    worker_pid: int = worker_pids[1]
    print(f"DEBUG: Checking worker {worker_pid} status after master exit:")
    try:
        os.kill(worker_pid, 0)
        print(f"WARNING: Worker {worker_pid} is a zombie (will be cleaned up by init).")

        os.kill(worker_pid, signal.SIGKILL)
        print(f"WARNING: Worker {worker_pid} could not be killed with SIGKILL (will be cleaned up by init).")
    except OSError:
        print(f"INFO: Worker {worker_pid} was successfully killed.")

    for pid in worker_pids:
        _wait_for_profile_samples(filename, pid, "wall-time")


@pytest.mark.parametrize("lazy_flag", ["--lazy-apps", "--lazy"])
@pytest.mark.skipif(
    tuple(int(x) for x in version("uwsgi").split(".")) >= (2, 0, 30),
    reason="uwsgi>=2.0.30 does not require --skip-atexit",
)
def test_uwsgi_require_skip_atexit_when_lazy_with_master(
    uwsgi: Callable[..., subprocess.Popen[str]], lazy_flag: str
) -> None:
    expected_warning = "ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"

    proc = uwsgi("--enable-threads", "--master", "--processes", "2", lazy_flag)
    time.sleep(1)
    proc.terminate()
    stdout, _ = proc.communicate()
    assert expected_warning in stdout


@pytest.mark.parametrize("lazy_flag", ["--lazy-apps", "--lazy"])
@pytest.mark.skipif(
    tuple(int(x) for x in version("uwsgi").split(".")) >= (2, 0, 30),
    reason="uwsgi>=2.0.30 does not require --skip-atexit",
)
def test_uwsgi_require_skip_atexit_when_lazy_without_master(
    uwsgi: Callable[..., subprocess.Popen[str]], lazy_flag: str
) -> None:
    expected_warning = "ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"
    num_workers = 2
    proc = uwsgi("--enable-threads", "--processes", str(num_workers), lazy_flag)

    worker_pids: List[int] = []
    logged_warning = 0
    while True:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if line == "":
            break
        if expected_warning in line:
            logged_warning += 1
        else:
            m: Optional[re.Match[str]] = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line)
            if m:
                worker_pids.append(int(m.group(1)))

        if logged_warning == num_workers:
            break

    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
