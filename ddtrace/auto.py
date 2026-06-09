"""
.. _ddtraceauto:

Importing ``ddtrace.auto`` installs Datadog instrumentation in the runtime. It should be used
when :ref:`ddtrace-run<ddtracerun>` is not an option. Using it with :ref:`ddtrace-run<ddtracerun>`
is unsupported and may lead to undefined behavior::

    # myapp.py

    import ddtrace.auto  # install instrumentation as early as possible
    import mystuff

    def main():
        print("It's my app!")

    main()

If you'd like more granular control over instrumentation setup, you can call the `patch*` functions
directly.
"""

# Experiments-only: when DD_PROFILE_OUT_DIR is set, start a process-wide
# cProfile *before* ddtrace's own imports so we capture the full lifetime
# overhead of dd-trace-py (auto-import, sitecustomize, contrib patching,
# import-hook callbacks). Dumped at interpreter exit. Pure no-op when the
# env var is unset. This lives on the experiments branch only — do not
# upstream to kr-igor/pytorch-rank-span.
import os as _os

if _os.environ.get("DD_PROFILE_OUT_DIR"):
    import atexit as _atexit
    import cProfile as _cProfile

    _ddtrace_lifetime_profiler = _cProfile.Profile()
    _ddtrace_lifetime_profiler.enable()

    def _dump_ddtrace_lifetime_profile() -> None:
        _ddtrace_lifetime_profiler.disable()
        out_dir = _os.environ.get("DD_PROFILE_OUT_DIR")
        if not out_dir:
            return
        try:
            _os.makedirs(out_dir, exist_ok=True)
            # Per-rank file when running under Ray Train; otherwise per-pid.
            rank = _os.environ.get("RANK") or _os.environ.get("RAY_TRAIN_WORKER_RANK")
            tag = f"rank-{rank}" if rank is not None else f"pid-{_os.getpid()}"
            _ddtrace_lifetime_profiler.dump_stats(_os.path.join(out_dir, f"lifetime-{tag}.prof"))
        except Exception:
            pass

    _atexit.register(_dump_ddtrace_lifetime_profile)

import ddtrace.bootstrap.sitecustomize  # noqa:F401
