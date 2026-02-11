"""Test that .gitlab/scripts/generate-core-backtraces.sh produces readable backtraces from core dumps.

This test intentionally crashes a subprocess to create a core file, then runs the
backtrace generation script and verifies the output contains resolved symbols.
"""

import os
import shutil
import signal
import subprocess
import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_generate_core_backtrace(tmp_path):
    """Spawn a child that segfaults, then verify the backtrace script produces readable output."""

    if not shutil.which("gdb"):
        pytest.skip("gdb is not installed")

    # Ensure core dumps are enabled
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    if soft == 0:
        pytest.skip("Core dumps are disabled (ulimit -c 0)")

    # Spawn a subprocess that segfaults. Using subprocess (not os.fork) so the
    # child gets its own AT_EXECFN pointing to the Python binary, not pytest.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import ctypes; ctypes.string_at(0)"],
        cwd=str(tmp_path),
    )
    proc.wait()
    assert proc.returncode == -signal.SIGSEGV, "Child should have been killed by SIGSEGV, got %d" % proc.returncode

    # Expect core.<pid> (matches CI testrunner where kernel.core_pattern = core.%p)
    core_file = str(tmp_path / ("core.%d" % proc.pid))
    if not os.path.exists(core_file):
        pytest.skip("No core file produced at %s (core_pattern may differ from core.%%p)" % core_file)

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
