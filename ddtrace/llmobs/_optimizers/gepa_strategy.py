"""GEPA adapter for dd-trace-py prompt optimization.

Bridges the dd-trace-py task/evaluators/optimization_task interface with GEPA's
evolutionary optimization loop via the ``GEPAAdapter`` protocol.
"""

from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import TypedDict

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._evaluators import BaseEvaluator
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import EvaluatorType


log = get_logger(__name__)


class TrajectoryRecord(TypedDict):
    """A single task execution record for reflection."""

    input_data: dict
    expected_output: Any
    output: Any
    evaluations: dict
    score: float


class LLMObsGEPAAdapter:
    """Bridges dd-trace-py task/evaluators/optimization_task with GEPA.

    Implements GEPA's ``GEPAAdapter`` protocol:
    - ``evaluate``: runs the user's task and evaluators on a batch of records
    - ``make_reflective_dataset``: builds feedback entries from trajectories
    - ``propose_new_texts``: wraps the user's ``optimization_task`` to produce
      improved prompts using our system prompt template
    """

    def __init__(
        self,
        task: Callable,
        evaluators: Sequence[EvaluatorType],
        optimization_task: Callable[[str, str, ConfigType], str],
        config: ConfigType,
        labelization_function: Optional[Callable[[dict[str, Any]], str]] = None,
    ) -> None:
        self._task = task
        self._evaluators = evaluators
        self._optimization_task = optimization_task
        self._config = config
        self._labelization_function = labelization_function

    # ------------------------------------------------------------------
    # GEPAAdapter protocol
    # ------------------------------------------------------------------

    def evaluate(self, batch: list, candidate: dict, capture_traces: bool = False) -> Any:
        """Run the user's task and evaluators on *batch* with *candidate* prompt.

        :param batch: List of dataset record dicts (``input_data``, ``expected_output``, …).
        :param candidate: Dict with at least ``{"system_prompt": str}``.
        :param capture_traces: Whether to capture trajectory records for reflection.
        :returns: ``gepa.EvaluationBatch`` with outputs, scores, and optional trajectories.
        """
        from gepa import EvaluationBatch

        outputs: list = []
        scores: list[float] = []
        trajectories: list[TrajectoryRecord] | None = [] if capture_traces else None

        # Inject candidate prompt into config
        modified_config = dict(self._config)
        modified_config["prompt"] = candidate["system_prompt"]

        for record in batch:
            input_data = record.get("input_data", record)
            expected_output = record.get("expected_output")

            # Run the user's task
            try:
                output = self._task(input_data=input_data, config=modified_config)
            except Exception:
                log.debug("Task failed for record", exc_info=True)
                output = None

            outputs.append(output)

            # Run the first evaluator to get a score
            evaluations: dict = {}
            score = 0.0
            for evaluator in self._evaluators:
                try:
                    if isinstance(evaluator, BaseEvaluator):
                        from ddtrace.llmobs._experiment import EvaluatorContext

                        ctx = EvaluatorContext(
                            input_data=input_data,
                            output_data=output,
                            expected_output=expected_output,
                            metadata={},
                            span_id="",
                            trace_id="",
                        )
                        result = evaluator.evaluate(ctx)
                    else:
                        result = evaluator(
                            input_data=input_data,
                            output_data=output,
                            expected_output=expected_output,
                        )
                    eval_name = getattr(evaluator, "name", None) or getattr(evaluator, "__name__", "evaluator")
                    evaluations[eval_name] = result
                    # Use the first evaluator's result as the score
                    if score == 0.0:
                        score = self._to_numeric_score(result)
                except Exception:
                    log.debug("Evaluator failed for record", exc_info=True)

            scores.append(score)

            if trajectories is not None:
                trajectories.append(
                    TrajectoryRecord(
                        input_data=input_data,
                        expected_output=expected_output,
                        output=output,
                        evaluations=evaluations,
                        score=score,
                    )
                )

        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self, candidate: dict, eval_batch: Any, components_to_update: list
    ) -> Mapping[str, list]:
        """Convert trajectories into the standard reflective format for GEPA.

        :param candidate: Current candidate dict.
        :param eval_batch: ``EvaluationBatch`` from ``evaluate()``.
        :param components_to_update: List of component names to update.
        :returns: Mapping of component name to list of reflective entries.
        """
        items: list[dict] = []
        trajectories = eval_batch.trajectories or []
        for traj in trajectories:
            feedback = self._build_feedback(traj)
            items.append(
                {
                    "Inputs": traj["input_data"],
                    "Generated Outputs": traj["output"],
                    "Feedback": feedback,
                }
            )
        return {"system_prompt": items}

    def propose_new_texts(
        self, candidate: dict, reflective_dataset: Mapping, components_to_update: list
    ) -> dict[str, str]:
        """Generate an improved prompt using the user's optimization_task.

        Loads the system prompt template, builds a user prompt from the reflective
        dataset entries, and calls the user's ``optimization_task``.

        :param candidate: Current candidate dict with ``system_prompt``.
        :param reflective_dataset: Output from ``make_reflective_dataset()``.
        :param components_to_update: List of component names to update.
        :returns: Dict mapping component name to new text, e.g. ``{"system_prompt": "..."}``.
        """
        from ddtrace.llmobs._prompt_optimization import load_optimization_system_prompt

        current_prompt = candidate["system_prompt"]
        entries = reflective_dataset.get("system_prompt", [])

        # Step 1: Load system prompt (reuse existing template logic)
        system_prompt = load_optimization_system_prompt(self._config)

        # Step 2: Build user prompt from reflective dataset entries
        user_prompt = self._build_user_prompt_from_reflective(current_prompt, entries)

        # Step 3: Call the user's optimization_task
        try:
            new_prompt = self._optimization_task(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=self._config,
            )
        except Exception:
            log.error("propose_new_texts: optimization_task failed", exc_info=True)
            new_prompt = ""

        if not new_prompt:
            return {"system_prompt": current_prompt}  # keep current on failure
        return {"system_prompt": new_prompt}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_user_prompt_from_reflective(self, current_prompt: str, entries: list[dict]) -> str:
        """Build a user prompt from reflective dataset entries.

        Follows a structure similar to ``OptimizationIteration._build_user_prompt()``.

        :param current_prompt: The current prompt being optimized.
        :param entries: List of reflective entries from ``make_reflective_dataset()``.
        :returns: User prompt string.
        """
        parts = [f"Initial Prompt:\n{current_prompt}\n"]

        if entries:
            parts.append("## Examples from Current Evaluation\n")
            for idx, entry in enumerate(entries, 1):
                parts.append(f"### Example {idx}")
                parts.append(f"Input: {entry.get('Inputs', '')}")
                parts.append(f"Actual Output: {entry.get('Generated Outputs', '')}")
                parts.append(f"Feedback: {entry.get('Feedback', '')}")
                parts.append("")  # spacing

        return "\n".join(parts)

    def _build_feedback(self, traj: TrajectoryRecord) -> str:
        """Build a feedback string from a trajectory record.

        Includes expected output and evaluator results/reasoning to give the
        optimizer actionable information about failures.

        :param traj: A ``TrajectoryRecord`` dict.
        :returns: Feedback string.
        """
        parts = []
        expected = traj.get("expected_output")
        if expected is not None:
            parts.append(f"Expected Output: {expected}")

        parts.append(f"Score: {traj['score']:.2f}")

        evaluations = traj.get("evaluations", {})
        for eval_name, eval_data in evaluations.items():
            if isinstance(eval_data, dict):
                value = eval_data.get("value")
                reasoning = eval_data.get("reasoning")
                if value is not None:
                    parts.append(f"{eval_name}: {value}")
                if reasoning:
                    parts.append(f"{eval_name} reasoning: {reasoning}")
            else:
                parts.append(f"{eval_name}: {eval_data}")

        return "\n".join(parts) if parts else "No feedback available."

    @staticmethod
    def _to_numeric_score(result: Any) -> float:
        """Convert an evaluator result to a numeric score.

        Handles various return types from evaluators:
        - float/int: returned directly
        - bool: True -> 1.0, False -> 0.0
        - EvaluatorResult: recurse on .value
        - dict with "value" key: recurse on ["value"]
        - Known pass strings: 1.0
        - Other strings: 0.0

        :param result: Evaluator return value.
        :returns: Numeric score as float.
        """
        if isinstance(result, (float, int)) and not isinstance(result, bool):
            return float(result)

        if isinstance(result, bool):
            return 1.0 if result else 0.0

        # EvaluatorResult or similar with .value attribute
        if hasattr(result, "value"):
            return LLMObsGEPAAdapter._to_numeric_score(result.value)

        if isinstance(result, dict) and "value" in result:
            return LLMObsGEPAAdapter._to_numeric_score(result["value"])

        if isinstance(result, str):
            pass_strings = {"true_positive", "true_negative", "correct", "pass"}
            if result.lower() in pass_strings:
                return 1.0
            return 0.0

        log.warning("Unexpected evaluator result type %s, returning 0.0", type(result).__name__)
        return 0.0

    @staticmethod
    def _dataset_to_gepa_format(dataset: Dataset) -> list[dict]:
        """Convert a Dataset to the list-of-dicts format expected by GEPA.

        :param dataset: An LLMObs ``Dataset``.
        :returns: List of dicts with ``input_data``, ``expected_output``, ``metadata``.
        """
        records = []
        for record in dataset:
            records.append(
                {
                    "input_data": record.get("input_data", record.get("input", {})),
                    "expected_output": record.get("expected_output"),
                    "metadata": record.get("metadata", {}),
                }
            )
        return records
