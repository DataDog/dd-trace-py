"""Bridge: run inline-experiment baselines through the real LLM Obs Experiments SDK.

Turns captured baselines into a ``Dataset``, replays the decorated subject as the
experiment *task*, and scores each case with the comparator (wrapped as an evaluator) —
so the run appears in **LLM Observability -> Experiments** with eval metrics, instead of
only a local terminal diff.

This reuses the existing engine (``LLMObs.create_dataset`` / ``LLMObs.experiment`` /
``_experiment.py``) rather than reimplementing it.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._inline_experiment import _REGISTRY
from ddtrace.llmobs._inline_experiment import Mode
from ddtrace.llmobs._inline_experiment import _ExperimentStop
from ddtrace.llmobs._inline_experiment import _set_mode
from ddtrace.llmobs._inline_experiment import _start_output
from ddtrace.llmobs._inline_experiment_runner import _invoke
from ddtrace.llmobs._inline_experiment_runner import as_evaluator
from ddtrace.llmobs._inline_experiment_runner import resolve_evaluators


log = get_logger(__name__)


def _make_task(name: str) -> Callable[..., Any]:
    """An Experiment task that replays the subject: re-invoke its entry with the
    record's ``input_data`` and return the produced output.
    """
    spec = _REGISTRY[name]
    has_end = "end" in spec
    start_output_fn = spec.get("start_output")

    def task(input_data: Any, config: Any) -> Any:
        try:
            ret = _invoke(spec, input_data)
        except _ExperimentStop as stop:  # emit shape: end unwound with the output
            return stop.output
        if has_end:
            return None  # the end marker never fired
        return _start_output(start_output_fn, ret)  # single-function unit

    return task


def _make_evaluator(comparator: Callable[[Any, Any], bool]) -> Callable[..., Any]:
    """Wrap a comparator as the always-on ``regression_match`` guard evaluator.

    Thin alias over the shared ``as_evaluator`` adapter so the guard and any
    user-attached ``comparison(...)`` share one comparator->evaluator implementation.
    """
    return as_evaluator(comparator, name="regression_match")


def _baseline_task(cases: list[dict[str, Any]]) -> Callable[..., Any]:
    """A task that echoes the captured (recorded) output for each case.

    This is the **baseline reference run**: it re-runs nothing and makes no model calls —
    it just returns what capture recorded, keyed by the record's input. Publishing it as
    its own experiment lets the Experiments *compare* view diff baseline vs the current
    replay immediately, so a user sees value the moment they run ``--publish``.
    """
    by_input = {json.dumps(c["input"], sort_keys=True, default=str): c["output"] for c in cases}

    def task(input_data: Any, config: Any) -> Any:
        return by_input.get(json.dumps(input_data, sort_keys=True, default=str))

    return task


def experiment_url(experiment: Any) -> Optional[str]:
    return getattr(experiment, "url", None) or getattr(getattr(experiment, "_experiment", None), "url", None)


def _experiment_id(experiment: Any) -> Optional[str]:
    eid = getattr(experiment, "_id", None) or getattr(getattr(experiment, "_experiment", None), "_id", None)
    return str(eid) if eid else None


def dataset_url(dataset: Any) -> Optional[str]:
    return getattr(dataset, "url", None)


def compare_url(baseline: Any, current: Any) -> Optional[str]:
    """Best-effort URL for the Experiments *compare* view (baseline vs current).

    FIXME: confirm the exact frontend route for the compare view — it is not defined in
    dd-trace-py (the engine only exposes per-experiment URLs). Centralized here so it's a
    one-line fix once verified against the live UI.
    """
    try:
        from ddtrace.llmobs._experiment import _get_base_url

        b, c = _experiment_id(baseline), _experiment_id(current)
        if not (b and c):
            return None
        return "%s/llm/experiments/compare?experiments=%s,%s" % (_get_base_url(), b, c)
    except Exception:
        log.debug("inline experiment: could not build compare url", exc_info=True)
        return None


def run_as_experiment(
    name: str,
    cases: list[dict[str, Any]],
    comparator: Callable[[Any, Any], bool],
    experiment_name: Optional[str] = None,
    project_name: Optional[str] = None,
    dataset_name: Optional[str] = None,
    publish_baseline: bool = True,
) -> dict[str, Any]:
    """Create a Dataset from ``cases`` and publish it as LLM Obs experiment(s).

    Publishes two runs over the same dataset so the compare view has something to diff
    immediately:
      * ``baseline`` — echoes the captured outputs (no subject re-run, no model calls); the
        known-good reference. Skipped when ``publish_baseline`` is False.
      * ``current`` — replays the subject against the captured inputs (real re-execution).

    Both are scored by the same evaluators (the always-on ``regression_match`` guard first,
    then the subject's attached evaluators, resolved lazily here — never at import). Returns
    ``{"current", "baseline", "dataset"}``. Requires ``LLMObs`` to be enabled.
    """
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._experiment import DatasetRecordNew

    records: list[DatasetRecordNew] = [{"input_data": c["input"], "expected_output": c["output"]} for c in cases]
    dataset = LLMObs.create_dataset(
        dataset_name or ("inline-experiment-%s" % name),
        project_name=project_name,
        description="Inline-experiment baseline for %r (captured locally)." % name,
        records=records,
    )
    spec = _REGISTRY.get(name, {})
    evaluators = [_make_evaluator(comparator), *resolve_evaluators(spec.get("evaluators"))]
    base_name = experiment_name or ("inline-%s" % name)

    # Baseline reference run first (mode stays OFF — the task only echoes stored outputs,
    # the subject is never invoked), so the compare view can diff it against `current`.
    baseline = None
    if publish_baseline:
        baseline = LLMObs.experiment(
            base_name + "-baseline",
            _baseline_task(cases),
            dataset,
            evaluators=evaluators,
            project_name=project_name,
            tags={"source": "ddtrace-experiment", "inline_role": "baseline"},
        )
        baseline.run()

    # Current run: replay the subject. REPLAY so an emit-shape end marker unwinds (set once;
    # the engine may run tasks concurrently, and a single-function subject is unaffected).
    current = LLMObs.experiment(
        base_name,
        _make_task(name),
        dataset,
        evaluators=evaluators,
        project_name=project_name,
        tags={"source": "ddtrace-experiment", "inline_role": "current"},
    )
    _set_mode(Mode.REPLAY)
    try:
        current.run()
    finally:
        _set_mode(Mode.OFF)
    return {"current": current, "baseline": baseline, "dataset": dataset}
