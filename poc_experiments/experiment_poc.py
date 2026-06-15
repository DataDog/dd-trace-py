"""
Trace-seeded local regression experiments — runnable POC.

Self-contained, stdlib-only. NOT wired into ddtrace yet — this exists to feel
the decorator + stop-point mechanics before integrating with the real LLMObs
SDK or a real service like lux.

Two boundary shapes are supported:

  1. Single-function unit (lux's `_run_lux_agent` shape): input enters and the
     result is RETURNED by the same function.
        @experiment_start(inputs=["message"], output=lambda ret: ret[0])
        async def run_agent(agent, deps, message, user_handle=""): ...
     Capture records (selected inputs -> extracted return). Replay re-invokes
     the function and compares the new return.

  2. Across-functions emit shape (input at one fn, output handed to another):
        @experiment_start(inputs=["query"])
        def ingest(query): ...
        @experiment_end
        def emit(result): ...
     Capture records (inputs -> what emit received). Replay re-invokes the start
     and UNWINDS at emit, so emit's real side effects do NOT run.

Mode gating: decorators are an inert passthrough unless a mode is set by the CLI
(never read from an ambient OS env var), so they are safe in app code.

Usage:
    python experiment_poc.py capture example_app:generate_traffic
    python experiment_poc.py replay  example_app
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import importlib
import inspect
import json
import sys
from enum import Enum


class Mode(Enum):
    OFF = "off"
    CAPTURE = "capture"
    REPLAY = "replay"


_mode: Mode = Mode.OFF  # the single gate; only the CLI flips it
_REGISTRY: dict[str, dict] = {}  # name -> {start, inputs, start_output, end, end_output}
_current_case: contextvars.ContextVar = contextvars.ContextVar("experiment_case", default=None)
CAPTURE_PATH = "experiment_cases.jsonl"


class _ExperimentStop(BaseException):
    """Raised at the end marker during REPLAY to unwind to the harness.
    BaseException (not Exception) so a broad ``except Exception:`` between start
    and end cannot swallow it."""

    def __init__(self, output):
        self.output = output


# --------------------------------------------------------------------------- #
# (de)serialization
# --------------------------------------------------------------------------- #
def _encode(obj):
    return json.loads(json.dumps(obj, default=repr))


def _bind_inputs(fn, args, kwargs, inputs):
    """Entry args as a {param_name: value} dict, optionally restricted to
    `inputs` (so live infra like agent/deps is excluded)."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        named = dict(bound.arguments)
    except Exception:
        named = {"args": list(args), "kwargs": dict(kwargs)}
    if inputs is not None:
        named = {k: v for k, v in named.items() if k in inputs}
    return _encode(named)


def _start_output(output_fn, ret):
    """Single-function unit: output is the entry's RETURN value."""
    return _encode(output_fn(ret)) if output_fn is not None else _encode(ret)


def _end_output(output_fn, args, kwargs):
    """Emit shape: output is what was passed INTO the end function."""
    if output_fn is not None:
        return _encode(output_fn(args, kwargs))
    if len(args) == 1 and not kwargs:
        return _encode(args[0])
    return _encode({"args": list(args), "kwargs": dict(kwargs)})


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #
def experiment_start(_fn=None, *, name: str = "default", inputs: list | None = None, output=None, fixtures=None):
    """Mark the ENTRY point.

    inputs   : restrict which args are captured/replayed (others are live infra).
    output   : (ret) -> value, extracts the semantic output from the return when
               this is a single-function unit (no separate @experiment_end).
    fixtures : callable (sync or async) returning a dict of the NON-captured args
               (the live infra) to supply at replay time. For lux this is where
               create_lux_runtime() + LuxAgentDependencies(...) get built.
    """

    def deco(fn):
        _REGISTRY.setdefault(name, {}).update(start=fn, inputs=inputs, start_output=output, fixtures=fixtures)

        def _finish_capture(case, result):
            if case["reached_end"]:  # an @experiment_end set the output
                _append_case(case)
            elif "end" not in _REGISTRY.get(name, {}):  # single-function unit
                case["output"] = _start_output(output, result)
                _append_case(case)

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                if _mode is Mode.OFF:
                    return await fn(*args, **kwargs)
                case = {"name": name, "input": _bind_inputs(fn, args, kwargs, inputs), "output": None, "reached_end": False}
                token = _current_case.set(case)
                try:
                    result = await fn(*args, **kwargs)
                    if _mode is Mode.CAPTURE:
                        _finish_capture(case, result)
                    return result
                finally:
                    _current_case.reset(token)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if _mode is Mode.OFF:
                return fn(*args, **kwargs)
            case = {"name": name, "input": _bind_inputs(fn, args, kwargs, inputs), "output": None, "reached_end": False}
            token = _current_case.set(case)
            try:
                result = fn(*args, **kwargs)
                if _mode is Mode.CAPTURE:
                    _finish_capture(case, result)
                return result
            finally:
                _current_case.reset(token)

        return wrapper

    return deco(_fn) if _fn is not None else deco


def experiment_end(_fn=None, *, name: str = "default", output=None):
    """Mark the STOP point (emit shape). output : (args, kwargs) -> value."""

    def deco(fn):
        _REGISTRY.setdefault(name, {}).update(end=fn, end_output=output)

        def _handle(args, kwargs):
            out = _end_output(output, args, kwargs)
            if _mode is Mode.REPLAY:
                raise _ExperimentStop(out)  # capture + unwind before side effects
            case = _current_case.get()  # CAPTURE
            if case is not None:
                case["output"] = out
                case["reached_end"] = True

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                if _mode is Mode.OFF:
                    return await fn(*args, **kwargs)
                _handle(args, kwargs)  # raises in REPLAY
                return await fn(*args, **kwargs)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if _mode is Mode.OFF:
                return fn(*args, **kwargs)
            _handle(args, kwargs)  # raises in REPLAY
            return fn(*args, **kwargs)

        return wrapper

    return deco(_fn) if _fn is not None else deco


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _append_case(case):
    with open(CAPTURE_PATH, "a") as f:
        f.write(json.dumps({"name": case["name"], "input": case["input"], "output": case["output"]}) + "\n")


def _load_cases():
    cases = []
    try:
        with open(CAPTURE_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    except FileNotFoundError:
        pass
    return cases


# --- Comparators -------------------------------------------------------------
# A comparator answers "is the new output equivalent to the recorded baseline?".
# Real LLM apps are non-deterministic (free text, timestamps), so `exact` reports
# CHANGED on every replay. `ignoring`/`structural` make replay meaningful by
# comparing what actually matters.


def exact_comparator(recorded, new):
    """Strict equality. Useful for deterministic outputs only."""
    return recorded == new


def _deep_drop(value, keys):
    if isinstance(value, dict):
        return {k: _deep_drop(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_deep_drop(v, keys) for v in value]
    return value


def ignoring(*keys):
    """Equality after recursively dropping volatile keys (e.g. 'generated_at')."""
    keyset = set(keys)

    def cmp(recorded, new):
        return _deep_drop(recorded, keyset) == _deep_drop(new, keyset)

    return cmp


def _shape(value):
    """Reduce a value to its structure: dict keys, list lengths, and leaf *types*
    (not leaf values). So free-text/number drift is ignored, but a missing field,
    a changed type, or a dropped list element shows up."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__


def structural_comparator(recorded, new):
    """Equivalent if the output *shape* matches (keys/types/list-lengths)."""
    return _shape(recorded) == _shape(new)


def llm_judge(judge_fn):
    """Comparator backed by a caller-supplied judge: ``judge_fn(recorded, new) -> bool``
    (typically an LLM 'are these semantically equivalent?' call). Kept dependency-free
    — the caller supplies the model call, so the POC stays stdlib-only. For semantic
    equivalence on free-text outputs that `structural` can't judge."""

    def cmp(recorded, new):
        return bool(judge_fn(recorded, new))

    return cmp


_default_comparator = exact_comparator


def _comparator_from_args(rest):
    """Build a comparator from CLI flags: --comparator exact|structural|ignoring
    and --ignore key1,key2 (implies ignoring)."""
    kind = "exact"
    ignore: list = []
    i = 0
    while i < len(rest):
        if rest[i] == "--comparator" and i + 1 < len(rest):
            kind = rest[i + 1]
            i += 2
            continue
        if rest[i] == "--ignore" and i + 1 < len(rest):
            ignore = [k for k in rest[i + 1].split(",") if k]
            i += 2
            continue
        i += 1
    if kind == "structural":
        return structural_comparator
    if kind == "ignoring" or ignore:
        return ignoring(*ignore)
    return exact_comparator


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #
def _invoke(spec, input_kwargs):
    """Call the entry with captured inputs + freshly-built live fixtures."""
    entry = spec["start"]
    fixtures = spec.get("fixtures")
    if asyncio.iscoroutinefunction(entry):

        async def _run():
            fx = fixtures() if fixtures else {}
            if inspect.iscoroutine(fx):
                fx = await fx
            return await entry(**fx, **input_kwargs)

        return asyncio.run(_run())

    fx = fixtures() if fixtures else {}
    if inspect.iscoroutine(fx):
        raise RuntimeError("async `fixtures` requires an async entry function")
    return entry(**fx, **input_kwargs)


def replay(name: str = "default", comparator=None):
    comparator = comparator or _default_comparator
    spec = _REGISTRY.get(name, {})
    entry = spec.get("start")
    if entry is None:
        raise RuntimeError(f"No @experiment_start registered for '{name}'. Did you import the app module?")
    has_end = "end" in spec
    start_output_fn = spec.get("start_output")

    results = []
    for case in _load_cases():
        if case.get("name") != name:
            continue
        row = {"input": case["input"], "recorded": case["output"], "new": None, "status": "NO_END"}
        try:
            ret = _invoke(spec, case["input"])
            if not has_end:  # single-function unit: the return IS the output
                row["new"] = _start_output(start_output_fn, ret)
                row["status"] = "MATCH" if comparator(row["recorded"], row["new"]) else "CHANGED"
        except _ExperimentStop as stop:  # emit shape: end unwound with the output
            row["new"] = stop.output
            row["status"] = "MATCH" if comparator(row["recorded"], row["new"]) else "CHANGED"
        except Exception as e:  # noqa: BLE001 - POC: surface task errors as a row
            row["new"] = f"<error: {e!r}>"
            row["status"] = "ERROR"
        results.append(row)
    return results


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _trunc(v, n=40):
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_report(name, results):
    counts: dict = {}
    print(f"\n  experiment: {name}   ({len(results)} cases)")
    print("  " + "-" * 86)
    print(f"  {'status':<8} {'input':<30} {'recorded':<22} {'new':<22}")
    print("  " + "-" * 86)
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"  {r['status']:<8} {_trunc(r['input'], 30):<30} {_trunc(r['recorded'], 22):<22} {_trunc(r['new'], 22):<22}")
    print("  " + "-" * 86)
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()) + "\n")
    return counts


# --------------------------------------------------------------------------- #
# CLI — the explicit, prod-impossible trigger
# --------------------------------------------------------------------------- #
def _import_target(target):
    mod_name, _, attr = target.partition(":")
    mod = importlib.import_module(mod_name)
    return mod, (getattr(mod, attr) if attr else None)


def cli(argv=None):
    global _mode
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in {"capture", "replay"}:
        print(__doc__)
        raise SystemExit(1)

    cmd, target = argv[0], argv[1]
    if cmd == "capture":
        _mode = Mode.CAPTURE
        _, entry = _import_target(target)
        if entry is None:
            raise SystemExit("capture needs 'module:entrypoint', e.g. example_app:generate_traffic")
        result = entry()
        if inspect.iscoroutine(result):
            asyncio.run(result)
        print(f"captured cases -> {CAPTURE_PATH}")
    else:
        _mode = Mode.REPLAY
        _import_target(target)
        comparator = _comparator_from_args(argv[2:])
        for name in sorted(_REGISTRY):
            _print_report(name, replay(name, comparator))


if __name__ == "__main__":
    import experiment_poc  # single module identity (see README)

    experiment_poc.cli()
