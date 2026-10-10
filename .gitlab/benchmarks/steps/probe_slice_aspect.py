#!/usr/bin/env python3
"""slice-aspect child: the benchmark's aspect machinery in the probe's harness.

EXPERIMENT (do not merge): CPU-asymmetry probe (PR #20052 / APMSP-4059),
scenario ``slice-aspect`` (T11). This file runs as a CHILD PROCESS of
probe.py, under a venv whose interpreter has the benchmark's ddtrace wheel
installed (probe.py builds it from the pass job's candidate-wheel/ or
baseline-wheel/ artifacts); it cannot run under the probe parent's
stdlib-only python. All timing happens here; probe.py only launches and
collects, so the CPU pin the parent set is inherited and nothing else runs
in this process.

The setup mirrors the benchmark's scenario module exactly, cited per line:
- scenario.py:1-8 -- the imports (bm, IAST_ENV, both iast contexts,
  asm_config, override_env, enable_iast_propagation), verbatim; bm is
  imported BEFORE the enable block so the watchdog never patches it, same
  as the benchmark
- scenario.py:11-15 -- the import-time enable block, verbatim: IAST_ENV
  overridden, both asm_config flags set, enable_iast_propagation(), then
  ``import functions`` INSIDE the with, so the ModuleWatchdog AST-patches
  the benchmark's own functions.py (os.path.basename ->
  aspects.ospathbasename_aspect, visitor.py:99-100). Per F21 this is the
  benchmark's executed loop for BOTH configs, so this child is where the
  5-7x scale gap (F20) should appear if the machinery engages
- scenario.py:29-31 -- the per-iteration call shape
  ``_ = getattr(functions, <function_name>)()``
- scenario.py:33-34 -- the config's context: the noaspect config
  (iast_enabled: false) times inside _without_iast_context(), the aspect
  config inside _with_iast_context()
- config.yaml -- function_name resolves to "ospathbasename_noaspect"
  (noaspect) / "iast_ospathbasename_aspect" (aspect)
- functions.py:41-42,45-46 -- the timed functions: the aspect config's
  explicit ospathbasename_aspect call, and the noaspect
  os.path.basename call whose AST-patched form is this scenario's main
  metric

Two variants are measured with the probe's plain calibrated-batch harness
(perf_counter around batches grown to >= 50 ms, no pyperf, no worker
process, default GC -- the same harness as the slice scenario):
- main, "reps": the patched plain call (the noaspect config's executed
  loop), window --seconds per rep
- explicit, "explicit_reps": the aspect config's explicit aspect call,
  window --explicit-seconds per rep; this one cannot fail to engage, so it
  stays informative even if the AST patch does not fire in this process

Engagement is decided structurally, never from the timing: the loaded
ospathbasename_noaspect code object is compared against a plain compile of
the same functions.py source executed without the import machinery (so the
watchdog cannot intervene), plus a count of the patched module's
_ddtrace_-prefixed symbols (visitor.py:20 uses that prefix). If ast_patched
is false the main variant measured the plain ~0.75 us call and the report
must record that plainly (pre-registered T11 fork).

Output: one JSON line on stdout (probe.py parses it); tracebacks go to
stderr with a nonzero exit on failure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback


BENCH_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "appsec_iast_aspects_ospath"
# The benchmark harness makes both the scenario dir (for `import functions`)
# and benchmarks/ (for `import bm`) importable; this child does the same
# before the copied block below -- pure harness plumbing, not part of the
# copied scenario semantics.
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(0, str(BENCH_DIR))

# copied verbatim from benchmarks/appsec_iast_aspects_ospath/scenario.py:1-8
import bm  # noqa: E402,F401
from bm.iast_utils import IAST_ENV  # noqa: E402
from bm.iast_utils import _with_iast_context  # noqa: E402
from bm.iast_utils import _without_iast_context  # noqa: E402
from bm.iast_utils import asm_config  # noqa: E402
from bm.utils import override_env  # noqa: E402

from ddtrace.appsec._iast import enable_iast_propagation  # noqa: E402


# copied verbatim from scenario.py:11-15: importing functions INSIDE the
# enabled state is what AST-patches its os.path.basename call site
with override_env(IAST_ENV):
    asm_config._iast_enabled = True
    asm_config._iast_propagation_enabled = True
    enable_iast_propagation()
    import functions  # noqa: E402

# same 50 ms target as probe.py's SLICE_BATCH_TARGET_S for the slice
# scenario: every timed window clears perf_counter's resolution
BATCH_TARGET_S = 0.05


def engagement_check() -> dict:
    """Is the loaded functions module the AST-patched one?

    Compares the loaded ospathbasename_noaspect code object against a plain
    compile of the same functions.py source, executed without the import
    machinery so the watchdog cannot intervene. Identical bytecode means
    the patch never fired and the main variant timed the plain ~0.75 us
    call; the _ddtrace_-prefixed symbols the visitor injects are counted as
    corroborating evidence.
    """
    source = (BENCH_DIR / "functions.py").read_text()
    plain_ns = {}
    exec(compile(source, str(BENCH_DIR / "functions.py"), "exec"), plain_ns)  # nosec B102
    plain = plain_ns["ospathbasename_noaspect"].__code__
    loaded = functions.ospathbasename_noaspect.__code__
    code_differs = (plain.co_code, plain.co_names) != (loaded.co_code, loaded.co_names)
    symbols = sorted(k for k in vars(functions) if k.startswith("_ddtrace_"))
    return {
        "ast_patched": bool(code_differs or symbols),
        "code_differs": bool(code_differs),
        "patch_symbols": symbols,
    }


def _batch(function_name: str, iterations: int) -> float:
    """One timed batch: iterations of the benchmark's per-iteration shape
    (scenario.py:29-31), perf_counter around the whole batch.
    """
    start = time.perf_counter()
    for _ in range(iterations):
        _ = getattr(functions, function_name)()
    return time.perf_counter() - start


def _calibrate(function_name: str) -> int:
    """Grow a batch size until one batch takes >= BATCH_TARGET_S, untimed;
    doubles as the warm-up the benchmark gets from pyperf's warmups.
    """
    n = 1024
    while _batch(function_name, n) < BATCH_TARGET_S:
        n *= 4
    return n


def _measure(function_name: str, batch: int, reps: int, seconds: float):
    """reps x seconds of calibrated batches; returns [(ops, seconds), ...]
    per rep like the probe's other cores, seconds being the sum of the
    batch windows of that rep.
    """
    out = []
    for _ in range(reps):
        ops = 0
        elapsed = 0.0
        while elapsed < seconds:
            elapsed += _batch(function_name, batch)
            ops += batch
        out.append((ops, elapsed))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="slice-aspect child (launched by probe.py under the aspect venv)")
    parser.add_argument("--reps", type=int, default=3, help="reps per variant")
    parser.add_argument("--seconds", type=float, default=2.0, help="measured window per rep, main variant")
    parser.add_argument("--explicit-seconds", type=float, default=1.0, help="measured window per rep, explicit variant")
    args = parser.parse_args()

    out = {"python": sys.version.split()[0], "engagement": engagement_check()}
    if functions.ospathbasename_noaspect() != "file" or functions.iast_ospathbasename_aspect() != "file":
        raise RuntimeError("timed functions do not return the benchmark's result")

    # main: the noaspect config's executed loop (scenario.py:33-34 with
    # iast_enabled: false -> _without_iast_context), timed inside it exactly
    # like the benchmark times it
    with _without_iast_context():
        batch = _calibrate("ospathbasename_noaspect")
        out["batch"] = batch
        out["reps"] = _measure("ospathbasename_noaspect", batch, args.reps, args.seconds)

    # explicit: the aspect config's executed loop (iast_enabled: true ->
    # _with_iast_context, function_name "iast_ospathbasename_aspect"); runs
    # after the main variant so a started request context can never leak
    # into it
    with _with_iast_context():
        batch = _calibrate("iast_ospathbasename_aspect")
        out["explicit_batch"] = batch
        out["explicit_reps"] = _measure("iast_ospathbasename_aspect", batch, args.reps, args.explicit_seconds)

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
