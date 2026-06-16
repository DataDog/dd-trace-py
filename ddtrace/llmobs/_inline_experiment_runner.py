"""Runner + comparators for inline experiments.

Drives REPLAY: re-invokes a registered experiment subject with each captured input
(rebuilding live infra via its ``fixtures`` hook) and compares the new output against
the recorded baseline using a comparator.

This is the fully-local runner (no backend). The Experiment-SDK bridge — capture ->
``Dataset`` -> ``LLMObs.experiment()`` so results land in the Experiments UI — is added
in a later slice; the comparators here are reused there as evaluators.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._inline_experiment import _REGISTRY
from ddtrace.llmobs._inline_experiment import _ExperimentStop
from ddtrace.llmobs._inline_experiment import _start_output


log = get_logger(__name__)

DEFAULT_BASELINE_PATH = ".llmobs_experiments.json"


def _normalize(value: Any) -> Any:
    """JSON round-trip so captured (raw) and replayed values compare on equal footing
    (and persisted baselines load back identically).
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return value


def save_baselines(path: str = DEFAULT_BASELINE_PATH) -> dict[str, Any]:
    """Persist all captured baselines ({experiment_name: [cases]}) to a JSON file."""
    data = {name: spec.get("cases", []) for name, spec in _REGISTRY.items() if spec.get("cases")}
    with open(path, "w") as f:
        json.dump(data, f, default=str, indent=2)
    return data


def load_baselines(path: str = DEFAULT_BASELINE_PATH) -> dict[str, Any]:
    """Load persisted baselines written by ``save_baselines``."""
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    return data


# --------------------------------------------------------------------------- #
# Comparators — "is the new output equivalent to the recorded baseline?"
# Real LLM output is non-deterministic, so `exact` reports CHANGED on every replay;
# `ignoring`/`structural` make replay meaningful.
# --------------------------------------------------------------------------- #
def exact(recorded: Any, new: Any) -> bool:
    """Strict equality. Deterministic outputs only."""
    return bool(recorded == new)


def _deep_drop(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {k: _deep_drop(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_deep_drop(v, keys) for v in value]
    return value


def ignoring(*keys: str) -> Callable[[Any, Any], bool]:
    """Equality after recursively dropping volatile keys (e.g. ``generated_at``)."""
    keyset = set(keys)

    def cmp(recorded: Any, new: Any) -> bool:
        return bool(_deep_drop(recorded, keyset) == _deep_drop(new, keyset))

    return cmp


def _shape(value: Any) -> Any:
    """Reduce a value to its structure: dict keys, list lengths, and leaf *types*
    (not leaf values). Free-text/number drift is ignored; a missing field, a changed
    type, or a dropped list element shows up.
    """
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__


def structural(recorded: Any, new: Any) -> bool:
    """Equivalent if the output *shape* matches (keys / types / list-lengths)."""
    return bool(_shape(recorded) == _shape(new))


def comparator_from_spec(kind: str = "structural", ignore: Optional[list[str]] = None) -> Callable[[Any, Any], bool]:
    """Build a comparator from CLI-style options."""
    if ignore:
        return ignoring(*ignore)
    if kind == "exact":
        return exact
    if kind == "structural":
        return structural
    raise ValueError("unknown comparator %r (use exact|structural|ignoring)" % kind)


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #
def _invoke(spec: dict[str, Any], input_kwargs: dict[str, Any]) -> Any:
    """Call the entry with captured inputs + freshly-built live fixtures. Handles async."""
    entry = spec["start"]
    fixtures = spec.get("fixtures")
    if asyncio.iscoroutinefunction(entry):

        async def _run() -> Any:
            fx = fixtures() if fixtures else {}
            if inspect.iscoroutine(fx):
                fx = await fx
            return await entry(**fx, **input_kwargs)

        return asyncio.run(_run())

    fx = fixtures() if fixtures else {}
    if inspect.iscoroutine(fx):
        raise RuntimeError("async `fixtures` requires an async entry function")
    return entry(**fx, **input_kwargs)


def replay(
    name: str, comparator: Callable[[Any, Any], bool] = exact, cases: Optional[list[dict[str, Any]]] = None
) -> list[dict[str, Any]]:
    """Re-drive a subject over its captured cases; return per-case result rows.

    Each row: ``{input, recorded, new, status}`` where status is
    MATCH / CHANGED / ERROR / NO_END.
    """
    spec = _REGISTRY.get(name, {})
    entry = spec.get("start")
    if entry is None:
        raise RuntimeError("No experiment_start registered for %r. Did you import the app module?" % name)
    has_end = "end" in spec
    start_output_fn = spec.get("start_output")
    cases = cases if cases is not None else spec.get("cases", [])

    results: list[dict[str, Any]] = []
    for case in cases:
        recorded = _normalize(case["output"])
        row: dict[str, Any] = {"input": case["input"], "recorded": recorded, "new": None, "status": "NO_END"}
        try:
            ret = _invoke(spec, case["input"])
        except _ExperimentStop as stop:  # emit shape: end unwound with the output
            new = stop.output
        except Exception as e:  # noqa: BLE001 - surface task errors as a row rather than abort
            row["new"] = "<error: %r>" % (e,)
            row["status"] = "ERROR"
            results.append(row)
            continue
        else:
            if has_end:
                # the end marker never fired this replay -> leave status as NO_END
                results.append(row)
                continue
            new = _start_output(start_output_fn, ret)  # single-function unit: return is the output
        row["new"] = _normalize(new)
        row["status"] = "MATCH" if comparator(recorded, row["new"]) else "CHANGED"
        results.append(row)
    return results
