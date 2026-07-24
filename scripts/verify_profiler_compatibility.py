#!/usr/bin/env python
"""
Verify that the ddtrace profiler works correctly on any Python version.

Collects real profiler samples and validates them — suitable for post-install
smoke tests and new Python version compatibility checks. Run this on a known-good
version to establish a baseline, then on a new version to compare.

Usage:
    # Test current interpreter
    python scripts/verify_profiler_compatibility.py

    # Test a specific pyenv-installed version
    python scripts/verify_profiler_compatibility.py --python 3.15.0a7

    # Import/guard checks only — no C++ extensions required
    python scripts/verify_profiler_compatibility.py --quick

    # Save current results as a baseline for this Python MAJOR.MINOR
    python scripts/verify_profiler_compatibility.py --baseline

    # Compare current Python results against a saved baseline
    python scripts/verify_profiler_compatibility.py --compare
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import TextIO


if TYPE_CHECKING:
    import asyncio

    from tests.profiling.collector import pprof_pb2


_REPO_ROOT: Path = Path(__file__).parent.parent
_BASELINE_FILE: Path = _REPO_ROOT / "scripts" / "profiles" / "compatibility_baselines.json"

# Names used for asyncio tasks in the profiler sample collection suite.
# These must be unique strings that won't appear in any other samples.
_ASYNCIO_TASK_NAMES: list[str] = ["compat-task-0", "compat-task-1", "compat-task-2"]
_PROFILER_RUN_SECONDS: float = 5.0
_MIN_WALL_TIME_SAMPLES: int = 2


# =============================================================================
# SUBPROCESS MODE
# Spawned by the orchestrator under the target Python. Outputs JSON to stdout.
# All diagnostic output goes to stderr.
# =============================================================================


def _suite_asyncio_guards() -> dict[str, Any]:
    """Check that _asyncio.py import guards run without error.

    This is the "import smoke test" — it verifies that:
      - _asyncio.py imports cleanly on the current Python version
      - The ModuleWatchdog callback fires when asyncio is imported
      - The hasattr guards for _scheduled_tasks, _GatheringFuture, _wait, etc.
        don't raise on this version
      - The asyncio policy hook path (set_event_loop wrapping) doesn't crash
    """
    # Import _asyncio BEFORE asyncio to ensure the ModuleWatchdog callback is
    # registered first. The callback fires when asyncio is subsequently imported.
    # Importing asyncio triggers the ModuleWatchdog callback, which runs
    # _call_init_asyncio() — exercising all the hasattr guards.
    import asyncio

    import ddtrace.profiling._asyncio as _asyncio_mod

    if not _asyncio_mod.ASYNCIO_IMPORTED:
        return {"passed": False, "error": "ASYNCIO_IMPORTED flag not set after asyncio import"}

    # Verify globals were replaced with real asyncio functions (not the no-op stubs)
    if _asyncio_mod.current_task is not asyncio.current_task:
        return {
            "passed": False,
            "error": "current_task not patched to asyncio.current_task — asyncio module watchdog may not have fired",
        }

    # Exercise the policy hook: asyncio.set_event_loop() triggers
    # stack.track_asyncio_loop() via the BaseDefaultEventLoopPolicy wrapper.
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    return {"passed": True}


def _suite_profiler_samples(tmpdir: str) -> dict[str, Any]:
    """Run the stack profiler with named asyncio tasks and validate pprof output.

    Checks:
      - ddup and stack C++ extensions are available
      - The profiler collects at least _MIN_WALL_TIME_SAMPLES wall-time samples
      - asyncio task names appear in the profiler output
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack as _stack_ext
    from ddtrace.profiling.collector import stack as stack_collector

    if not ddup.is_available:
        return {"passed": False, "skipped": True, "reason": f"ddup unavailable: {ddup.failure_msg}"}
    if not _stack_ext.is_available:
        return {"passed": False, "skipped": True, "reason": f"stack unavailable: {_stack_ext.failure_msg}"}

    # Ensure the asyncio watchdog is registered before any loop is created.
    # (It may already be imported, but importing it again is a no-op.)
    import ddtrace.profiling._asyncio  # noqa: F401

    pprof_prefix: str = os.path.join(tmpdir, "compat")
    output_filename: str = pprof_prefix + "." + str(os.getpid())

    ddup.config(
        env="test",
        service="verify-profiler-compatibility",
        version="0",
        output_filename=pprof_prefix,
    )
    ddup.start()

    async def _workload() -> None:
        end: float = time.monotonic() + _PROFILER_RUN_SECONDS
        while time.monotonic() < end:
            await asyncio.sleep(0.05)

    with stack_collector.StackCollector():
        loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                asyncio.gather(*[loop.create_task(_workload(), name=n) for n in _ASYNCIO_TASK_NAMES])
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    ddup.upload()

    result: dict[str, Any] = {
        "passed": False,
        "pprof_written": False,
        "wall_time_samples": 0,
        "asyncio_task_samples": 0,
        "asyncio_task_names_seen": [],
    }

    # Try to parse the pprof. Requires zstandard + google.protobuf.
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        from tests.profiling.collector import pprof_utils

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
        result["pprof_written"] = True

        wall_samples: list[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        task_samples: list[pprof_pb2.Sample] = pprof_utils.get_samples_with_label_key(profile, "task name")

        result["wall_time_samples"] = len(wall_samples)
        result["asyncio_task_samples"] = len(task_samples)

        # Extract the actual task name strings from the pprof string table
        names_seen: set[str] = set()
        for sample in task_samples:
            label: pprof_pb2.Label | None = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
            if label is not None:
                names_seen.add(profile.string_table[label.str])
        result["asyncio_task_names_seen"] = sorted(names_seen)

        expected: set[str] = set(_ASYNCIO_TASK_NAMES)
        if result["wall_time_samples"] < _MIN_WALL_TIME_SAMPLES:
            result["error"] = (
                f"Too few wall-time samples: {result['wall_time_samples']} < {_MIN_WALL_TIME_SAMPLES}. "
                "The profiler may not have started correctly."
            )
        elif result["asyncio_task_samples"] == 0:
            result["error"] = (
                "No 'task name' labels in any sample. "
                "asyncio task tracking is not working — check stack.init_asyncio() and _asyncio.py."
            )
        elif not names_seen & expected:
            result["error"] = (
                f"Expected to see at least one of {sorted(expected)}, "
                f"but got: {sorted(names_seen) or '(none)'}. "
                "Task names are not being attributed to profiler samples."
            )
        else:
            result["passed"] = True

    except FileNotFoundError as exc:
        result["error"] = f"No pprof file written to {output_filename}.*: {exc}"
    except ImportError as exc:
        # zstandard or protobuf not installed — degrade to file-existence check
        import glob as _glob

        files: list[str] = _glob.glob(pprof_prefix + "*.pprof")
        result["pprof_written"] = bool(files)
        result["passed"] = result["pprof_written"]
        result["pprof_parse_skipped"] = True
        result["pprof_parse_skip_reason"] = (
            f"{exc}. Install zstandard and protobuf in the test venv for full validation."
        )

    return result


def _run_subprocess(quick: bool) -> None:
    """Entry point for subprocess mode. Outputs JSON to stdout."""
    results: dict[str, Any] = {
        "python_version": sys.version,
        "python_hexversion": hex(sys.hexversion),
    }

    # Suite 1: asyncio guards — always run, no C++ required
    try:
        results["asyncio_guards"] = _suite_asyncio_guards()
    except Exception as exc:
        results["asyncio_guards"] = {"passed": False, "error": str(exc)}

    if not quick:
        with tempfile.TemporaryDirectory(prefix="ddtrace-compat-") as tmpdir:
            try:
                results["profiler_samples"] = _suite_profiler_samples(tmpdir)
            except Exception as exc:
                results["profiler_samples"] = {"passed": False, "error": str(exc)}

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")


# =============================================================================
# ORCHESTRATOR MODE
# Finds the target Python, spawns a subprocess, formats the report.
# =============================================================================


def _find_python(version: str | None) -> str:
    """Return the path to the Python executable for the given version string.

    Accepts:
      None          → current interpreter
      "3.15"        → tries python3.15, then ~/.pyenv/versions/3.15.*/bin/python3
      "3.15.0a7"    → tries ~/.pyenv/versions/3.15.0a7/bin/python3, then python3.15
      "/path/to/py" → used as-is
    """
    if version is None:
        return sys.executable

    # Absolute path
    if os.sep in version:
        if not os.path.isfile(version):
            raise SystemExit(f"Python not found at: {version}")
        return version

    # Try exact pyenv path first (handles pre-releases like 3.15.0a7)
    pyenv_root: str = os.path.expanduser("~/.pyenv/versions")
    pyenv_exact: str = os.path.join(pyenv_root, version, "bin", "python3")
    if os.path.isfile(pyenv_exact):
        return pyenv_exact

    # Try python3.X in PATH (handles short versions like "3.15")
    short: list[str] = version.split(".")
    short_name: str = ""
    if len(short) >= 2:
        short_name = f"python{short[0]}.{short[1]}"
        found: str | None = shutil.which(short_name)
        if found:
            return found

    # Try pyenv glob for partial versions (e.g. "3.15" matches "3.15.0a7")
    import glob as _glob

    matches: list[str] = _glob.glob(os.path.join(pyenv_root, version + "*", "bin", "python3"))
    if matches:
        matches.sort()
        return matches[-1]

    raise SystemExit(
        f"Could not find Python {version!r}.\n"
        f"  Tried: {pyenv_exact}, {short_name if len(short) >= 2 else '(n/a)'}, PATH\n"
        f"  Install with: pyenv install {version}"
    )


def _load_baselines() -> dict[str, Any]:
    if not _BASELINE_FILE.exists():
        return {}
    f: TextIO
    with open(_BASELINE_FILE) as f:
        data: object = json.load(f)
    if not isinstance(data, dict):
        return {}
    return data


def _save_baselines(baselines: dict[str, Any]) -> None:
    _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    f: TextIO
    with open(_BASELINE_FILE, "w") as f:
        json.dump(baselines, f, indent=2)
        f.write("\n")


def _baseline_key(python_exe: str) -> str:
    """Return MAJOR.MINOR for the given Python executable (e.g. '3.15')."""
    out: str = subprocess.check_output(  # nosec B603
        [python_exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        text=True,
    ).strip()
    return out


def _format_result(name: str, result: dict[str, Any], width: int = 22) -> str:
    label: str = f"  {name:<{width}}"
    if result.get("skipped"):
        reason: str = result.get("reason", "no reason given")
        return f"{label}SKIP  ({reason})"
    if result.get("passed"):
        extras: list[str] = []
        if "wall_time_samples" in result:
            extras.append(f"{result['wall_time_samples']} wall-time samples")
        if "asyncio_task_names_seen" in result and result["asyncio_task_names_seen"]:
            extras.append("tasks: " + ", ".join(result["asyncio_task_names_seen"]))
        if result.get("pprof_parse_skipped"):
            extras.append("pprof content not validated (missing zstandard/protobuf)")
        suffix: str = f"  ({', '.join(extras)})" if extras else ""
        return f"{label}PASS{suffix}"
    err: str = result.get("error", "unknown error")
    return f"{label}FAIL\n    {err}"


def _compare_with_baseline(results: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return a list of comparison failure messages (empty = all OK)."""
    failures: list[str] = []

    for suite in ("asyncio_guards", "profiler_samples"):
        cur: dict[str, Any] = results.get(suite, {})
        ref: dict[str, Any] = baseline.get(suite, {})
        if not ref:
            continue  # baseline doesn't have this suite — skip

        cur_passed: bool = cur.get("passed", False)
        ref_passed: bool = ref.get("passed", True)  # assume baseline was passing

        if ref_passed and not cur_passed:
            failures.append(f"{suite}: was PASS in baseline, now FAIL — {cur.get('error', '?')}")

        # Check sample count regression
        if "wall_time_samples" in ref and "wall_time_samples" in cur:
            if cur["wall_time_samples"] < ref.get("min_wall_time_samples", _MIN_WALL_TIME_SAMPLES):
                failures.append(
                    f"{suite}: wall_time_samples dropped: {cur['wall_time_samples']} "
                    f"< baseline minimum {ref.get('min_wall_time_samples', _MIN_WALL_TIME_SAMPLES)}"
                )

        # Check task names
        if "asyncio_task_names_seen" in ref:
            expected: set[str] = set(ref["asyncio_task_names_seen"])
            got: set[str] = set(cur.get("asyncio_task_names_seen", []))
            missing: set[str] = expected - got
            if missing:
                failures.append(f"{suite}: task names missing from samples: {sorted(missing)}")

    return failures


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Verify ddtrace profiler compatibility on any Python version.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--python",
        metavar="VERSION",
        help="Python version or path to test (e.g. 3.15.0a7, /usr/bin/python3). Default: current interpreter.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run import/guard checks only. No C++ extensions required.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Save results as the baseline for this Python MAJOR.MINOR.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare results against the saved baseline and fail if they regress.",
    )
    # Internal flag — not for direct use
    parser.add_argument("--subprocess", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--subprocess-quick", action="store_true", help=argparse.SUPPRESS)

    args: argparse.Namespace = parser.parse_args()

    # --- Subprocess mode ---
    if args.subprocess:
        _run_subprocess(quick=args.subprocess_quick)
        return

    # --- Orchestrator mode ---
    python_exe: str = _find_python(args.python)

    version_str: str = (
        subprocess.check_output(  # nosec B603
            [python_exe, "-c", "import sys; print(sys.version)"],
            text=True,
        )
        .strip()
        .splitlines()[0]
    )

    print(f"\n=== Profiler compatibility: Python {version_str} ===")
    if args.quick:
        print("  (quick mode — import checks only)")

    cmd: list[str] = [python_exe, __file__, "--subprocess"]
    if args.quick:
        cmd.append("--subprocess-quick")

    proc: subprocess.CompletedProcess[str] = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603

    if proc.returncode != 0 and not proc.stdout.strip():
        print(f"\nSubprocess crashed (exit {proc.returncode}):")
        print(proc.stderr or "(no stderr)")
        raise SystemExit(1)

    try:
        results: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"\nCould not parse subprocess output:\n{proc.stdout}")
        if proc.stderr:
            print("stderr:", proc.stderr)
        raise SystemExit(1)

    if proc.stderr.strip():
        print("\n[profiler stderr]")
        for stderr_line in proc.stderr.strip().splitlines():
            print(f"  {stderr_line}")

    print()
    all_passed: bool = True
    for suite_name in ("asyncio_guards", "profiler_samples"):
        if suite_name not in results:
            continue
        line: str = _format_result(suite_name, results[suite_name])
        print(line)
        if not results[suite_name].get("passed") and not results[suite_name].get("skipped"):
            all_passed = False

    print()

    if args.baseline:
        if not all_passed:
            print("Not saving baseline — some checks failed. Fix them first, then re-run with --baseline.")
        else:
            baseline_key: str = _baseline_key(python_exe)
            baselines: dict[str, Any] = _load_baselines()
            # Only save the expected task names (not transient names like "<invalid>")
            # so the baseline comparison is deterministic across runs.
            seen_names: set[str] = set(results.get("profiler_samples", {}).get("asyncio_task_names_seen", []))
            stable_names: list[str] = sorted(seen_names & set(_ASYNCIO_TASK_NAMES))

            baselines[baseline_key] = {
                "asyncio_guards": results.get("asyncio_guards", {}),
                "profiler_samples": {
                    "passed": results.get("profiler_samples", {}).get("passed", False),
                    "min_wall_time_samples": _MIN_WALL_TIME_SAMPLES,
                    "asyncio_task_names_seen": stable_names or _ASYNCIO_TASK_NAMES,
                },
            }
            _save_baselines(baselines)
            print(f"Baseline saved for Python {baseline_key} → {_BASELINE_FILE}")

    if args.compare:
        baseline_key = _baseline_key(python_exe)
        baselines = _load_baselines()
        if baseline_key not in baselines:
            print(f"No baseline for Python {baseline_key}. Run with --baseline on a known-good version first.")
        else:
            failures: list[str] = _compare_with_baseline(results, baselines[baseline_key])
            if failures:
                print("Baseline comparison FAILED:")
                for failure in failures:
                    print(f"  - {failure}")
                all_passed = False
            else:
                print(f"Baseline comparison PASSED (vs Python {baseline_key} baseline).")

    if all_passed:
        print("All checks passed.")
    else:
        print("Some checks FAILED. See above for details.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
