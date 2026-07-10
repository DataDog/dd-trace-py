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


def _dataset_name(name: str) -> str:
    return "inline-experiment-%s" % name


def _unique_dataset_name(name: str) -> str:
    """A fresh dataset name per capture. `create_dataset` reuses+appends on an existing name
    (accumulating across runs), so each capture gets its own dataset — replay then pulls that
    exact one and runs over only the examples this capture recorded.
    """
    return "%s-%s" % (_dataset_name(name), uuid.uuid4().hex[:8])


def experiment_url(experiment: Any) -> Optional[str]:
    return getattr(experiment, "url", None) or getattr(getattr(experiment, "_experiment", None), "url", None)


def _experiment_id(experiment: Any) -> Optional[str]:
    eid = getattr(experiment, "_id", None) or getattr(getattr(experiment, "_experiment", None), "_id", None)
    return str(eid) if eid else None


def dataset_url(dataset: Any) -> Optional[str]:
    return getattr(dataset, "url", None)


def compare_url_from_ids(
    baseline_id: Optional[str], current_id: Optional[str], project_name: Optional[str] = None
) -> Optional[str]:
    """URL for the Experiments compare view: baseline is the page, current the compare target.

    ``/llm/experiments/{baseline}?compareTargetExperimentId={current}&project=...``
    """
    try:
        from ddtrace.llmobs._experiment import _get_base_url

        if not (baseline_id and current_id):
            return None
        url = "%s/llm/experiments/%s?compareTargetExperimentId=%s" % (_get_base_url(), baseline_id, current_id)
        if project_name:
            url += "&project=%s" % quote(project_name, safe="")
        return url
    except Exception:
        log.debug("inline experiment: could not build compare url", exc_info=True)
        return None


def compare_url(baseline: Any, current: Any, project_name: Optional[str] = None) -> Optional[str]:
    """Compare-view URL from two experiment objects (baseline vs current)."""
    return compare_url_from_ids(_experiment_id(baseline), _experiment_id(current), project_name)


def _backfill_expected_output(dataset: Any, name: str) -> None:
    """Set each dataset record's ``expected_output`` to the output captured for its input.

    The baseline run (below) records ``(input, output)`` cases as it executes; matching them
    back onto the dataset by input (order can differ under concurrency) makes the dataset the
    shared golden reference, so a later ``current`` run's ``regression_match`` compares against
    it. Best-effort — never fail the publish over a backfill hiccup.
    """
    from ddtrace.llmobs._inline_experiment import captured_cases

    try:
        by_input = {json.dumps(c["input"], sort_keys=True, default=str): c["output"] for c in captured_cases(name)}
        changed = False
        for i, record in enumerate(dataset):
            key = json.dumps(record.get("input_data"), sort_keys=True, default=str)
            if key in by_input:
                dataset.update(i, {"expected_output": by_input[key]})
                changed = True
        if changed:
            dataset.push()
    except Exception:
        log.debug("inline experiment: could not backfill baseline expected_output", exc_info=True)


def publish_baseline(
    name: str,
    inputs: list[dict[str, Any]],
    project_name: Optional[str] = None,
    experiment_name: Optional[str] = None,
    dataset_name: Optional[str] = None,
) -> dict[str, Any]:
    """``capture --publish``: run the REAL boundary as the ``<name>-baseline`` experiment.

    Creates a fresh dataset (unique name, so it never accumulates records from other captures)
    from ``inputs`` (kwargs dicts, e.g. ``{"tickers": [...]}``) and runs the actual subject over
    them through the engine — a single real run, so the baseline carries real spans + token/dollar
    cost. CAPTURE mode records the produced ``(input, output)`` cases (read via ``captured_cases``
    to persist locally), and the dataset's ``expected_output`` is backfilled from them so a later
    ``current`` run compares against this baseline. Baseline evaluators are the subject's attached
    evaluators only (no ``regression_match`` — nothing to regress against yet). Returns
    ``{"baseline", "dataset", "dataset_name"}`` (persist ``dataset_name`` so replay pulls this
    exact dataset).
    """
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._experiment import DatasetRecordNew

    ds_name = dataset_name or _unique_dataset_name(name)
    records: list[DatasetRecordNew] = [{"input_data": inp} for inp in inputs]
    dataset = LLMObs.create_dataset(
        ds_name,
        project_name=project_name,
        description="Inline-experiment baseline for %r (captured from a real run)." % name,
        records=records,
    )
    spec = _REGISTRY.get(name, {})
    baseline = LLMObs.experiment(
        (experiment_name or ("inline-%s" % name)) + "-baseline",
        _make_task(name),
        dataset,
        evaluators=resolve_evaluators(spec.get("evaluators")),
        project_name=project_name,
        tags={"source": "ddtrace-experiment", "inline_role": "baseline"},
    )
    _set_mode(Mode.CAPTURE)  # record (input, output) cases as the real run executes
    try:
        baseline.run()
    finally:
        _set_mode(Mode.OFF)
    _backfill_expected_output(dataset, name)
    return {"baseline": baseline, "dataset": dataset, "dataset_name": ds_name}


def publish_current(
    name: str,
    cases: list[dict[str, Any]],
    comparator: Callable[[Any, Any], bool],
    project_name: Optional[str] = None,
    experiment_name: Optional[str] = None,
    dataset_name: Optional[str] = None,
) -> dict[str, Any]:
    """``replay --publish``: run the CURRENT code as the ``<name>`` experiment.

    Pulls the *specific* dataset published at capture (``dataset_name`` from the sidecar) so it
    runs over exactly the captured examples, falling back to a fresh dataset built from ``cases``
    if none is given or the pull fails. Scored by the ``regression_match`` guard plus the
    subject's attached evaluators. Returns ``{"current", "dataset"}``.
    """
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._experiment import DatasetRecordNew

    dataset = None
    if dataset_name:
        try:
            dataset = LLMObs.pull_dataset(dataset_name, project_name=project_name)
        except Exception:
            log.debug("inline experiment: could not pull dataset %r; building from cases", dataset_name, exc_info=True)
    if dataset is None:
        records: list[DatasetRecordNew] = [{"input_data": c["input"], "expected_output": c["output"]} for c in cases]
        dataset = LLMObs.create_dataset(
            _unique_dataset_name(name),
            project_name=project_name,
            description="Inline-experiment baseline for %r." % name,
            records=records,
        )
    spec = _REGISTRY.get(name, {})
    current = LLMObs.experiment(
        experiment_name or ("inline-%s" % name),
        _make_task(name),
        dataset,
        evaluators=[_make_evaluator(comparator), *resolve_evaluators(spec.get("evaluators"))],
        project_name=project_name,
        tags={"source": "ddtrace-experiment", "inline_role": "current"},
    )
    _set_mode(Mode.REPLAY)
    try:
        current.run()
    finally:
        _set_mode(Mode.OFF)
    return {"current": current, "dataset": dataset}
