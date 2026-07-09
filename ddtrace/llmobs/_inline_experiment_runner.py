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


# Publish state (baseline experiment id, dataset name) is remembered between the separate
# `capture --publish` and `replay --publish` invocations in a sidecar next to the baseline,
# so replay can link the compare view to the baseline experiment capture published.
def _publish_state_path(baseline_path: str) -> str:
    return baseline_path + ".publish.json"


def save_publish_state(baseline_path: str, name: str, **fields: Any) -> None:
    path = _publish_state_path(baseline_path)
    state: dict[str, Any] = {}
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state[name] = {**state.get(name, {}), **fields}
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def load_publish_state(baseline_path: str, name: str) -> Optional[dict[str, Any]]:
    try:
        with open(_publish_state_path(baseline_path)) as f:
            state: dict[str, Any] = json.load(f)
    except (OSError, ValueError):
        return None
    value = state.get(name)
    return value if isinstance(value, dict) else None


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
# Comparisons-as-evaluators — the unified surface.
# A comparator is `(recorded, new) -> bool`; an evaluator is
# `(input_data, output_data, expected_output) -> bool | EvaluatorResult`. `as_evaluator`
# is the single adapter between the two (recorded baseline == expected_output, new ==
# output_data). The always-on guard and any user-attached comparison both go through it,
# so "a comparison" is just one kind of evaluator. See RFC §"Comparisons as evaluators".
# --------------------------------------------------------------------------- #
def as_evaluator(comparator: Callable[[Any, Any], bool], name: str = "regression_match") -> Callable[..., Any]:
    """Wrap a `(recorded, new)` comparator as a `(input, output, expected)` evaluator.

    A comparison is just an evaluator whose assessment is ``match`` / ``changed`` (rather than
    a judge's ``pass`` / ``fail``) — so the familiar regression vocabulary survives as a label
    without a privileged status. The evaluator's ``__name__`` becomes the eval-metric label.
    """

    def _comparison(input_data: Any, output_data: Any, expected_output: Any) -> Any:
        from ddtrace.llmobs._experiment import EvaluatorResult

        matched = bool(comparator(expected_output, output_data))
        return EvaluatorResult(value=matched, assessment="match" if matched else "changed")

    _comparison.__name__ = name
    return _comparison


def comparison(
    kind: str = "structural", ignore: Optional[list[str]] = None, name: Optional[str] = None
) -> Callable[..., Any]:
    """A built-in comparison as an evaluator, droppable into ``evaluators=``.

    The generalized form of the ``--comparator`` knob: ``comparison("exact")``,
    ``comparison("ignoring", ignore=["ts"])``, ``comparison()`` (structural). Reports a
    ``match`` / ``changed`` assessment. Defaults the metric label to ``"<kind>_match"`` so
    multiple comparisons don't collide; the default comparison keeps ``"regression_match"``.
    """
    return as_evaluator(comparator_from_spec(kind, ignore), name or ("%s_match" % kind))


# --------------------------------------------------------------------------- #
# Evaluators — richer per-case checks that stack behind the comparator guard.
# AIDEV-NOTE: `evaluate_one` deliberately MIRRORS the engine's per-type evaluator
# dispatch (`Experiment._evaluate_record._run_single_evaluator` in _experiment.py) so a
# local `replay --evaluate` yields the same verdict as `replay --publish` (which routes
# through the engine). The engine path is intentionally left untouched — it carries
# retries / semaphore / instance state we don't need locally. If the engine's dispatch
# changes, keep this in sync. See RFC "Replay data sources" / Open Question #5.
# --------------------------------------------------------------------------- #
def resolve_evaluators(evaluators: Any) -> list[Any]:
    """Resolve the decorator's lazy ``evaluators`` into a concrete list.

    Accepts a list/tuple (used as-is) or a zero-arg callable returning a list (called
    here — never at import, so a credentialed evaluator is built only by an activated
    runner). ``None`` -> ``[]``.
    """
    if evaluators is None:
        return []
    if isinstance(evaluators, (list, tuple)):
        return list(evaluators)
    if callable(evaluators):
        produced = evaluators()
        return list(produced) if produced else []
    return [evaluators]


def _normalize_eval_result(name: str, result: Any) -> list[dict[str, Any]]:
    """Flatten an evaluator return into one or more verdict rows.

    Mirrors the engine: a ``MultiEvaluatorResult`` emits one row per sub-value, labeled
    ``<name>-<key>`` (or the bare key when ``prefix=False``); an ``EvaluatorResult`` carries
    its value/assessment/reasoning; a bare bool becomes pass/fail.
    """
    from ddtrace.llmobs._experiment import EvaluatorResult
    from ddtrace.llmobs._experiment import MultiEvaluatorResult

    if isinstance(result, MultiEvaluatorResult):
        rows: list[dict[str, Any]] = []
        for key, sub in result.values.items():
            label = ("%s-%s" % (name, key)) if result.prefix else key
            rows.extend(_normalize_eval_result(label, sub))
        return rows
    if isinstance(result, EvaluatorResult):
        return [
            {
                "name": name,
                "value": result.value,
                "assessment": result.assessment,
                "reasoning": result.reasoning,
                "error": None,
            }
        ]
    assessment = ("pass" if result else "fail") if isinstance(result, bool) else None
    return [{"name": name, "value": result, "assessment": assessment, "reasoning": None, "error": None}]


def evaluate_one(evaluator: Any, recorded: Any, new: Any, input_data: Any) -> list[dict[str, Any]]:
    """Score a single evaluator for one replayed case; return one or more verdict rows.

    Each row is ``{name, value, assessment, reasoning, error}``. One evaluator yields one row,
    except a ``MultiEvaluatorResult`` which expands to one row per sub-metric (matching the
    ``--publish`` engine path). The recorded baseline is the evaluator's ``expected_output``;
    the new output is ``output_data``.
    """
    from ddtrace.llmobs._experiment import BaseAsyncEvaluator
    from ddtrace.llmobs._experiment import EvaluatorContext
    from ddtrace.llmobs._experiment import _is_class_evaluator
    from ddtrace.llmobs._experiment import _is_function_evaluator

    name = getattr(evaluator, "name", None) or getattr(evaluator, "__name__", None) or repr(evaluator)
    context = EvaluatorContext(input_data=input_data, output_data=new, expected_output=recorded)
    try:
        if isinstance(evaluator, BaseAsyncEvaluator):
            result: Any = asyncio.run(evaluator.evaluate(context))
        elif _is_class_evaluator(evaluator):  # BaseEvaluator, including LLMJudge
            result = evaluator.evaluate(context)
        elif asyncio.iscoroutinefunction(evaluator):
            result = asyncio.run(evaluator(input_data, new, recorded))
        elif _is_function_evaluator(evaluator):
            result = evaluator(input_data, new, recorded)
        else:
            return [{"name": name, "value": None, "assessment": None, "reasoning": None, "error": "unsupported type"}]
    except Exception as e:  # noqa: BLE001 - surface eval errors per-row, never abort the replay
        return [{"name": name, "value": None, "assessment": None, "reasoning": None, "error": "%r" % (e,)}]

    return _normalize_eval_result(name, result)


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
    name: str,
    comparator: Optional[Callable[[Any, Any], bool]] = None,
    cases: Optional[list[dict[str, Any]]] = None,
    score_evaluators: bool = False,
) -> list[dict[str, Any]]:
    """Re-drive a subject over its captured cases; return per-case result rows.

    There is no privileged comparator status: each row has an *execution* status
    ``exec`` (``OK`` / ``ERROR`` / ``NO_END`` — did we get an output to judge) and a list of
    *evaluator* verdicts ``evals`` (``{name, value, assessment, reasoning, error}``). The
    comparison is just the **default evaluator**: ``comparator`` (default ``structural``,
    reported as ``match`` / ``changed``) runs on every ``OK`` row. When ``score_evaluators``
    is set, the subject's attached ``evaluators`` (resolved lazily) are scored too — stacked
    after the default comparison.
    """
    spec = _REGISTRY.get(name, {})
    entry = spec.get("start")
    if entry is None:
        raise RuntimeError("No experiment_start registered for %r. Did you import the app module?" % name)
    has_end = "end" in spec
    start_output_fn = spec.get("start_output")
    cases = cases if cases is not None else spec.get("cases", [])
    # The default comparison evaluator (structural unless overridden) always runs; attached
    # evaluators stack after it and are resolved lazily only when explicitly scoring.
    default_eval = as_evaluator(comparator or structural)
    attached = resolve_evaluators(spec.get("evaluators")) if score_evaluators else []
    evaluators = [default_eval, *attached]

    results: list[dict[str, Any]] = []
    for case in cases:
        recorded = _normalize(case["output"])
        row: dict[str, Any] = {"input": case["input"], "recorded": recorded, "new": None, "exec": "NO_END", "evals": []}
        try:
            ret = _invoke(spec, case["input"])
        except _ExperimentStop as stop:  # emit shape: end unwound with the output
            new = stop.output
        except Exception as e:  # noqa: BLE001 - surface task errors as a row rather than abort
            row["new"] = "<error: %r>" % (e,)
            row["exec"] = "ERROR"
            results.append(row)
            continue
        else:
            if has_end:
                # the end marker never fired this replay -> leave exec as NO_END, nothing to judge
                results.append(row)
                continue
            new = _start_output(start_output_fn, ret)  # single-function unit: return is the output
        row["new"] = _normalize(new)
        row["exec"] = "OK"
        # Default comparison + any attached evaluators, scored against the (recorded -> new) pair.
        row["evals"] = [v for ev in evaluators for v in evaluate_one(ev, recorded, row["new"], case["input"])]
        results.append(row)
    return results
