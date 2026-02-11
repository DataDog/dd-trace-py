"""Test that .gitlab/scripts/generate-core-backtraces.sh produces readable backtraces from core dumps.

This test intentionally crashes a subprocess to create a core file, then runs the
backtrace generation script and verifies the output contains resolved symbols.
"""

import ctypes
import glob
import os
import signal
import subprocess
import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_generate_core_backtrace(tmp_path):
    """Fork a child that segfaults, then verify the backtrace script produces readable output."""

    # Ensure core dumps are enabled
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    if soft == 0:
        pytest.skip("Core dumps are disabled (ulimit -c 0)")

    # Check that the core pattern writes to cwd (core.%p or just core)
    try:
        with open("/proc/sys/kernel/core_pattern", "r") as f:
            core_pattern = f.read().strip()
    except FileNotFoundError:
        pytest.skip("Cannot read /proc/sys/kernel/core_pattern")

    if core_pattern.startswith("|") or "/" in core_pattern:
        pytest.skip("Core pattern pipes to a handler or uses an absolute path: %s" % core_pattern)

    # Fork a child that will segfault
    pid = os.fork()
    if pid == 0:
        # Child: change to tmp_path so core dumps land there
        os.chdir(str(tmp_path))
        ctypes.string_at(0)  # SIGSEGV
        os._exit(1)  # unreachable

    # Parent: wait for child
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status), "Child should have been killed by a signal"
    assert os.WTERMSIG(status) == signal.SIGSEGV, "Child should have received SIGSEGV"

    # Find the core file
    core_files = glob.glob(str(tmp_path / "core.*"))
    assert len(core_files) == 1, "Expected exactly one core file in %s, found: %s" % (tmp_path, core_files)
    core_file = core_files[0]

    # Run the backtrace generation script
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(project_root, ".gitlab", "scripts", "generate-core-backtraces.sh")

    result = subprocess.run(
        ["bash", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    # The script should have created a .bt.txt file
    bt_file = core_file + ".bt.txt"
    assert os.path.exists(bt_file), "Backtrace file %s was not created. Script stdout: %s, stderr: %s" % (
        bt_file,
        result.stdout,
        result.stderr,
    )

    with open(bt_file, "r") as f:
        bt_content = f.read()

    # Verify the backtrace contains resolved symbols, not just ?? addresses
    assert "string_at" in bt_content, (
        "Backtrace should contain 'string_at' from the ctypes crash.\nBacktrace content:\n%s" % bt_content
    )
    assert "Thread" in bt_content or "LWP" in bt_content, (
        "Backtrace should contain thread information.\nBacktrace content:\n%s" % bt_content
    )
