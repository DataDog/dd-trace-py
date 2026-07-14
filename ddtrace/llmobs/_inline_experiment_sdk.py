"""Bridge: run an inline-experiment subject through the real LLM Obs Experiments SDK.

Runs ``run --publish``: syncs the subject's auto-managed ``Dataset`` to the current inputs,
runs the decorated subject as the experiment *task* scored by the subject's own evaluators,
and links the compare view — so the run appears in **LLM Observability -> Experiments** with
cost + eval metrics, instead of only a local terminal diff.

This reuses the existing engine (``LLMObs.create_dataset`` / ``LLMObs.pull_dataset`` /
``LLMObs.experiment`` / ``_experiment.py``) rather than reimplementing it.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Callable
from typing import Optional
from urllib.parse import quote

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._inline_experiment import _REGISTRY
from ddtrace.llmobs._inline_experiment import Mode
from ddtrace.llmobs._inline_experiment import _ExperimentStop
from ddtrace.llmobs._inline_experiment import _set_mode
from ddtrace.llmobs._inline_experiment import _start_output
from ddtrace.llmobs._inline_experiment_runner import _invoke
from ddtrace.llmobs._inline_experiment_runner import resolve_evaluators


log = get_logger(__name__)


class PublishAbort(RuntimeError):
    """Abort a ``--publish`` run *before* the (paid) experiment runs.

    Raised when proceeding would produce a misleading result — e.g. the dataset sync could
    not be persisted, so the experiment would run over stale/unpersisted data. The CLI turns
    this into a friendly message + non-zero exit rather than recording a bad baseline.
    """


def experiment_exists(experiment_id: Optional[str]) -> bool:
    """Best-effort check that a frozen baseline experiment id still resolves in the backend.

    Used to detect a dead baseline pointer (experiment deleted in the UI) so the caller can
    suggest re-recording instead of printing a compare link that 404s.
    """
    if not experiment_id:
        return False
    from ddtrace.llmobs import LLMObs

    try:
        return LLMObs.pull_experiment(experiment_id) is not None
    except Exception:
        log.debug("inline experiment: baseline experiment %r not resolvable", experiment_id, exc_info=True)
        return False


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


def experiment_url(experiment: Any) -> Optional[str]:
    return getattr(experiment, "url", None) or getattr(getattr(experiment, "_experiment", None), "url", None)


def _experiment_id(experiment: Any) -> Optional[str]:
    eid = getattr(experiment, "_id", None) or getattr(getattr(experiment, "_experiment", None), "_id", None)
    return str(eid) if eid else None


def dataset_url(dataset: Any) -> Optional[str]:
    """The dataset's UI URL (``.../llm/datasets/<id>``), or None if not resolvable."""
    try:
        url = dataset.url
        return str(url) if url else None
    except Exception:
        log.debug("inline experiment: could not resolve dataset url", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# Unified `run --publish`: each run is a real Experiment (the "refresh" model).
#
# One STABLE dataset per subject (``inline-experiment-<subject>``). Every publish run
# ``sync_dataset``s it to EXACTLY this run's inputs (add missing / delete removed / keep
# unchanged records with their ids, so the compare view lines up), then runs the boundary
# through the engine as a timestamped experiment scored by the subject's OWN evaluators
# (no regression guard — the regression signal is the compare view). The FIRST publish is
# the frozen baseline; every later run compares current-vs-baseline. Correlation (dataset
# name + baseline experiment id) is persisted in the local baseline file by the caller —
# there is no sidecar.
# --------------------------------------------------------------------------- #
def _dataset_name(name: str) -> str:
    """The one stable dataset for a subject (overwritten in place each publish run)."""
    return "inline-experiment-%s" % name


def _timestamp() -> str:
    """A sortable run stamp so each run is a distinct experiment in the UI."""
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M%S")


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


def _baseline_marker() -> Callable[..., Any]:
    """A trivial evaluator used when the subject declares none of its own.

    The engine requires at least one evaluator; this just records that an output was
    produced. Real regression signal comes from the compare view, not an in-experiment check.
    """

    def output_present(input_data: Any, output_data: Any, expected_output: Any) -> Any:
        from ddtrace.llmobs._experiment import EvaluatorResult

        return EvaluatorResult(value=output_data is not None, assessment="recorded")

    return output_present


def _input_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _pull_or_create_dataset(dataset_name: str, project_name: Optional[str]) -> Any:
    """The subject's stable dataset, created empty if it doesn't exist yet."""
    from ddtrace.llmobs import LLMObs

    try:
        return LLMObs.pull_dataset(dataset_name, project_name=project_name)
    except Exception:
        log.debug("inline experiment: dataset %r not found, creating it", dataset_name, exc_info=True)
        return LLMObs.create_dataset(
            dataset_name, project_name=project_name, description="Inline-experiment set (auto-managed).", records=[]
        )


def sync_dataset(dataset: Any, inputs: list[Any]) -> dict[str, int]:
    """Diff-sync ``dataset`` to hold EXACTLY ``inputs``: add missing, delete removed, keep the
    rest (with their record ids, so the compare view matches unchanged cases across runs).

    Inputs are treated as a **set** — duplicate inputs collapse to a single record (repeats are
    not preserved). Deletes are applied high-index-first (``Dataset.delete`` pops the local
    list). Pushes once as a new version. Returns ``{"added", "deleted", "kept", "pushed"}``;
    ``pushed`` is ``False`` when a needed push to the backend failed (a warning is logged).
    """
    want = {_input_key(i): i for i in inputs}
    have_keys = [_input_key(dataset[idx].get("input_data")) for idx in range(len(dataset))]
    seen: set[str] = set()
    to_delete: list[int] = []
    for idx, key in enumerate(have_keys):
        if key in want and key not in seen:
            seen.add(key)  # keep the first record for each wanted input
        else:
            to_delete.append(idx)  # not wanted, or a duplicate of one we already kept
    for idx in sorted(to_delete, reverse=True):
        dataset.delete(idx)
    added = 0
    for key, value in want.items():
        if key not in seen:
            # Carry an explicit expected_output key (empty) so the record shape matches what the
            # engine reads; the regression signal is still the compare view, not this field.
            dataset.append({"input_data": value, "expected_output": None})
            added += 1
    counts = {"added": added, "deleted": len(to_delete), "kept": len(seen), "pushed": True}
    if added or to_delete:
        try:
            dataset.push()
        except Exception:
            counts["pushed"] = False
            log.warning(
                "inline experiment: failed to push dataset %r to the backend — the experiment "
                "will run against an unpersisted dataset and the compare view may not line up. "
                "Re-run with debug logging for details.",
                getattr(dataset, "name", "?"),
                exc_info=True,
            )
    return counts


def publish_run(
    name: str,
    inputs: list[Any],
    project_name: Optional[str] = None,
    experiment_name: Optional[str] = None,
) -> dict[str, Any]:
    """Run the subject over ``inputs`` as one Experiment (the refresh model).

    Syncs the subject's stable dataset to exactly ``inputs``, then runs the real boundary
    through the engine (spans -> cost) scored by the subject's OWN evaluators. Returns
    ``{experiment, experiment_id, url, dataset, dataset_name, sync, pairs}``. The caller
    decides baseline-vs-compare from its persisted state; this function treats every run the
    same. Requires ``LLMObs`` to be enabled.
    """
    ds_name = _dataset_name(name)
    dataset = _pull_or_create_dataset(ds_name, project_name)
    sync = sync_dataset(dataset, inputs)
    if not sync.get("pushed", True):
        # The dataset couldn't be persisted; abort BEFORE the (paid) experiment run so we don't
        # record a baseline/experiment over stale data. The CLI asks the user to re-run.
        raise PublishAbort(
            "could not persist dataset %r to the backend; aborting before the experiment run. "
            "Re-run `ddtrace-experiment run <target> --publish` (check DD_API_KEY / connectivity)." % ds_name
        )
    spec = _REGISTRY.get(name, {})
    evaluators = resolve_evaluators(spec.get("evaluators")) or [_baseline_marker()]

    from ddtrace.llmobs import LLMObs

    experiment = LLMObs.experiment(
        experiment_name or ("%s-%s" % (name, _timestamp())),
        _make_task(name),
        dataset,
        evaluators=evaluators,
        project_name=project_name,
        tags={"source": "ddtrace-experiment"},
    )
    _set_mode(Mode.REPLAY)  # so an emit-shape end marker unwinds; a single-function subject is unaffected
    try:
        run = experiment.run()
    finally:
        _set_mode(Mode.OFF)
    return {
        "experiment": experiment,
        "experiment_id": _experiment_id(experiment),
        "url": experiment_url(experiment),
        "dataset": dataset,
        "dataset_name": ds_name,
        "dataset_url": dataset_url(dataset),
        "sync": sync,
        "pairs": _rows_pairs(run),
    }
