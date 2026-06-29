"""Reproducers for native syscall hazards with the timer_create CPU profiler.

This module is both a pytest helper and a standalone sample application. It
builds a tiny CPython extension from native_cpu_timer_syscall_hazards.c and runs
scenarios that model native code the Python profiler cannot rewrite:

- a raw pthread that never enters Python and should not be armed by the CPU timer
- a Python-visible native function that blocks SIGPROF, accumulates a pending CPU
  timer signal, then atomically unblocks SIGPROF inside ppoll without retrying
  EINTR

Run manually, for example:

    _DD_PROFILING_STACK_CPU_TIMER_ENABLED=1 \
    _DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS=1 \
    python tests/profiling/cpu_timer_native_syscall_hazard_app.py --scenario ppoll
"""

from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from types import ModuleType
from typing import Any


SOURCE_PATH = Path(__file__).with_name("native_cpu_timer_syscall_hazards.c")
MODULE_NAME = "native_cpu_timer_syscall_hazards"


def _compiler_command() -> list[str]:
    cc = os.environ.get("CC")
    if cc:
        return shlex.split(cc)

    for candidate in ("cc", "gcc", "clang"):
        path = shutil.which(candidate)
        if path is not None:
            return [path]

    raise RuntimeError("no C compiler found")


def build_native_module(build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)

    include_dir = sysconfig.get_config_var("INCLUDEPY")
    extension_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not include_dir or not extension_suffix:
        raise RuntimeError("could not resolve Python extension build settings")

    output_path = build_dir / f"{MODULE_NAME}{extension_suffix}"
    command = _compiler_command()
    command.extend(
        [
            "-shared",
            "-fPIC",
            "-O2",
            "-pthread",
            "-I",
            str(include_dir),
            str(SOURCE_PATH),
            "-o",
            str(output_path),
        ]
    )

    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "failed to build native syscall hazard module\n"
            + "command: "
            + " ".join(command)
            + "\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr
        )

    return output_path


def import_native_module(build_dir: Path) -> ModuleType:
    build_native_module(build_dir)
    sys.path.insert(0, str(build_dir))
    try:
        return importlib.import_module(MODULE_NAME)
    finally:
        try:
            sys.path.remove(str(build_dir))
        except ValueError:
            pass


def run_ppoll_scenario(build_dir: Path, burn_ms: int = 50, timeout_ms: int = 500) -> dict[str, Any]:
    native = import_native_module(build_dir)

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        time.sleep(0.05)
        before = stack._cpu_timer_debug_stats()
        err = native.raw_ppoll_with_pending_sigprof(
            burn_ms=burn_ms,
            timeout_ms=timeout_ms,
            release_gil=True,
            use_raw_syscall=True,
        )
        after = stack._cpu_timer_debug_stats()
    finally:
        p.stop()

    return {
        "scenario": "ppoll",
        "errno": err,
        "errno_name": errno.errorcode.get(err),
        "before": before,
        "after": after,
    }


def run_raw_pthread_scenario(build_dir: Path, burn_ms: int = 250) -> dict[str, Any]:
    native = import_native_module(build_dir)

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        time.sleep(0.05)
        before = stack._cpu_timer_debug_stats()
        pthread_rc = native.raw_pthread_burn_cpu(burn_ms)
        after = stack._cpu_timer_debug_stats()
    finally:
        p.stop()

    return {
        "scenario": "raw-pthread",
        "pthread_rc": pthread_rc,
        "before": before,
        "after": after,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("ppoll", "raw-pthread"), required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--burn-ms", type=int)
    parser.add_argument("--timeout-ms", type=int, default=500)
    parser.add_argument(
        "--expect-no-eintr",
        action="store_true",
        help="return a non-zero status if the ppoll scenario surfaces EINTR",
    )
    args = parser.parse_args(argv)

    if args.build_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="ddtrace-cpu-timer-syscall-")
        build_dir = Path(temp_dir.name)
    else:
        temp_dir = None
        build_dir = args.build_dir

    try:
        if args.scenario == "ppoll":
            result = run_ppoll_scenario(
                build_dir,
                burn_ms=args.burn_ms if args.burn_ms is not None else 50,
                timeout_ms=args.timeout_ms,
            )
        else:
            result = run_raw_pthread_scenario(
                build_dir,
                burn_ms=args.burn_ms if args.burn_ms is not None else 250,
            )

        print(json.dumps(result, sort_keys=True))

        if args.expect_no_eintr and result.get("errno") == errno.EINTR:
            return 2
        return 0
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
