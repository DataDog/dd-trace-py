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
from urllib.parse import quote
import uuid

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


def compare_url(baseline: Any, current: Any, project_name: Optional[str] = None) -> Optional[str]:
    """URL for the Experiments compare view: the ``baseline`` run with ``current`` as the target.

    The baseline experiment is the page (first id); the current run is the compare target:
    ``/llm/experiments/{baseline}?compareTargetExperimentId={current}&project=...``.
    """
    try:
        from ddtrace.llmobs._experiment import _get_base_url

        b, c = _experiment_id(baseline), _experiment_id(current)
        if not (b and c):
            return None
        url = "%s/llm/experiments/%s?compareTargetExperimentId=%s" % (_get_base_url(), b, c)
        if project_name:
            url += "&project=%s" % quote(project_name, safe="")
        return url
    except Exception:
        log.debug("inline experiment: could not build compare url", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# Unified `run --publish`: each run is a real Experiment.
#
# First publish -> `publish_baseline`: a fresh Dataset (unique name, no accumulation) + the
# boundary run through the engine as the BASELINE experiment (real spans -> cost, scored by
# the subject's own quality evaluators). Its produced outputs are backfilled as the dataset's
# ``expected_output`` so a later rerun has something to regress against.
# Rerun publish -> `publish_current`: the SAME dataset, the current code run as the CURRENT
# experiment, scored by the regression guard + the subject's evaluators, linked to the
# baseline via the compare view. Correlation is by explicit dataset name / experiment id
# (persisted in the local baseline file by the caller) — there is no sidecar.
# --------------------------------------------------------------------------- #
def _unique_dataset_name(name: str) -> str:
    """A fresh dataset name per capture so repeated publishes never accumulate records."""
    return "inline-experiment-%s-%s" % (name, uuid.uuid4().hex[:8])


def compare_url_from_ids(
    baseline_id: Optional[str], current_id: Optional[str], project_name: Optional[str] = None
) -> Optional[str]:
    """Compare-view URL from two experiment ids: baseline is the page, current the target."""
    if not (baseline_id and current_id):
        return None
    try:
        from ddtrace.llmobs._experiment import _get_base_url

        url = "%s/llm/experiments/%s?compareTargetExperimentId=%s" % (_get_base_url(), baseline_id, current_id)
        if project_name:
            url += "&project=%s" % quote(project_name, safe="")
        return url
    except Exception:
        log.debug("inline experiment: could not build compare url", exc_info=True)
        return None


def _rows_pairs(run: Any) -> list[tuple[Any, Any]]:
    """(input, output) for each non-errored row of an ExperimentRun."""
    pairs: list[tuple[Any, Any]] = []
    for row in getattr(run, "rows", None) or []:
        if row.get("error"):
            continue
        pairs.append((row.get("input"), row.get("output")))
    return pairs


def _backfill_expected(dataset: Any, pairs: list[tuple[Any, Any]]) -> None:
    """Set each dataset record's ``expected_output`` to the output the baseline produced.

    Matches by input (json key) rather than position, then pushes once. Best-effort: a push
    failure is logged, not raised (the experiment already ran).
    """
    by_input = {json.dumps(i, sort_keys=True, default=str): o for i, o in pairs}
    changed = False
    for idx in range(len(dataset)):
        key = json.dumps(dataset[idx].get("input_data"), sort_keys=True, default=str)
        if key in by_input:
            dataset.update(idx, {"expected_output": by_input[key]})
            changed = True
    if not changed:
        return
    try:
        dataset.push()
    except Exception:
        log.debug("inline experiment: could not push backfilled expected_output", exc_info=True)


def _baseline_marker() -> Callable[..., Any]:
    """A trivial evaluator for the baseline when the subject has no evaluators of its own.

    The baseline has no prior run to regress against, so ``regression_match`` would be
    meaningless there — but the engine requires at least one evaluator. This records that an
    output was produced, nothing more.
    """

    def baseline_recorded(input_data: Any, output_data: Any, expected_output: Any) -> Any:
        from ddtrace.llmobs._experiment import EvaluatorResult

        return EvaluatorResult(value=output_data is not None, assessment="recorded")

    return baseline_recorded


def publish_baseline(
    name: str,
    inputs: list[Any],
    project_name: Optional[str] = None,
    experiment_name: Optional[str] = None,
    dataset_name: Optional[str] = None,
) -> dict[str, Any]:
    """First publish: run the subject over ``inputs`` as the BASELINE experiment.

    Creates a fresh dataset (input_data only), runs the real boundary through the engine
    (spans -> cost) scored by the subject's own evaluators (no regression guard — nothing to
    regress against yet), then backfills each record's ``expected_output`` with the produced
    output. Returns ``{experiment, experiment_id, url, dataset, dataset_name, pairs}``.
    Requires ``LLMObs`` to be enabled.
    """
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._experiment import DatasetRecordNew

    ds_name = dataset_name or _unique_dataset_name(name)
    records: list[DatasetRecordNew] = [{"input_data": i} for i in inputs]
    dataset = LLMObs.create_dataset(
        ds_name,
        project_name=project_name,
        description="Inline-experiment baseline for %r." % name,
        records=records,
    )
    spec = _REGISTRY.get(name, {})
    evaluators = resolve_evaluators(spec.get("evaluators")) or [_baseline_marker()]
    experiment = LLMObs.experiment(
        experiment_name or ("%s-baseline" % name),
        _make_task(name),
        dataset,
        evaluators=evaluators,
        project_name=project_name,
        tags={"source": "ddtrace-experiment", "inline_role": "baseline"},
    )
    _set_mode(Mode.REPLAY)  # so an emit-shape end marker unwinds; a single-function subject is unaffected
    try:
        run = experiment.run()
    finally:
        _set_mode(Mode.OFF)
    pairs = _rows_pairs(run)
    _backfill_expected(dataset, pairs)
    return {
        "experiment": experiment,
        "experiment_id": _experiment_id(experiment),
        "url": experiment_url(experiment),
        "dataset": dataset,
        "dataset_name": ds_name,
        "pairs": pairs,
    }


def publish_current(
    name: str,
    dataset_name: str,
    comparator: Callable[[Any, Any], bool],
    baseline_experiment_id: Optional[str] = None,
    project_name: Optional[str] = None,
    experiment_name: Optional[str] = None,
) -> dict[str, Any]:
    """Rerun publish: run the current code over the baseline's dataset as the CURRENT experiment.

    Pulls the shared dataset (so ``expected_output`` = the baseline's output), runs the
    boundary through the engine scored by the regression guard + the subject's evaluators, and
    builds the compare-view URL against ``baseline_experiment_id``. Returns
    ``{experiment, experiment_id, url, dataset, compare_url}``.
    """
    from ddtrace.llmobs import LLMObs

    dataset = LLMObs.pull_dataset(dataset_name, project_name=project_name)
    spec = _REGISTRY.get(name, {})
    evaluators = [_make_evaluator(comparator), *resolve_evaluators(spec.get("evaluators"))]
    experiment = LLMObs.experiment(
        experiment_name or name,
        _make_task(name),
        dataset,
        evaluators=evaluators,
        project_name=project_name,
        tags={"source": "ddtrace-experiment", "inline_role": "current"},
    )
    _set_mode(Mode.REPLAY)
    try:
        experiment.run()
    finally:
        _set_mode(Mode.OFF)
    current_id = _experiment_id(experiment)
    return {
        "experiment": experiment,
        "experiment_id": current_id,
        "url": experiment_url(experiment),
        "dataset": dataset,
        "compare_url": compare_url_from_ids(baseline_experiment_id, current_id, project_name),
    }


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
