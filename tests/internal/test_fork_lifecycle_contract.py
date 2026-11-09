"""Fork lifecycle contract shared by NativeRuntime and PeriodicService-based services.

Every service that owns background workers is expected to follow the same three phases around a
fork:

1. prepare: its workers are stopped before the fork,
2. resume parent: the forking process gets its workers back, on the same wrapper objects,
3. reset and restart child: the child resets the inherited state and restarts the workers of the
   wrapper objects it inherited.

The checks run against ``ForksafeAwakeablePeriodicService`` and against the native shared runtime
with the telemetry worker built on it. Each fork path runs once, in its own interpreter, through
``fork_lifecycle_scenarios.py``; the tests below assert separate fields of that report so that
each expected failure documents exactly one gap.

Fork paths: ``os.fork``, a raw ``fork()`` from libc that bypasses the Python fork hooks,
``subprocess`` with and without ``preexec_fn`` (and with vfork disabled), nested forks, failed
forks and concurrent forks from several threads. The Celery beat path that closes descriptors after
the fork lives in ``tests/contrib/celery/test_fork_lifecycle.py``.

Known limitation, asserted as current behavior: a raw native fork runs no Python hook, so a
PeriodicService cannot restart in that child. uWSGI forks natively but runs the Python child hooks
from its postfork hook (see ``ddtrace/internal/uwsgi.py``).

Compatibility boundary: ``tests/telemetry/test_telemetry.py::
test_rebuild_telemetry_worker_after_fork_fd_close`` closes every inherited descriptor right after
``os.fork`` returns, with no ddtrace hook around the close. Changes to the fork lifecycle must keep
that test passing.
"""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parent / "fork_lifecycle_scenarios.py"

PLANNED_NATIVE_RESTART = (
    "NativeRuntime abandons the inherited runtime after a Python-managed fork and rebuilds its "
    "workers; the planned change resets and restarts the inherited runtime and its workers in the "
    "child, like PeriodicService."
)
BUG_INHERITED_WORKER_HANGS = (
    "Known bug: once the child has rebuilt the shared runtime, the abandon flag is cleared and a "
    "native worker inherited through a Python-managed fork blocks forever on the forgotten runtime."
)
BUG_RAW_THEN_OS_FORK_HANGS = (
    "Known bug: a raw native fork leaves the parent's periodic threads registered although they "
    "did not survive, so a later os.fork in that child blocks in the before-fork hook joining them."
)
BUG_NESTED_RAW_THEN_OS_FORK_NATIVE = (
    "Known bug: after a raw native fork followed by os.fork, tracing and telemetry calls in the "
    "grandchild block on the shared runtime."
)

# Python-managed fork paths and raw native fork paths, by the kind of the innermost fork.
PYTHON_FORKS = ["os_fork", "os_fork_rebuild_first", "nested_os_os", "nested_libc_os_native_only", "threads_os_fork"]
RAW_FORKS = ["libc_fork", "nested_os_libc", "threads_libc_fork"]
EXEC_PATHS = ["subprocess_default", "subprocess_forced_fork", "subprocess_preexec_fn"]
FAILED_FORKS = ["failed_os_fork", "failed_libc_fork"]
FORKS = PYTHON_FORKS + RAW_FORKS + ["nested_libc_os"]
ALL = FORKS + EXEC_PATHS + FAILED_FORKS


def _run_scenario(name):
    env = dict(os.environ)
    env.update(
        DD_INSTRUMENTATION_TELEMETRY_ENABLED="true",
        # Nothing listens on this port, so every export fails fast instead of waiting on a peer.
        DD_TRACE_AGENT_URL="http://127.0.0.1:1",
        DD_REMOTE_CONFIGURATION_ENABLED="false",
        PYTHONWARNINGS="ignore::DeprecationWarning",
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), name], env=env, capture_output=True, timeout=240, check=False
        )
    except subprocess.TimeoutExpired as e:
        return {"error": "scenario timed out", "stderr": (e.stderr or b"").decode(errors="replace")}
    stderr = proc.stderr.decode(errors="replace")
    lines = proc.stdout.decode(errors="replace").strip().splitlines()
    try:
        report = json.loads(lines[-1])
    except (IndexError, ValueError):
        report = {"error": "no report (exit %d)" % proc.returncode}
    report["returncode"] = proc.returncode
    report["stderr"] = stderr
    return report


@pytest.fixture(scope="session")
def scenario_report(tmp_path_factory):
    """Run each scenario once per session, shared across xdist workers through a locked file cache."""
    root = tmp_path_factory.getbasetemp().parent / "fork_lifecycle_reports"
    root.mkdir(exist_ok=True)

    def get(name):
        path = root / (name + ".json")
        with open(root / (name + ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                path.write_text(json.dumps(_run_scenario(name)))
        report = json.loads(path.read_text())
        if "skip" in report:
            pytest.skip(report["skip"])
        assert "error" not in report, report
        return report

    return get


def _params(names, marks=None):
    marks = marks or {}
    return [pytest.param(n, marks=marks[n], id=n) if n in marks else pytest.param(n, id=n) for n in names]


def _xfail(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


@pytest.mark.parametrize("name", _params(ALL))
def test_process_is_safe(scenario_report, name):
    """The forking process completes, and no process prints a Rust panic."""
    report = scenario_report(name)
    assert report["returncode"] == 0, report["stderr"]
    assert "panic" not in report["stderr"].lower(), report["stderr"]


@pytest.mark.parametrize("name", _params(ALL))
def test_parent_resumes(scenario_report, name):
    """Resume parent: the forking process gets both kinds of service back on the same objects."""
    parent = scenario_report(name)["parent"]
    assert parent["periodic_resumed"], parent
    assert parent["native_flush"] == "ok", parent
    assert parent["worker_preserved"], parent
    assert parent["native_counts_match"], parent
    assert parent["native_runtime_preserved"], parent
    assert parent["fork_lock_released"] and parent["fork_lock_acquirable"], parent


@pytest.mark.parametrize("name", _params(FAILED_FORKS))
def test_failed_fork_reports_error(scenario_report, name):
    assert scenario_report(name)["parent"]["fork_failed"]


@pytest.mark.parametrize("name", _params(EXEC_PATHS))
def test_exec_children_exit_cleanly(scenario_report, name):
    child = scenario_report(name)["child"]
    assert child["exit_ok"] and child["count"] > 0, child


@pytest.mark.parametrize("name", _params(FORKS, {"nested_libc_os": _xfail(BUG_RAW_THEN_OS_FORK_HANGS)}))
def test_child_exits_cleanly(scenario_report, name):
    report = scenario_report(name)
    child = report["child"]
    assert "error" not in child and child.get("exit_ok"), child
    if "intermediate" in report:
        intermediate = report["intermediate"]
        assert intermediate["exit_ok"], intermediate
        # The middle process is itself a child: its own inherited worker is covered by the
        # child checks, so only require that its fork lock was released.
        assert intermediate["fork_lock_released"] and intermediate["fork_lock_acquirable"], intermediate


@pytest.mark.parametrize("name", _params(PYTHON_FORKS + RAW_FORKS))
def test_child_prepare_native(scenario_report, name):
    """Prepare: no native worker was running at the time of the fork, whatever the fork path."""
    child = scenario_report(name)["child"]
    assert child["prepare_native"], child


@pytest.mark.parametrize("name", _params(["os_fork", "os_fork_rebuild_first", "nested_os_os"]))
def test_child_prepare_periodic(scenario_report, name):
    """Prepare: the Python before-fork hooks stopped the service thread."""
    child = scenario_report(name)["child"]
    assert child["prepare_periodic"], child


@pytest.mark.parametrize("name", _params(["os_fork", "os_fork_rebuild_first", "nested_os_os"]))
def test_child_periodic_reset_and_restarted(scenario_report, name):
    """Reset and restart child: the inherited service object is reset and runs again."""
    child = scenario_report(name)["child"]
    assert child["periodic_reset"], child
    assert child["periodic_restarted"], child


@pytest.mark.parametrize("name", _params(["libc_fork"]))
def test_child_raw_fork_cannot_restart_periodic(scenario_report, name):
    """Known limitation: without Python hooks the service thread is neither stopped nor restarted."""
    child = scenario_report(name)["child"]
    assert not child["prepare_periodic"], child
    assert not child["periodic_restarted"], child


@pytest.mark.parametrize(
    "name",
    _params(
        PYTHON_FORKS + RAW_FORKS,
        {"nested_libc_os_native_only": _xfail(BUG_NESTED_RAW_THEN_OS_FORK_NATIVE)},
    ),
)
def test_child_functional(scenario_report, name):
    """The child can trace a span and flush telemetry without hanging."""
    child = scenario_report(name)["child"]
    assert child["functional"] == "ok", child


@pytest.mark.parametrize(
    "name",
    _params(
        PYTHON_FORKS + RAW_FORKS,
        {"nested_libc_os_native_only": _xfail(BUG_NESTED_RAW_THEN_OS_FORK_NATIVE)},
    ),
)
def test_child_restartable_native_workers_running(scenario_report, name):
    """Reset and restart child: every worker registered to restart on fork is running again."""
    child = scenario_report(name)["child"]
    assert child["native_restartable_running"], child


_INHERITED_MARKS = {
    "os_fork": _xfail(PLANNED_NATIVE_RESTART),
    "nested_os_os": _xfail(PLANNED_NATIVE_RESTART),
    # The middle process forked with os.fork, so the raw-fork grandchild inherits the abandon flag.
    "nested_os_libc": _xfail(PLANNED_NATIVE_RESTART),
    "nested_libc_os_native_only": _xfail(PLANNED_NATIVE_RESTART),
    "os_fork_rebuild_first": _xfail(BUG_INHERITED_WORKER_HANGS),
}


@pytest.mark.parametrize("name", _params(["os_fork", "os_fork_rebuild_first", "libc_fork", "nested_os_os", "nested_os_libc", "nested_libc_os_native_only", "threads_libc_fork"], _INHERITED_MARKS))
def test_child_inherited_native_worker_usable(scenario_report, name):
    """Reset and restart child: the inherited native worker object works in the child."""
    child = scenario_report(name)["child"]
    assert child["inherited_flush"] == "ok", child


@pytest.mark.parametrize(
    "name",
    _params(
        ["os_fork", "os_fork_rebuild_first", "libc_fork", "nested_os_os", "nested_os_libc", "nested_libc_os_native_only"],
        {
            "os_fork": _xfail(BUG_INHERITED_WORKER_HANGS),
            "os_fork_rebuild_first": _xfail(BUG_INHERITED_WORKER_HANGS),
            "nested_os_os": _xfail(BUG_INHERITED_WORKER_HANGS),
            "nested_os_libc": _xfail(BUG_INHERITED_WORKER_HANGS),
            "nested_libc_os_native_only": _xfail(PLANNED_NATIVE_RESTART),
        },
    ),
)
def test_child_inherited_native_worker_usable_after_child_use(scenario_report, name):
    """The inherited worker still works, or fails cleanly, after the child used the runtime."""
    child = scenario_report(name)["child"]
    assert child["inherited_flush_after_use"] == "ok", child


@pytest.mark.parametrize(
    "name",
    _params(
        ["os_fork", "libc_fork", "nested_os_os", "nested_os_libc"],
        {
            "os_fork": _xfail(PLANNED_NATIVE_RESTART),
            "nested_os_os": _xfail(PLANNED_NATIVE_RESTART),
            "nested_os_libc": _xfail(PLANNED_NATIVE_RESTART),
        },
    ),
)
def test_child_telemetry_worker_preserved(scenario_report, name):
    """Reset and restart child: telemetry keeps the worker object it had before the fork."""
    child = scenario_report(name)["child"]
    assert child["worker_preserved"], child
