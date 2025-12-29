"""Base classes for LLMObs evaluators."""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
import re
from typing import Any
from typing import Dict
from typing import Optional


def _validate_evaluator_name(name: str) -> None:
    """Validate that evaluator name contains only alphanumeric characters and underscores.

    :param name: The evaluator name to validate
    :raises ValueError: If the name contains invalid characters
    """
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError(
            f"Evaluator name '{name}' is invalid. Name must contain only alphanumeric characters and underscores."
        )


@dataclass(frozen=True)
class EvaluatorContext:
    """Context object containing all data needed for evaluation.

    This frozen dataclass wraps all metadata needed to run an evaluation,
    providing better state management and extensibility compared to individual parameters.

    :param input_data: The input data that was provided to the task
    :param output_data: The output data produced by the task
    :param expected_output: The expected output for comparison (optional)
    :param metadata: Additional metadata about the evaluation (optional)
    :param span_id: The span ID associated with the task execution (optional)
    :param trace_id: The trace ID associated with the task execution (optional)
    :param config: Configuration dictionary for the experiment (optional)
    """

    input_data: Dict[str, Any]
    output_data: Any
    expected_output: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    span_id: Optional[str] = None
    trace_id: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)


class BaseEvaluator(ABC):
    """Abstract base class for evaluators in the LLMObs SDK.

    This class provides a unified interface for both synchronous and asynchronous evaluators.
    By implementing __call__, class-based evaluators remain compatible with legacy code
    expecting a callable.

    Subclasses must implement the `evaluate` method for synchronous evaluation.
    Optionally, they can override `evaluate_async` for native async support.

    Example::

        class SemanticSimilarity(BaseEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="semantic_similarity")
                self.threshold = threshold
                self.model = load_embedding_model()

            def evaluate(self, context: EvaluatorContext):
                score = self.model.compare(context.output_data, context.expected_output)
                return {"score": score, "passed": score >= self.threshold}
    """

    def __init__(self, name: Optional[str] = None):
        """Initialize the evaluator.

        :param name: Optional custom name for the evaluator. If not provided,
                     the class name will be used.
                     Name must contain only alphanumeric characters and underscores.
        """
        if name is not None and not isinstance(name, str):
            raise TypeError("Evaluator name must be a string")
        if name is not None and not name.strip():
            raise ValueError("Evaluator name cannot be empty")
        if name is not None:
            _validate_evaluator_name(name.strip())
        self._custom_name = name.strip() if name is not None else None

    @property
    def name(self) -> str:
        """Return the name of the evaluator.

        Uses the custom name if provided, otherwise uses the class name directly.

        :return: The evaluator name (alphanumeric characters and underscores only)
        :raises ValueError: If the generated name contains invalid characters
        """
        evaluator_name = self._custom_name if self._custom_name else self.__class__.__name__
        # Validate the final name (defensive check for auto-generated names)
        _validate_evaluator_name(evaluator_name)
        return evaluator_name

    @abstractmethod
    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform synchronous evaluation.

        This method must be implemented by all subclasses.

        :param context: The evaluation context containing input, output, and metadata
        :return: A dictionary containing the evaluation results
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")

    async def evaluate_async(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform asynchronous evaluation.

        Default implementation falls back to synchronous evaluation.
        Override this method to provide native async support.

        :param context: The evaluation context containing input, output, and metadata
        :return: A dictionary containing the evaluation results
        """
        return self.evaluate(context)

    def __call__(self, input_data: Dict[str, Any], output_data: Any, expected_output: Any) -> Dict[str, Any]:
        """Legacy compatibility shim for functional interface.

        This allows class-based evaluators to be used in contexts expecting
        a callable function with the old signature.

        :param input_data: The input data
        :param output_data: The output data
        :param expected_output: The expected output
        :return: The evaluation results
        """
        ctx = EvaluatorContext(input_data=input_data, output_data=output_data, expected_output=expected_output)
        return self.evaluate(ctx)
