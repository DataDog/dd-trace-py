"""Guard/observer for ddtrace native-extension loads.

Blocks (or logs) every import of ``ddtrace.internal.native._native`` so the
import-time "native diet" can be driven empirically: run a scenario, see the
exact loading stack, defer the eager edge, repeat.

Modes:
  enforce  raise RuntimeError at the first native load. The traceback is the
           loading path to defer next. Loud by design; use for inventory.
  absent   raise ImportError instead, simulating an installation without the
           compiled extension. Code paths designed to tolerate a missing
           extension engage their fallbacks; hard requirements fail loudly.
           This is the acceptance-test mode: a scenario must *complete* here.
  observe  log every load attempt with its stack and let it proceed.

Usage:
    python scripts/no_native_guard.py <mode> [pytest args...]

Example - drive the diet for the pytest plugin on a scratch suite:
    python scripts/no_native_guard.py enforce --ddtrace -q /path/to/suite
    python scripts/no_native_guard.py absent --ddtrace -q /path/to/suite
"""

import sys
import traceback


MODE = sys.argv[1]
TARGET = "ddtrace.internal.native._native"
loads = []


class _Guard:
    def find_spec(self, name, path=None, target=None):
        if name != TARGET:
            return None
        stack = "".join(traceback.format_stack()[:-1])  # drop the find_spec frame
        loads.append(stack)
        if MODE == "enforce":
            raise RuntimeError("native extension load blocked:\n" + stack)
        if MODE == "absent":
            raise ImportError("native extension absent (simulated by no_native_guard)", name=TARGET)
        return None  # observe: let the real loader proceed


sys.meta_path.insert(0, _Guard())
sys.argv = ["pytest"] + sys.argv[2:]

import pytest  # noqa: E402


rc = pytest.main(sys.argv[1:])

print(f"\n===== native load attempts: {len(loads)} =====")
for i, s in enumerate(loads):
    print(f"--- load #{i} ---\n{s}")
sys.exit(rc)
