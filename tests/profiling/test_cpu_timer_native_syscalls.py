import errno
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


CPU_TIMER_SKIP_REASON = "CPU timer profiler is enabled only on Linux Python 3.12+"
CPU_TIMER_SKIP = sys.platform != "linux" or sys.version_info < (3, 12)
REPO_ROOT = Path(__file__).parents[2]
HAZARD_APP = REPO_ROOT / "tests" / "profiling" / "cpu_timer_native_syscall_hazard_app.py"


def _run_hazard_app(tmp_path, scenario, *extra_args):
    env = os.environ.copy()
    env.update(
        {
            "DD_PROFILING_OUTPUT_PPROF": str(tmp_path / f"cpu-timer-native-syscall-{scenario}"),
            "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
            "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
            "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
        }
    )

    pythonpath = str(REPO_ROOT)
    if env.get("PYTHONPATH"):
        pythonpath += os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath

    command = [
        sys.executable,
        str(HAZARD_APP),
        "--scenario",
        scenario,
        "--build-dir",
        str(tmp_path / "native-build"),
    ]
    command.extend(extra_args)

    completed = subprocess.run(command, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed
    return json.loads(completed.stdout)


@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_does_not_arm_raw_pthread_that_never_enters_python(tmp_path):
    result = _run_hazard_app(tmp_path, "raw-pthread")

    assert result["pthread_rc"] == 0, result
    assert result["before"]["active"] is True, result
    assert result["after"]["active"] is True, result
    assert result["after"]["timer_syscall_failures"] == result["before"]["timer_syscall_failures"], result
    assert result["after"]["capture_failed_count"] == result["before"]["capture_failed_count"], result


@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
@pytest.mark.xfail(
    reason=(
        "Known limitation: the CPU timer has no .NET-style syscall shield. "
        "A Python-visible native function can unblock a pending timer SIGPROF "
        "inside ppoll and observe EINTR if it does not retry."
    )
)
def test_cpu_timer_raw_native_ppoll_without_eintr_retry_is_not_interrupted(tmp_path):
    result = _run_hazard_app(tmp_path, "ppoll")

    assert result["before"]["active"] is True, result
    assert result["after"]["active"] is True, result
    assert result["errno"] != errno.EINTR, result


# AIDEV-NOTE: Unlike ppoll, read/readv and nanosleep/clock_nanosleep take no signal
# mask, so there is no atomic unblock-inside-the-syscall race. A pending CPU timer
# SIGPROF is delivered when it is unblocked, before the syscall, and the syscall then
# blocks off-CPU where a per-thread CPU timer does not advance. These tests record that
# a CPU timer does not surface EINTR for these syscalls. If a kernel ever disproves
# this, convert the affected variant to an xfail like the ppoll case above.
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
@pytest.mark.parametrize("scenario", ["read", "readv"])
def test_cpu_timer_raw_native_read_without_eintr_retry_is_not_interrupted(tmp_path, scenario):
    result = _run_hazard_app(tmp_path, scenario)

    assert result["before"]["active"] is True, result
    assert result["after"]["active"] is True, result
    assert result["errno"] != errno.EINTR, result


@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
@pytest.mark.parametrize("scenario", ["nanosleep", "clock-nanosleep"])
def test_cpu_timer_raw_native_nanosleep_without_eintr_retry_is_not_interrupted(tmp_path, scenario):
    result = _run_hazard_app(tmp_path, scenario)

    assert result["before"]["active"] is True, result
    assert result["after"]["active"] is True, result
    assert result["errno"] != errno.EINTR, result
