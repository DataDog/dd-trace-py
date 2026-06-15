"""Inline experiments — decorator-driven, trace-seeded local regression testing.

This is the foundation layer for the "unit test for LLM apps" paradigm described in
``ddtrace/llmobs/_local_regression_experiments_design.md`` (RFC-002): a developer marks
an input->output boundary inline in their already-instrumented app, and an explicit,
out-of-band runner captures a baseline and replays the *current* code against it.

This module provides ONLY the novel core:
  * ``experiment_start`` / ``experiment_end`` decorators (sync + async),
  * the explicit-activation gate (a process-internal mode flag),
  * a registry of experiment subjects, and
  * capture of (selected inputs -> output) cases into memory.

The replay/run and reporting reuse the existing experiments engine
(``ddtrace.llmobs._experiment``: ``Dataset`` / ``Experiment`` / evaluators) — see the
runner layer (added separately). Comparators here are lightweight helpers usable as
evaluators.

AIDEV-NOTE: Activation is POSITIVE and EXPLICIT, never inferred from the environment.
The decorators are a pure no-op passthrough unless a runner flips ``_mode`` in-process
*before* importing the user's module. Production never takes that path, so the decorators
are safe to ship in app code. Do NOT gate on ``DD_ENV`` / TTY / hostname — those fail
open in production. See the design doc's "Safety principle".
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
from enum import Enum
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class Mode(Enum):
    OFF = "off"  # default: decorators are inert passthroughs (production-safe)
    CAPTURE = "capture"  # record (inputs -> output) baselines from a real run
    REPLAY = "replay"  # re-drive the entry; the runner compares against the baseline


# The single gate. Only a deliberate runner flips this (never read from an OS env var),
# so it cannot accidentally activate in production.
_mode: Mode = Mode.OFF

# name -> {"start", "inputs", "start_output", "end", "end_output", "fixtures", "cases"}
_REGISTRY: dict[str, dict] = {}

# Pairs a start (one function) with an end (possibly another) across one call tree;
# a ContextVar propagates down the stack and across ``await`` within the same task.
_current_case: contextvars.ContextVar = contextvars.ContextVar("_llmobs_experiment_case", default=None)


class _ExperimentStop(BaseException):
    """Raised at the end marker during REPLAY to unwind back to the runner.

    Subclasses ``BaseException`` (not ``Exception``) on purpose: a broad
    ``except Exception:`` in the user's orchestration between start and end must not
    swallow it.
    """

    def __init__(self, output: Any) -> None:
        self.output = output


# --------------------------------------------------------------------------- #
# Activation (called by the runner, never by app code)
# --------------------------------------------------------------------------- #
def _set_mode(mode: Mode) -> None:
    global _mode
    _mode = mode


def _get_mode() -> Mode:
    return _mode


def _reset() -> None:
    """Clear registry + mode. Primarily for tests."""
    global _mode
    _mode = Mode.OFF
    _REGISTRY.clear()


def registered_experiments() -> list[str]:
    return sorted(_REGISTRY)


def captured_cases(name: str) -> list[dict]:
    """The (input, output) baselines captured for an experiment during CAPTURE mode."""
    return list(_REGISTRY.get(name, {}).get("cases", []))


def _spec(name: str) -> dict:
    return _REGISTRY.setdefault(name, {"cases": []})


# --------------------------------------------------------------------------- #
# Input/output extraction
# --------------------------------------------------------------------------- #
def _bind_inputs(fn: Callable, args: tuple, kwargs: dict, inputs: Optional[list]) -> dict:
    """Entry args as a {param_name: value} dict, optionally restricted to ``inputs``
    (so live infra args like ``agent``/``deps`` are excluded from the captured input)."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        named = dict(bound.arguments)
    except (TypeError, ValueError):
        named = {"args": list(args), "kwargs": dict(kwargs)}
    if inputs is not None:
        named = {k: v for k, v in named.items() if k in inputs}
    return named


def _start_output(output_fn: Optional[Callable], ret: Any) -> Any:
    """Single-function unit: the semantic output is the entry's return value."""
    return output_fn(ret) if output_fn is not None else ret


def _end_output(output_fn: Optional[Callable], args: tuple, kwargs: dict) -> Any:
    """Emit shape: the semantic output is what was passed INTO the end function."""
    if output_fn is not None:
        return output_fn(args, kwargs)
    if len(args) == 1 and not kwargs:
        return args[0]
    return {"args": list(args), "kwargs": dict(kwargs)}


def _record_case(name: str, inputs: dict, output: Any) -> None:
    _spec(name)["cases"].append({"input": inputs, "output": output})


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #
def experiment_start(
    _fn: Optional[Callable] = None,
    *,
    name: str = "default",
    inputs: Optional[list] = None,
    output: Optional[Callable] = None,
    fixtures: Optional[Callable] = None,
) -> Callable:
    """Mark the ENTRY point of an experiment subject.

    :param name: Experiment name; groups a start with its end and its captured cases.
    :param inputs: Restrict which parameters are captured/replayed (others are treated
        as live infrastructure, rebuilt at replay time via ``fixtures``).
    :param output: ``(ret) -> value`` extracting the semantic output from the return,
        for the single-function-unit shape (no separate ``experiment_end``).
    :param fixtures: Callable (sync or async) returning a dict of the NON-captured args
        (the live infra) to supply at replay time.

    Inert (pure passthrough) unless a runner has activated CAPTURE/REPLAY.
    """

    def deco(fn: Callable) -> Callable:
        _spec(name).update(start=fn, inputs=inputs, start_output=output, fixtures=fixtures)

        def _finish_capture(case_inputs: dict, reached_end: bool, result: Any) -> None:
            if reached_end:
                return  # an experiment_end already recorded the output for this case
            if "end" not in _REGISTRY.get(name, {}):  # single-function unit
                _record_case(name, case_inputs, _start_output(output, result))

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                if _mode is Mode.OFF:
                    return await fn(*args, **kwargs)
                case = {"inputs": _bind_inputs(fn, args, kwargs, inputs), "reached_end": False}
                token = _current_case.set(case)
                try:
                    result = await fn(*args, **kwargs)
                    if _mode is Mode.CAPTURE:
                        _finish_capture(case["inputs"], case["reached_end"], result)
                    return result
                finally:
                    _current_case.reset(token)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _mode is Mode.OFF:
                return fn(*args, **kwargs)
            case = {"inputs": _bind_inputs(fn, args, kwargs, inputs), "reached_end": False}
            token = _current_case.set(case)
            try:
                result = fn(*args, **kwargs)
                if _mode is Mode.CAPTURE:
                    _finish_capture(case["inputs"], case["reached_end"], result)
                return result
            finally:
                _current_case.reset(token)

        return wrapper

    return deco(_fn) if _fn is not None else deco


def experiment_end(
    _fn: Optional[Callable] = None,
    *,
    name: str = "default",
    output: Optional[Callable] = None,
) -> Callable:
    """Mark the STOP point of an experiment subject (emit shape).

    Captures the output here. In REPLAY, unwinds before this function runs so its
    (potentially production) side effects do NOT fire. Inert unless activated.

    :param output: ``(args, kwargs) -> value`` extracting the semantic output from what
        the end function was called with.
    """

    def deco(fn: Callable) -> Callable:
        _spec(name).update(end=fn, end_output=output)

        def _handle(args: tuple, kwargs: dict) -> None:
            out = _end_output(output, args, kwargs)
            if _mode is Mode.REPLAY:
                raise _ExperimentStop(out)  # capture + unwind before side effects
            case = _current_case.get()  # CAPTURE
            if case is not None:
                case["reached_end"] = True
                _record_case(name, case["inputs"], out)

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                if _mode is Mode.OFF:
                    return await fn(*args, **kwargs)
                _handle(args, kwargs)  # raises in REPLAY
                return await fn(*args, **kwargs)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _mode is Mode.OFF:
                return fn(*args, **kwargs)
            _handle(args, kwargs)  # raises in REPLAY
            return fn(*args, **kwargs)

        return wrapper

    return deco(_fn) if _fn is not None else deco
