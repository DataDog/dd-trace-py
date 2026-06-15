"""Bridge: run inline-experiment baselines through the real LLM Obs Experiments SDK.

Turns captured baselines into a ``Dataset``, replays the decorated subject as the
experiment *task*, and scores each case with the comparator (wrapped as an evaluator) —
so the run appears in **LLM Observability -> Experiments** with eval metrics, instead of
only a local terminal diff.

This reuses the existing engine (``LLMObs.create_dataset`` / ``LLMObs.experiment`` /
``_experiment.py``) rather than reimplementing it.
"""

from __future__ import annotations

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


log = get_logger(__name__)


def _make_task(name: str) -> Callable:
    """An Experiment task that replays the subject: re-invoke its entry with the
    record's ``input_data`` and return the produced output.
    """
    spec = _REGISTRY[name]
    has_end = "end" in spec
    start_output_fn = spec.get("start_output")

    def task(input_data, config):
        try:
            ret = _invoke(spec, input_data)
        except _ExperimentStop as stop:  # emit shape: end unwound with the output
            return stop.output
        if has_end:
            return None  # the end marker never fired
        return _start_output(start_output_fn, ret)  # single-function unit

    return task


def _make_evaluator(comparator: Callable[[Any, Any], bool]) -> Callable:
    """Wrap a comparator as a boolean experiment evaluator. The function name becomes
    the eval-metric label in the UI.
    """

    def regression_match(input_data, output_data, expected_output):
        return bool(comparator(expected_output, output_data))

    return regression_match


def experiment_url(experiment) -> Optional[str]:
    return getattr(experiment, "url", None) or getattr(getattr(experiment, "_experiment", None), "url", None)


def run_as_experiment(
    name: str,
    cases: list,
    comparator: Callable[[Any, Any], bool],
    experiment_name: Optional[str] = None,
    project_name: Optional[str] = None,
    dataset_name: Optional[str] = None,
):
    """Create a Dataset from ``cases`` and run the subject as an LLM Obs experiment
    scored by ``comparator``. Returns the experiment. Requires ``LLMObs`` to be enabled.
    """
    from ddtrace.llmobs import LLMObs

    records = [{"input_data": c["input"], "expected_output": c["output"]} for c in cases]
    dataset = LLMObs.create_dataset(
        dataset_name or ("inline-experiment-%s" % name),
        project_name=project_name,
        description="Inline-experiment baseline for %r (captured locally)." % name,
        records=records,
    )
    experiment = LLMObs.experiment(
        experiment_name or ("inline-%s" % name),
        _make_task(name),
        dataset,
        evaluators=[_make_evaluator(comparator)],
        project_name=project_name,
        tags={"source": "ddtrace-experiment"},
    )
    # REPLAY so an emit-shape end marker unwinds; set once (the engine may run tasks
    # concurrently, and a single-function subject's raw entry is unaffected by the mode).
    _set_mode(Mode.REPLAY)
    try:
        experiment.run()
    finally:
        _set_mode(Mode.OFF)
    return experiment
