"""Base classes for LLMObs evaluators."""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union


# JSONType matches the type used in _experiment.py for evaluator return values
JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]


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

    **Return Value Guidelines:**

    The return value determines how the evaluation is stored and displayed in the LLMObs backend:

    - **Numeric (int/float)**: Stored as a "score" metric, enables charting and aggregation
    - **Boolean**: Stored as a "boolean" metric for pass/fail tracking
    - **None**: Stored as a categorical metric with null value
    - **Other types (str/dict/list)**: Converted to string and stored as "categorical" metric

    For best backend integration, prefer returning numeric scores (0.0-1.0) or booleans.
    If you need to return additional metadata, consider using span attributes separately.

    Example::

        class SemanticSimilarity(BaseEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="semantic_similarity")
                self.threshold = threshold
                self.model = load_embedding_model()

            def evaluate(self, context: EvaluatorContext):
                score = self.model.compare(context.output_data, context.expected_output)
                # Return numeric score for proper backend metrics
                return score
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
    def evaluate(self, context: EvaluatorContext) -> JSONType:
        """Perform synchronous evaluation.

        This method must be implemented by all subclasses.

        :param context: The evaluation context containing input, output, and metadata
        :return: Evaluation results - can be a dict, primitive (str/int/float/bool), list, or None
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")

    async def evaluate_async(self, context: EvaluatorContext) -> JSONType:
        """Perform asynchronous evaluation.

        Default implementation falls back to synchronous evaluation.
        Override this method to provide native async support.

        :param context: The evaluation context containing input, output, and metadata
        :return: Evaluation results - can be a dict, primitive (str/int/float/bool), list, or None
        """
        return self.evaluate(context)

    def __call__(self, input_data: Dict[str, Any], output_data: Any, expected_output: Any) -> JSONType:
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
