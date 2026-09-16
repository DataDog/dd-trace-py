#!/usr/bin/env python3
"""Reproduce IR-60700: profiler SIGSEGV handler takeover -> process death.

One scenario per process (signal-handler state is far too sticky to test several
in one interpreter). Select with REPRO_SCENARIO:

  control      no profiler at all -- baseline for how a plain fault dies
  owned        profiler running, ddtrace legitimately owns SIGSEGV
  foreign      a component installs its own SIGSEGV handler via signal.signal,
               bypassing ddtrace's faulthandler wrapper entirely. Same end state
               as the documented pause_sampling() timeout path in
               ddtrace/profiling/_faulthandler.py, and the state that emits
               "handler was taken over by another component" in production.
  faulthandler the real-world path: faulthandler.enable() through ddtrace's
               patched wrapper, which pauses the sampler and swaps handlers.

Each scenario faults in a forked child and records how the child died. A child
that never dies is the signature of an infinite fault-handler cycle.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import time
import typing


_SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
LOG_PATH: str = os.environ.get("REPRO_LOG_PATH", "/tmp/segv_handler_repro.ndjson")
RUN_ID: str = os.environ.get("REPRO_RUN_ID", "baseline")
SCENARIO: str = os.environ.get("REPRO_SCENARIO", "control")
WARMUP_S: float = float(os.environ.get("REPRO_WARMUP_S", "18"))
CHILD_TIMEOUT_S: float = float(os.environ.get("REPRO_CHILD_TIMEOUT_S", "15"))
FOREIGN_EXIT_CODE: int = 42


def _log(hypothesis_id: str, message: str, data: dict[str, typing.Any]) -> None:
    rec: dict[str, typing.Any] = {
        "runId": RUN_ID,
        "hypothesisId": hypothesis_id,
        "scenario": SCENARIO,
        "location": "segv_handler_repro.py",
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(LOG_PATH, "a") as fh:
        fh.write(json.dumps(rec, default=repr) + "\n")
        fh.flush()


def _fault() -> None:
    ctypes.string_at(0)


def _fault_in_child(hypothesis_id: str) -> dict[str, typing.Any]:
    """Fault in a forked child and report how it died."""
    sys.stdout.flush()
    sys.stderr.flush()
    pid: int = os.fork()
    if pid == 0:
        try:
            _fault()
        except BaseException:
            os._exit(43)
        os._exit(44)

    deadline: float = time.time() + CHILD_TIMEOUT_S
    while time.time() < deadline:
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            signaled: bool = os.WIFSIGNALED(status)
            termsig: int = os.WTERMSIG(status) if signaled else 0
            exitcode: int = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            outcome: dict[str, typing.Any] = {
                "hung": False,
                "signaled": signaled,
                "termsig": termsig,
                "termsig_name": signal.Signals(termsig).name if termsig else None,
                "exitcode": exitcode,
                "foreign_handler_ran": exitcode == FOREIGN_EXIT_CODE,
                "recovered_no_crash": exitcode == 44,
                "seconds_to_die": round(time.time() - (deadline - CHILD_TIMEOUT_S), 3),
            }
            _log(hypothesis_id, "child outcome after fault", outcome)
            return outcome
        time.sleep(0.05)

    outcome = {"hung": True, "timeout_s": CHILD_TIMEOUT_S, "child_pid": pid}
    _log(
        hypothesis_id,
        "CHILD HUNG after fault -- signature of an infinite fault-handler cycle",
        outcome,
    )
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return outcome


def _install_native_foreign_handler() -> dict[str, typing.Any]:
    """Install a NATIVE SIGSEGV handler via sigaction, like torch/abseil/gRPC do.

    Unlike signal.signal, this is a real C handler, so it actually runs on a fault
    and chains to whatever was installed before it (ddtrace's, when the profiler
    is up). This is the faithful model of the production takeover.
    """
    lib_path: str = os.environ.get("REPRO_FOREIGN_LIB", os.path.join(_SCRIPT_DIR, "libforeign.so"))
    lib = ctypes.CDLL(lib_path)
    rc: int = lib.install_foreign_handler()
    return {"sigaction_rc": rc, "chained_onto_existing_handler": bool(lib.prev_was_handler())}


def _install_foreign_segv_handler() -> None:
    """Install a SIGSEGV handler without going through ddtrace's wrapper."""

    def _handler(signum: int, frame: typing.Any) -> None:  # pragma: no cover
        os._exit(FOREIGN_EXIT_CODE)

    signal.signal(signal.SIGSEGV, _handler)


def main() -> int:
    if SCENARIO == "control":
        _log("control", "no profiler -- baseline fault behaviour", {"python": sys.version.split()[0]})
        _fault_in_child("control")
        return 0

    if SCENARIO == "foreign-no-profiler":
        # Critical control for the `foreign` result: install the SAME Python-level
        # SIGSEGV handler with ddtrace never imported. If this also hangs, the hang
        # is Python signal semantics (the C trampoline sets a flag and returns, so
        # the faulting instruction re-executes forever) and says nothing about ddtrace.
        _install_foreign_segv_handler()
        _log(
            "control",
            "python-level SIGSEGV handler, NO profiler -- isolates Python artifact from ddtrace",
            {"python": sys.version.split()[0], "ddtrace_imported": "ddtrace" in sys.modules},
        )
        _fault_in_child("control")
        return 0

    if SCENARIO == "foreign-native-no-profiler":
        # Control for `foreign-native`: same native handler, ddtrace never imported.
        # It should chain onto SIG_DFL and the child should die promptly.
        info: dict[str, typing.Any] = _install_native_foreign_handler()
        _log(
            "H13",
            "native sigaction handler, NO profiler -- baseline for the native path",
            {**info, "ddtrace_imported": "ddtrace" in sys.modules},
        )
        _fault_in_child("H13")
        return 0

    try:
        import ddtrace
        from ddtrace.internal.datadog.profiling.stack import _stack
    except Exception as exc:
        print(f"FATAL: ddtrace/native stack unavailable: {exc!r}", file=sys.stderr)
        return 2

    _log(
        "setup",
        "environment",
        {
            "ddtrace_version": getattr(ddtrace, "__version__", "?"),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "machine": os.uname().machine,
            "warmup_s": WARMUP_S,
        },
    )

    # Start via the supported entry point. Driving _stack.start() directly skips
    # the initialisation Profiler() performs and segfaults on its own.
    from ddtrace.profiling.profiler import Profiler

    prof = Profiler()
    prof.start()

    # Wait past the real fast-copy warmup deadline so the sampler upgrades from
    # the syscall copy to safe_memcpy, which is the state production is in.
    time.sleep(WARMUP_S)

    _log(
        "H12",
        "state after warmup deadline -- fast copy should be upgraded and ours",
        {
            "segv_handler_installed": bool(_stack.segv_handler_installed()),
            "fast_copy_memory_active": bool(_stack.fast_copy_memory_active()),
        },
    )

    if SCENARIO == "owned":
        _fault_in_child("H12")

    elif SCENARIO == "foreign":
        _install_foreign_segv_handler()
        _log(
            "H12",
            "state after foreign signal.signal install -- installed=False is the prod takeover state",
            {
                "segv_handler_installed": bool(_stack.segv_handler_installed()),
                "fast_copy_memory_active": bool(_stack.fast_copy_memory_active()),
            },
        )
        _fault_in_child("H12")

    elif SCENARIO == "foreign-native":
        info = _install_native_foreign_handler()
        _log(
            "H13",
            "state after NATIVE sigaction install -- chained onto ddtrace's handler",
            {
                **info,
                "segv_handler_installed": bool(_stack.segv_handler_installed()),
                "fast_copy_memory_active": bool(_stack.fast_copy_memory_active()),
            },
        )
        _fault_in_child("H13")

    elif SCENARIO == "faulthandler":
        import faulthandler

        patched: bool = getattr(faulthandler.enable, "__name__", "") == "_patched_enable"
        t0: float = time.time()
        faulthandler.enable()
        _log(
            "H12",
            "state after faulthandler.enable() through ddtrace's wrapper",
            {
                "wrapper_active": patched,
                "enable_took_s": round(time.time() - t0, 3),
                "segv_handler_installed": bool(_stack.segv_handler_installed()),
                "fast_copy_memory_active": bool(_stack.fast_copy_memory_active()),
                "faulthandler_is_enabled": faulthandler.is_enabled(),
            },
        )
        _fault_in_child("H12")

    else:
        print(f"FATAL: unknown REPRO_SCENARIO={SCENARIO!r}", file=sys.stderr)
        return 2

    try:
        prof.stop(flush=False)
    except Exception:  # nosec: B110
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
