# -*- encoding: utf-8 -*-
import os
import re
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.profiling.collector import pprof_utils


# DEV: gunicorn tests are hard to debug, so keeping these print statements for
# future debugging
DEBUG_PRINT = True


def debug_print(*args):
    if DEBUG_PRINT:
        print(*args)


# gunicorn is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def _run_gunicorn(*args):
    cmd = (
        [
            "ddtrace-run",
            "gunicorn",
            "--bind",
            "127.0.0.1:7644",
            "--worker-tmp-dir",
            "/dev/shm",
            "-c",
            os.path.dirname(__file__) + "/gunicorn.conf.py",
            "--chdir",
            os.path.dirname(__file__),
        ]
        + list(args)
        + ["tests.profiling.gunicorn-app:app"]
    )
    debug_print("Running command:", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@pytest.fixture
def gunicorn(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "1")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")

    yield _run_gunicorn


def _get_worker_pids(stdout):
    # type: (str) -> list[int]
    return [int(_) for _ in re.findall(r"Booting worker with pid: (\d+)", stdout)]


def _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args):
    # type: (...) -> None
    filename = str(tmp_path / "gunicorn.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("_DD_PROFILING_STACK_V2_ADAPTIVE_SAMPLING_ENABLED", "0")

    debug_print("Creating gunicorn workers")
    # DEV: We only start 1 worker to simplify the test
    proc = gunicorn("-w", "1", *args)
    # Wait for the workers to start
    time.sleep(5)

    if proc.poll() is not None:
        # Capture the actual error output before failing
        try:
            output = proc.stdout.read().decode()
            debug_print("Gunicorn failed to start. Output:")
            debug_print(output)
            debug_print("Return code:", proc.returncode)

            # If segfault (-11), look for and analyze core dump
            if proc.returncode == -11:
                debug_print("Segfault detected! Looking for core dump...")
                core_file = _find_and_analyze_core_dump(proc.pid)
                if core_file:
                    debug_print(f"Core dump analyzed: {core_file}")
                else:
                    debug_print("No core dump found - check ulimit -c and core_pattern settings")
                    # Show current settings for debugging
                    try:
                        result = subprocess.run(["ulimit", "-c"], capture_output=True, text=True, shell=True)
                        debug_print(f"Current ulimit -c: {result.stdout.strip()}")
                    except Exception:
                        pass
                    try:
                        with open("/proc/sys/kernel/core_pattern", "r") as f:
                            debug_print(f"Core pattern: {f.read().strip()}")
                    except Exception:
                        pass

        except Exception as e:
            debug_print("Failed to read output:", e)
        pytest.fail("Gunicorn failed to start")

    debug_print("Making request to gunicorn server")
    try:
        with urllib.request.urlopen("http://127.0.0.1:7644", timeout=5) as f:
            status_code = f.getcode()
            assert status_code == 200, status_code
            response = f.read().decode()
            debug_print(response)
    except Exception as e:
        proc.terminate()
        output = proc.stdout.read().decode()
        print(output)
        pytest.fail("Failed to make request to gunicorn server %s" % e)
    finally:
        # Need to terminate the process to get the output and release the port
        proc.terminate()

    debug_print("Reading gunicorn worker output to get PIDs")
    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)
    debug_print("Gunicorn worker PIDs: %s" % worker_pids)

    for line in output.splitlines():
        debug_print(line)

    assert len(worker_pids) == 1, output

    debug_print("Waiting for gunicorn process to terminate")
    try:
        assert proc.wait(timeout=5) == 0, output
    except subprocess.TimeoutExpired:
        pytest.fail("Failed to terminate gunicorn process ", output)
    assert "module 'threading' has no attribute '_active'" not in output, output

    for pid in worker_pids:
        debug_print("Reading pprof file with prefix %s.%d" % (filename, pid))
        profile = pprof_utils.parse_profile("%s.%d" % (filename, pid))
        # This returns a list of samples that have non-zero cpu-time
        samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
        assert len(samples) > 0

        # DEV: somehow the filename is reported as either __init__.py or gunicorn-app.py
        # when run on GitLab CI. We need to match either of these two.
        filename_regex = r"^(?:__init__\.py|gunicorn-app\.py)$"

        expected_location = pprof_utils.StackLocation(function_name="fib", filename=filename_regex, line_no=8)

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            # DEV: we expect multiple locations as fibonacci is recursive
            expected_sample=pprof_utils.StackEvent(locations=[expected_location, expected_location]),
        )


def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    args = ("-k", "gevent") if TESTING_GEVENT else tuple()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)


def _find_and_analyze_core_dump(proc_pid):
    """Find and analyze core dump files

    Since ulimit -c unlimited is already set in GitLab CI,
    core dumps should be automatically generated when segfaults occur.
    This function searches for and analyzes those core dumps using GDB.
    """
    import glob
    import os
    import time

    # Give some time for the core dump to be written
    debug_print("Waiting for core dump to be written...")
    time.sleep(2)

    # Show current working directory
    cwd = os.getcwd()
    debug_print(f"Current working directory: {cwd}")

    # Show directory contents to see what files are there
    files = os.listdir(cwd)
    debug_print(f"Files in current directory: {files}")

    # Also check /tmp directory
    tmp_files = os.listdir("/tmp")
    core_files_in_tmp = [f for f in tmp_files if f.startswith("core")]
    debug_print(f"Core files in /tmp: {core_files_in_tmp}")

    # Common core dump locations and patterns
    core_patterns = [
        "core",  # Default pattern from core_pattern
        "./core",  # Explicit current directory
        f"{cwd}/core",  # Full path to current directory
        f"{cwd}/tests/profiling_v2/core.*",  # core file in tests/profiling_v2/
        "/tmp/core*",
        "./core*",
        f"core.{proc_pid}",
        f"/tmp/core.{proc_pid}",
        f"core.*{proc_pid}*",
        "/tmp/core.*",
        "/cores/core*",
        "core.*",
    ]

    debug_print("Looking for core dumps...")

    for pattern in core_patterns:
        debug_print(f"Searching pattern: {pattern}")
        core_files = glob.glob(pattern)
        if core_files:
            # Sort by modification time, get the most recent
            core_files.sort(key=os.path.getmtime, reverse=True)
            core_file = core_files[0]

            debug_print(f"Found core dump: {core_file}")
            debug_print(f"Core file size: {os.path.getsize(core_file)} bytes")
            debug_print(f"Core file modification time: {os.path.getmtime(core_file)}")

            # Get backtrace from core dump
            try:
                # Try to find the exact executable path
                # The current Python interpreter is the most likely candidate
                current_python = sys.executable
                debug_print(f"Current Python executable: {current_python}")

                executable_candidates = [
                    current_python,  # The Python interpreter running this test
                    "gunicorn",
                ]

                for exe in executable_candidates:
                    try:
                        cmd = [
                            "gdb",
                            "-q",
                            "-batch",
                            "-ex",
                            "set auto-load safe-path /",
                            "-ex",
                            "bt full",
                            "-ex",
                            "q",
                            exe,
                            core_file,
                        ]

                        debug_print(f"Running GDB with executable: {exe}")
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                        if result.returncode == 0 and "Core was generated by" in result.stdout:
                            debug_print("=== CORE DUMP ANALYSIS ===")
                            debug_print(result.stdout)
                            if result.stderr:
                                debug_print("=== GDB STDERR ===")
                                debug_print(result.stderr)
                            return core_file
                        else:
                            debug_print(f"GDB failed with {exe}: {result.stderr}")
                    except Exception as e:
                        debug_print(f"Failed to run GDB with {exe}: {e}")
                        continue

            except Exception as e:
                debug_print(f"Failed to analyze core dump {core_file}: {e}")

            return core_file

    # If no core dump found with glob, try using find command
    debug_print("No core dumps found with glob, trying find command...")
    try:
        # Search for core files in common locations
        find_locations = ["/tmp", cwd, "/"]
        for location in find_locations:
            # Look for files named "core" or "core.NUMBER" but exclude .py, .pyc, etc.
            cmd = ["find", location, "-name", "core*", "-type", "f", "-mtime", "-1"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.stdout.strip():
                debug_print(f"Found files with 'core' in name in {location}:")
                debug_print(result.stdout)

                # Filter to find actual core dump files
                potential_cores = result.stdout.strip().split("\n")
                actual_core_files = []

                for file_path in potential_cores:
                    file_path = file_path.strip()
                    if not file_path:
                        continue

                    # Skip Python files, cache files, and other non-core-dump files
                    if (
                        file_path.endswith(".py")
                        or file_path.endswith(".pyc")
                        or file_path.endswith(".pyo")
                        or "pycache" in file_path
                        or "site-packages" in file_path
                        or file_path.endswith(".yaml")
                        or file_path.endswith(".so")
                    ):
                        continue

                    # Look for files that match core dump patterns
                    basename = os.path.basename(file_path)
                    if (
                        basename == "core"
                        or (basename.startswith("core.") and basename.split(".")[-1].isdigit())
                        or (basename.startswith("core-") and basename.split("-")[-1].isdigit())
                    ):
                        actual_core_files.append(file_path)

                if actual_core_files:
                    debug_print(f"Found actual core dump files: {actual_core_files}")
                    # Take the most recent one
                    core_file = max(actual_core_files, key=os.path.getmtime)
                    debug_print(f"Using most recent core file: {core_file}")
                    return core_file
                else:
                    debug_print("No actual core dump files found after filtering")
    except Exception as e:
        debug_print(f"Find command failed: {e}")

    debug_print("No core dumps found")
    return None
