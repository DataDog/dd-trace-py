from abc import ABC
from abc import abstractmethod
import asyncio
from copy import copy
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
import inspect
import re
import sys
import time
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Iterator
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import TypedDict
from typing import Union
from typing import cast
from typing import overload


try:
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias  # Python < 3.10

import uuid


try:
    from deepeval.metrics import BaseConversationalMetric
    from deepeval.metrics import BaseMetric
except ImportError:
    BaseMetric = None
    BaseConversationalMetric = None

try:
    from pydantic_evals.evaluators import Evaluator as PydanticEvaluator
    from pydantic_evals.evaluators import ReportEvaluator as PydanticReportEvaluator
except ImportError:
    PydanticEvaluator = None
    PydanticReportEvaluator = None
from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import DD_SITE_STAGING
from ddtrace.llmobs._constants import DD_SITES_NEEDING_APP_SUBDOMAIN
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import convert_tags_dict_to_list
from ddtrace.llmobs._utils import get_asyncio
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs._utils import validate_tags_list
from ddtrace.version import __version__


if TYPE_CHECKING:
    import pandas as pd

    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentEvalMetricEvent
    from ddtrace.llmobs._writer import LLMObsExperimentsClient
    from ddtrace.llmobs.types import ExportedLLMObsSpan

logger = get_logger(__name__)

JSONType = Union[str, int, float, bool, None, Sequence["JSONType"], Mapping[str, "JSONType"]]
ConfigType = dict[str, JSONType]
ContextTransformFn = Callable[["EvaluatorContext"], dict[str, Any]]

TaskType = Callable[..., JSONType]
AsyncTaskType = Callable[..., Awaitable[JSONType]]


class EvaluatorResult:
    """Container for evaluator results with additional metadata.

    This class allows evaluators to return not just a value, but also
    reasoning, assessment, metadata, and tags alongside the evaluation result.

    Example::

        def my_evaluator(input_data, output_data, expected_output):
            score = calculate_score(output_data, expected_output)
            return EvaluatorResult(
                value=score,
                reasoning="The output matches the expected format",
                assessment="pass" if score > 0.8 else "fail",
                metadata={"confidence": 0.95},
                tags={"category": "accuracy"}
            )
    """

    def __init__(
        self,
        value: JSONType,
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        metadata: Optional[dict[str, JSONType]] = None,
        tags: Optional[dict[str, JSONType]] = None,
    ) -> None:
        """Initialize an EvaluatorResult.

        :param value: The primary evaluation result (numeric, boolean, string, etc.)
        :param reasoning: Optional explanation of why this evaluation result was produced
        :param assessment: Optional categorical assessment (e.g., "pass", "fail", "good", "bad")
        :param metadata: Optional dictionary of additional metadata about the evaluation
        :param tags: Optional dictionary of tags to categorize or label the evaluation
        """
        self.value = value
        self.reasoning = reasoning
        self.assessment = assessment
        self.metadata = metadata
        self.tags = tags


class RemoteEvaluatorResult(EvaluatorResult):
    """Internal result type for remote evaluators that includes backend status."""

    def __init__(
        self,
        value: JSONType,
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        super().__init__(value=value, reasoning=reasoning, assessment=assessment)
        self.status = status


def _validate_evaluator_name(name: str) -> None:
    """Validate that evaluator name is valid.

    :param name: The evaluator name to validate
    :raises TypeError: If the name is not a string
    :raises ValueError: If the name is empty or contains invalid characters
    """
    if not isinstance(name, str):
        raise TypeError("Evaluator name must be a string")
    if not name:
        raise ValueError("Evaluator name cannot be empty")
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(
            f"Evaluator name '{name}' is invalid."
            " Name must contain only alphanumeric characters, underscores, and hyphens."
        )


class RemoteEvaluatorError(Exception):
    """Error raised when a remote evaluator fails.

    Preserves backend error details (type, message, recommended_resolution)
    for proper error handling in the experiment framework.
    """

    def __init__(self, message: str, status: str, backend_error: Optional[dict[str, Any]] = None) -> None:
        """Initialize a RemoteEvaluatorError.

        :param message: The error message
        :param status: The backend evaluation status (e.g. "ERROR", "WARN")
        :param backend_error: The backend error details dict with type, message, recommended_resolution
        """
        super().__init__(message)
        self.status = status
        self.backend_error = backend_error or {}


@dataclass(frozen=True)
class EvaluatorContext:
    """Context object containing all data needed for evaluation.

    This frozen dataclass wraps all metadata needed to run an evaluation,
    providing better state management and extensibility compared to individual parameters.

    :param input_data: The input data that was provided to the task (read-only).
                       Any JSON-serializable type.
    :param output_data: The output data produced by the task (read-only).
                        Any JSON-serializable type.
    :param expected_output: The expected output for comparison, if available (read-only).
                            Optional JSON-serializable type.
    :param metadata: Additional metadata including dataset record metadata and experiment configuration (read-only).
                     Dictionary with string keys mapping to JSON-serializable values.
    :param span_id: The span ID associated with the task execution, if available (read-only).
                    Optional string.
    :param trace_id: The trace ID associated with the task execution, if available (read-only).
                     Optional string.
    """

    input_data: JSONType
    output_data: Any
    expected_output: Optional[JSONType] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    span_id: Optional[str] = None
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class SummaryEvaluatorContext:
    """Context object containing all data needed for summary evaluation.

    :param inputs: list of all input data from the dataset records (read-only).
    :param outputs: list of all outputs produced by the task (read-only).
    :param expected_outputs: list of all expected outputs (read-only).
    :param evaluation_results: Dictionary mapping evaluator names to their results (read-only).
    :param metadata: list of metadata for each dataset record, each combined with experiment configuration (read-only).
                     Each element contains the record's metadata merged with {"experiment_config": ...}.
    """

    inputs: list[JSONType]
    outputs: list[JSONType]
    expected_outputs: list[JSONType]
    evaluation_results: dict[str, list[JSONType]]
    metadata: list[dict[str, Any]] = field(default_factory=list)


class BaseEvaluator(ABC):
    """This class provides a unified interface for evaluators.

    Subclasses must implement the `evaluate` method.

    **Evaluator Return Values**
    LLM Observability supports storing and representing the following evaluator return value types:
    - **Numeric**: int/float values
    - **Boolean**: pass/fail boolean values
    - **Null**: None values
    - **JSON serializable**: string/dict/list values, which will be serialized into strings
    - **EvaluatorResult**: Any of the above values plus optional associated reasoning, assessment, metadata, and tags

    Example (simple return)::

        class SemanticSimilarityEvaluator(BaseEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="semantic_similarity")
                self.threshold = threshold
                self.model = load_embedding_model()

            def evaluate(self, context: EvaluatorContext):
                score = self.model.compare(context.output_data, context.expected_output)
                return score

    Example (with EvaluatorResult)::

        class SemanticSimilarityEvaluator(BaseEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="semantic_similarity")
                self.threshold = threshold
                self.model = load_embedding_model()

            def evaluate(self, context: EvaluatorContext):
                score = self.model.compare(context.output_data, context.expected_output)
                return EvaluatorResult(
                    value=score,
                    reasoning=f"Similarity score: {score:.2f}",
                    assessment="pass" if score >= self.threshold else "fail",
                    metadata={"threshold": self.threshold},
                    tags={"type": "semantic"}
                )

    Note: The ``evaluate`` method may be called concurrently from multiple threads.
    Avoid modifying instance attributes inside ``evaluate()``; use local variables instead.
    """

    def __init__(self, name: Optional[str] = None):
        """Initialize the evaluator.

        :param name: Optional custom name for the evaluator. If not provided,
                     the class name will be used.
                     Name must contain only alphanumeric characters and underscores.
        """
        if name is not None:
            name = name.strip()
        else:
            name = self.__class__.__name__

        _validate_evaluator_name(name)
        self.name = name

    @abstractmethod
    def evaluate(self, context: EvaluatorContext) -> Union[JSONType, EvaluatorResult]:
        """Perform evaluation.

        This method must be implemented by all subclasses.

        :param context: The evaluation context containing input, output, and metadata
        :return: Evaluation results - can be a JSONType value (dict, primitive, list, None)
                 or an EvaluatorResult object containing the value plus additional metadata
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")

    def _build_publish_payload(
        self,
        ml_app: str,
        eval_name: Optional[str] = None,
        variable_mapping: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("This evaluator does not support publishing.")


class BaseSummaryEvaluator(ABC):
    """Base class for summary evaluators that operate on aggregated experiment results.

    Summary evaluators receive all inputs, outputs, expected outputs, and per-row
    evaluation results at once, allowing them to compute aggregate metrics.

    Subclasses must implement the `evaluate` method.

    Example::

        class AverageScoreEvaluator(BaseSummaryEvaluator):
            def __init__(self, target_evaluator: str):
                super().__init__(name="average_score")
                self.target_evaluator = target_evaluator

            def evaluate(self, context: SummaryEvaluatorContext):
                scores = context.evaluation_results.get(self.target_evaluator, [])
                if not scores:
                    return None
                return sum(scores) / len(scores)
    """

    def __init__(self, name: Optional[str] = None):
        """Initialize the summary evaluator.

        :param name: Optional custom name for the evaluator. If not provided,
                     the class name will be used.
                     Name must contain only alphanumeric characters and underscores.
        """
        if name is not None:
            name = name.strip()
        else:
            name = self.__class__.__name__

        _validate_evaluator_name(name)
        self.name = name

    @abstractmethod
    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        """Perform summary evaluation on aggregated experiment results.

        This method must be implemented by all subclasses.

        :param context: The summary evaluation context containing all inputs, outputs,
                        expected outputs, and per-row evaluation results
        :return: Evaluation result as a JSON-serializable value (dict, primitive, list, None)
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")


def _default_context_transform(context: "EvaluatorContext") -> dict[str, Any]:
    """Default transform: maps EvaluatorContext to remote evaluator format.

    Transforms the context into the format expected by remote LLM-as-Judge evaluators.
    Uses span_input/span_output at top level, with meta.expected_output and meta.metadata.

    :param context: The evaluation context to transform
    :return: Dictionary with span_input, span_output, optional meta.expected_output,
             optional meta.metadata, and optional trace identifiers (span_id, trace_id)
    """
    result: dict[str, Any] = {
        "span_input": context.input_data,
        "span_output": context.output_data,
    }

    meta: dict[str, Any] = {}
    if context.expected_output is not None:
        meta["expected_output"] = context.expected_output
    if context.metadata:
        meta["metadata"] = context.metadata
    if meta:
        result["meta"] = meta

    if context.span_id:
        result["span_id"] = context.span_id
    if context.trace_id:
        result["trace_id"] = context.trace_id

    return result


class RemoteEvaluator(BaseEvaluator):
    """Evaluator that references a production BYOP evaluation by name.

    This class allows users to run LLM-as-Judge evaluations configured in the Datadog
    UI by referencing the evaluation name.

    Example (simple usage)::

        evaluator = RemoteEvaluator(eval_name="my-byop-evaluator")

    Example (with custom transform function)::

        def custom_transform(ctx: EvaluatorContext) -> dict[str, Any]:
            return {"span_input": ctx.input_data, "span_output": ctx.output_data}

        evaluator = RemoteEvaluator(
            eval_name="my-byop-evaluator",
            transform_fn=custom_transform,
        )
    """

    _is_remote_evaluator: bool = True

    def __init__(
        self,
        eval_name: str,
        transform_fn: Optional[ContextTransformFn] = None,
    ) -> None:
        """Initialize a RemoteEvaluator.

        :param eval_name: The name of the LLM-as-Judge evaluator configured in Datadog.
        :param transform_fn: Optional function to transform EvaluatorContext into
                             the format expected by the backend template. If not provided,
                             uses default mapping to span_input, span_output,
                             meta.expected_output, and meta.metadata.
        """
        if not isinstance(eval_name, str) or not eval_name.strip():
            raise ValueError("eval_name must be a non-empty string")
        if transform_fn is not None and not callable(transform_fn):
            raise TypeError("transform_fn must be callable")

        self.name = eval_name.strip()
        self._eval_name = eval_name.strip()
        self._transform_fn = transform_fn if transform_fn is not None else _default_context_transform

        from ddtrace.llmobs import LLMObs

        self._llmobs_service = LLMObs

    def evaluate(self, context: EvaluatorContext) -> Union[JSONType, EvaluatorResult]:
        """Evaluate using the remote LLM-as-Judge evaluator.

        :param context: The evaluation context containing input, output, and metadata
        :return: RemoteEvaluatorResult or raw value if no reasoning or assessment is provided
        """
        dne_client = getattr(getattr(self._llmobs_service, "_instance", None), "_dne_client", None)
        if dne_client is None:
            raise ValueError("LLMObs experiments client is not initialized.")

        result = dne_client.evaluator_infer(
            eval_name=self._eval_name,
            context=self._transform_fn(context),
        )

        value = result.get("value")
        reasoning = result.get("reasoning")
        assessment = result.get("assessment")
        status = result.get("status")

        if reasoning or assessment:
            return RemoteEvaluatorResult(value=value, reasoning=reasoning, assessment=assessment, status=status)
        return value


class BaseAsyncEvaluator(ABC):
    """Base class for async row-level evaluators."""

    def __init__(self, name: Optional[str] = None):
        """Initialize the async evaluator.

        :param name: Optional custom name for the evaluator. If not provided,
                     the class name will be used.
                     Name must contain only alphanumeric characters and underscores.
        """
        if name is not None:
            name = name.strip()
        else:
            name = self.__class__.__name__

        _validate_evaluator_name(name)
        self.name = name

    @abstractmethod
    async def evaluate(self, context: EvaluatorContext) -> Union[JSONType, EvaluatorResult]:
        """Perform async evaluation."""
        raise NotImplementedError("Subclasses must implement the evaluate method")


class BaseAsyncSummaryEvaluator(ABC):
    """Base class for async summary evaluators that operate on aggregated experiment results."""

    def __init__(self, name: Optional[str] = None):
        """Initialize the async summary evaluator.

        :param name: Optional custom name for the evaluator. If not provided,
                     the class name will be used.
                     Name must contain only alphanumeric characters and underscores.
        """
        if name is not None:
            name = name.strip()
        else:
            name = self.__class__.__name__

        _validate_evaluator_name(name)
        self.name = name

    @abstractmethod
    async def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        """Perform async summary evaluation on aggregated experiment results."""
        raise NotImplementedError("Subclasses must implement the evaluate method")


# Evaluator types (defined after base classes)
if BaseMetric is not None and BaseConversationalMetric is not None:
    _DeepEvalListType: TypeAlias = Union[list[BaseMetric], list[BaseConversationalMetric]]
    EvaluatorType: TypeAlias = Union[
        Callable[
            [JSONType, JSONType, JSONType],
            Union[JSONType, "EvaluatorResult"],
        ],
        BaseEvaluator,
        _DeepEvalListType,
    ]
    AsyncEvaluatorType: TypeAlias = Union[
        Callable[
            [JSONType, JSONType, JSONType],
            Awaitable[Union[JSONType, "EvaluatorResult"]],
        ],
        BaseAsyncEvaluator,
        _DeepEvalListType,
    ]
else:
    EvaluatorType: TypeAlias = Union[  # type: ignore[no-redef,misc]
        Callable[
            [JSONType, JSONType, JSONType],
            Union[JSONType, "EvaluatorResult"],
        ],
        BaseEvaluator,
    ]
    AsyncEvaluatorType: TypeAlias = Union[  # type: ignore[no-redef,misc]
        Callable[
            [JSONType, JSONType, JSONType],
            Awaitable[Union[JSONType, "EvaluatorResult"]],
        ],
        BaseAsyncEvaluator,
    ]
if PydanticEvaluator is not None:
    EvaluatorType = Union[EvaluatorType, PydanticEvaluator]  # type: ignore[misc]
    AsyncEvaluatorType = Union[AsyncEvaluatorType, PydanticEvaluator]  # type: ignore[misc]

# Summary evaluator types
if PydanticReportEvaluator is not None:
    SummaryEvaluatorType = Union[
        Callable[
            [
                Sequence[JSONType],
                Sequence[JSONType],
                Sequence[JSONType],
                dict[str, Sequence[JSONType]],
            ],
            JSONType,
        ],
        BaseSummaryEvaluator,
        PydanticReportEvaluator,
    ]
    AsyncSummaryEvaluatorType = Union[
        Callable[
            [
                Sequence[JSONType],
                Sequence[JSONType],
                Sequence[JSONType],
                dict[str, Sequence[JSONType]],
            ],
            Awaitable[JSONType],
        ],
        BaseAsyncSummaryEvaluator,
        PydanticReportEvaluator,
    ]
else:
    SummaryEvaluatorType = Union[  # type: ignore[misc]
        Callable[
            [
                Sequence[JSONType],
                Sequence[JSONType],
                Sequence[JSONType],
                dict[str, Sequence[JSONType]],
            ],
            JSONType,
        ],
        BaseSummaryEvaluator,
    ]
    AsyncSummaryEvaluatorType = Union[  # type: ignore[misc]
        Callable[
            [
                Sequence[JSONType],
                Sequence[JSONType],
                Sequence[JSONType],
                dict[str, Sequence[JSONType]],
            ],
            Awaitable[JSONType],
        ],
        BaseAsyncSummaryEvaluator,
    ]


def _is_class_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a class-based evaluator (inherits from BaseEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's a class-based evaluator, False otherwise
    """
    return isinstance(evaluator, BaseEvaluator)


def _is_deep_eval_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a deep eval evaluator (inherits from BaseMetric or BaseConversationalMetric).

    :param evaluator: The evaluator to check
    :return: True if it's a class-based deepeval evaluator, False otherwise
    """
    if BaseMetric is None or BaseConversationalMetric is None:
        return False
    return isinstance(evaluator, BaseMetric) or isinstance(evaluator, BaseConversationalMetric)


def _is_pydantic_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a pydantic evaluator (inherits from PydanticEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's a pydantic evaluator, False otherwise
    """
    if PydanticEvaluator is None:
        return False
    return isinstance(evaluator, PydanticEvaluator)


def _is_pydantic_report_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a pydantic report evaluator (inherits from PydanticReportEvaluator).
    :param evaluator: The evaluator to check
    :return: True if it's a pydantic report evaluator with a scalar result, False otherwise
    """
    if PydanticReportEvaluator is None:
        return False
    return isinstance(evaluator, PydanticReportEvaluator)


def _is_class_summary_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a class-based summary evaluator (inherits from BaseSummaryEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's a class-based summary evaluator, False otherwise
    """
    return isinstance(evaluator, BaseSummaryEvaluator)


def _is_function_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a function-based evaluator.

    :param evaluator: The evaluator to check
    :return: True if it's a function evaluator, False otherwise
    """
    return (
        not isinstance(evaluator, BaseEvaluator)
        and not isinstance(evaluator, BaseSummaryEvaluator)
        and not _is_deep_eval_evaluator(evaluator)
        and not _is_pydantic_evaluator(evaluator)
    )


if BaseMetric is not None and BaseConversationalMetric is not None:

    def _deep_eval_evaluator_wrapper(evaluator: Any) -> Any:
        """Wrapper to run deep eval evaluators and convert their result to an EvaluatorResult.

        :param evaluator: The deep eval evaluator to run
        :return: A callable function that can be used as an evaluator
        """
        from deepeval.test_case import LLMTestCase

        def wrapped_evaluator(
            input_data: dict[str, Any],
            output_data: Any,
            expected_output: Optional[JSONType] = None,
        ) -> EvaluatorResult:
            """Wrapper to run deep eval evaluators and convert their result to an EvaluatorResult.

            :param input_data: The input data
            :param output_data: The output data
            :param expected_output: The expected output
            :return: An EvaluatorResult containing the score, reasoning, and assessment
            """
            deepEvalTestCase = LLMTestCase(
                input=str(input_data),
                actual_output=str(output_data),
                expected_output=str(expected_output),
            )
            evaluator.measure(deepEvalTestCase)
            score = evaluator.score
            reasoning = evaluator.reason
            assessment = "pass" if evaluator.success else "fail"
            metadata = evaluator.score_breakdown
            eval_result = EvaluatorResult(
                value=score,
                reasoning=reasoning,
                assessment=assessment,
                metadata=metadata,
            )
            return eval_result

        wrapped_evaluator.__name__ = getattr(evaluator, "name", "deep_eval_evaluator")
        return wrapped_evaluator

    def _deep_eval_async_evaluator_wrapper(evaluator: Any) -> Any:
        """Sync factory that returns an async callable for use with await in async experiments."""
        from deepeval.test_case import LLMTestCase

        async def wrapped_evaluator(
            input_data: dict[str, Any],
            output_data: Any,
            expected_output: Optional[JSONType] = None,
        ) -> EvaluatorResult:
            deepEvalTestCase = LLMTestCase(
                input=str(input_data),
                actual_output=str(output_data),
                expected_output=str(expected_output),
            )
            await evaluator.a_measure(deepEvalTestCase)
            score = evaluator.score
            reasoning = evaluator.reason
            assessment = "pass" if evaluator.success else "fail"
            metadata = evaluator.score_breakdown
            eval_result = EvaluatorResult(
                value=score,
                reasoning=reasoning,
                assessment=assessment,
                metadata=metadata,
            )
            return eval_result

        wrapped_evaluator.__name__ = getattr(evaluator, "name", "deep_eval_evaluator")
        return wrapped_evaluator

else:

    def _deep_eval_evaluator_wrapper(evaluator: Any) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking.

        :param evaluator: The deep eval evaluator to run
        :return: A callable function that can be used as an evaluator
        """
        return evaluator

    def _deep_eval_async_evaluator_wrapper(evaluator: Any) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking."""
        return evaluator


if PydanticEvaluator is not None:
    from collections.abc import Mapping
    import json

    from pydantic_evals.evaluators import EvaluatorContext as PydanticEvaluatorContext
    from pydantic_evals.evaluators import EvaluatorOutput as PydanticEvaluatorOutput
    from pydantic_evals.evaluators import ReportEvaluatorContext as PydanticReportEvaluatorContext
    from pydantic_evals.evaluators.evaluator import EvaluationReason as PydanticEvaluationReason
    from pydantic_evals.evaluators.evaluator import EvaluationScalar as PydanticEvaluationScalar
    from pydantic_evals.reporting import EvaluationReport as PydanticEvaluationReport
    from pydantic_evals.reporting import ReportCase as PydanticReportCase
    from pydantic_evals.reporting import ScalarResult as PydanticScalarResult

    def get_mapping_result(_eval_result: Mapping) -> EvaluatorResult:
        eval_result_list = list(_eval_result.values())
        eval_result = EvaluatorResult(
            value=None,
            reasoning=None,
            assessment=None,
        )
        if len(eval_result_list) == 1:
            first_item = eval_result_list[0]
            if hasattr(first_item, "value"):
                first_item_value = first_item.value
            else:
                first_item_value = first_item
            if hasattr(first_item, "reason"):
                reasoning = first_item.reason
            else:
                reasoning = None
            if isinstance(first_item_value, bool):
                assessment = "pass" if first_item_value else "fail"
            else:
                assessment = None
            eval_result.value = first_item_value
            eval_result.reasoning = reasoning
            eval_result.assessment = assessment
        elif len(eval_result_list) == 2:
            first_item = eval_result_list[0]
            second_item = eval_result_list[1]
            if hasattr(first_item, "value"):
                first_item_value = first_item.value
            else:
                first_item_value = first_item
            if hasattr(second_item, "value"):
                second_item_value = second_item.value
            else:
                second_item_value = second_item
            if isinstance(first_item_value, bool):
                assessment = "pass" if first_item_value else "fail"
                value = second_item_value
            elif isinstance(second_item_value, bool):
                assessment = "pass" if second_item_value else "fail"
                value = first_item_value
            else:
                assessment = None
                value = None
            if (
                hasattr(first_item, "reason")
                and hasattr(second_item, "reason")
                and first_item.reason == second_item.reason
            ):
                eval_result.value = value
                eval_result.assessment = assessment
                eval_result.reasoning = second_item.reason
            elif first_item_value == second_item_value:
                eval_result.value = value
                eval_result.assessment = assessment
            else:
                eval_result.value = json.dumps(_eval_result, default=lambda o: o.__dict__, indent=4)
        else:
            eval_result.value = json.dumps(_eval_result, default=lambda o: o.__dict__, indent=4)
        eval_result.metadata = {"raw_response": json.dumps(_eval_result, default=lambda o: o.__dict__, indent=4)}
        return eval_result

    def get_pydantic_evaluator_result(
        result: PydanticEvaluatorOutput,
    ) -> EvaluatorResult:
        _eval_result = cast(PydanticEvaluatorOutput, result)
        eval_result = EvaluatorResult(
            value=None,
            reasoning=None,
            assessment=None,
        )
        if isinstance(_eval_result, PydanticEvaluationScalar):
            eval_result.value = _eval_result
            if isinstance(_eval_result, bool):
                eval_result.assessment = "pass" if _eval_result else "fail"
        elif isinstance(_eval_result, PydanticEvaluationReason):
            eval_result.value = _eval_result.value
            eval_result.reasoning = _eval_result.reason
            if hasattr(_eval_result, "assessment") and isinstance(_eval_result.assessment, bool):
                eval_result.assessment = "pass" if _eval_result.value else "fail"
        elif isinstance(_eval_result, Mapping):
            eval_result = get_mapping_result(_eval_result)
        else:
            eval_result.value = json.dumps(_eval_result, default=lambda o: o.__dict__, indent=4)
            eval_result.metadata = {"raw_response": json.dumps(_eval_result, default=lambda o: o.__dict__, indent=4)}
        return eval_result

    def _pydantic_evaluator_wrapper(evaluator: Any, duration: Optional[float] = None, idx: int = 1) -> Any:
        """Wrapper to run pydantic evaluators and convert their result to an EvaluatorResult.

        :param evaluator: The pydantic evaluator to run
        :return: A callable function that can be used as an evaluator
        """

        def wrapped_evaluator(
            input_data: dict[str, Any],
            output_data: Any,
            expected_output: Optional[JSONType] = None,
            duration: Optional[float] = None,
        ) -> EvaluatorResult:
            asyncio = get_asyncio()
            evalContext = PydanticEvaluatorContext(
                name="",
                inputs=input_data,
                expected_output=expected_output,
                output=output_data,
                duration=duration,
                metadata=None,
                _span_tree=None,
                attributes=None,
                metrics=None,
            )

            if asyncio.iscoroutinefunction(evaluator.evaluate):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(evaluator.evaluate(evalContext))
                else:
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        _result = pool.submit(asyncio.run, evaluator.evaluate(evalContext))
                        result = _result.result()
            else:
                result = evaluator.evaluate(evalContext)
            eval_result = get_pydantic_evaluator_result(result)
            return eval_result

        eval_name = evaluator.get_default_evaluation_name()
        if idx > 1:
            wrapped_evaluator.__name__ = f"{eval_name}_{idx}"
        else:
            wrapped_evaluator.__name__ = eval_name
        return wrapped_evaluator

    def _pydantic_report_evaluator_wrapper(evaluator: Any) -> Any:
        """Wrapper to run pydantic report evaluators and convert their result to an EvaluatorResult.
        :param evaluator: The pydantic report evaluator to run
        :return: A callable function that can be used as an evaluator
        """

        # Note: duration and total duration are not available as of 4-1-2026 and are set to 0
        def wrapped_evaluator(
            eval_context: SummaryEvaluatorContext,
        ) -> JSONType:
            cases = []
            eval_results = eval_context.evaluation_results
            eval_names = eval_results.keys()
            for idx, input_data in enumerate(eval_context.inputs):
                assertions = dict()
                scores = dict()
                labels = dict()
                for eval_name in eval_names:
                    if isinstance(eval_results[eval_name][idx], bool):
                        assertions[eval_name] = eval_results[eval_name][idx]
                    elif isinstance(eval_results[eval_name][idx], float) or isinstance(
                        eval_results[eval_name][idx], int
                    ):
                        scores[eval_name] = eval_results[eval_name][idx]
                    else:
                        labels[eval_name] = eval_results[eval_name][idx]
                cases.append(
                    PydanticReportCase(
                        name=f"case_{idx}",
                        inputs=input_data,
                        metadata=eval_context.metadata[idx],
                        expected_output=eval_context.expected_outputs[idx],
                        output=eval_context.outputs[idx],
                        assertions=assertions,
                        scores=scores,
                        labels=labels,
                        task_duration=0,
                        total_duration=0,
                        attributes={},
                        metrics={},
                    )
                )
            report_eval_context = PydanticReportEvaluatorContext(
                name="",
                report=PydanticEvaluationReport(
                    name=evaluator.get_serialization_name(),
                    cases=cases,
                    experiment_metadata=eval_context.metadata,
                ),
                experiment_metadata=eval_context.metadata,
            )
            result = evaluator.evaluate(report_eval_context)
            if not isinstance(result, PydanticScalarResult):
                raise TypeError(
                    "Pydantic report evaluator returned a non-scalar result; only a scalar result is allowed"
                )
            return result.value

        wrapped_evaluator.__name__ = evaluator.get_serialization_name()
        return wrapped_evaluator

    def _pydantic_async_evaluator_wrapper(evaluator: Any, duration: Optional[float] = None, idx: int = 1) -> Any:
        """Wrapper to run pydantic evaluators and convert their result to an EvaluatorResult.

        :param evaluator: The pydantic evaluator to run
        :return: A callable function that can be used as an evaluator
        """

        async def wrapped_evaluator(
            input_data: dict[str, Any],
            output_data: Any,
            expected_output: Optional[JSONType] = None,
        ) -> EvaluatorResult:
            evalContext = PydanticEvaluatorContext(
                name="",
                inputs=input_data,
                expected_output=expected_output,
                output=output_data,
                duration=duration,
                metadata=None,
                _span_tree=None,
                attributes=None,
                metrics=None,
            )

            result = await evaluator.evaluate_async(evalContext)
            eval_result = get_pydantic_evaluator_result(result)
            return eval_result

        eval_name = evaluator.get_default_evaluation_name()
        if idx > 1:
            wrapped_evaluator.__name__ = f"{eval_name}_{idx}"
        else:
            wrapped_evaluator.__name__ = eval_name
        return wrapped_evaluator

    def _pydantic_async_report_evaluator_wrapper(evaluator: Any) -> Any:
        """Wrapper to run pydantic report evaluators and convert their result to an EvaluatorResult.
        :param evaluator: The pydantic report evaluator to run
        :return: A callable function that can be used as an evaluator
        """

        # Note: duration and total duration are not available as of 4-1-2026 and are set to 0
        async def wrapped_evaluator(
            eval_context: SummaryEvaluatorContext,
        ) -> JSONType:
            cases = []
            eval_results = eval_context.evaluation_results
            eval_names = eval_results.keys()
            for idx, input_data in enumerate(eval_context.inputs):
                assertions = dict()
                scores = dict()
                labels = dict()
                for eval_name in eval_names:
                    if isinstance(eval_results[eval_name][idx], bool):
                        assertions[eval_name] = eval_results[eval_name][idx]
                    elif isinstance(eval_results[eval_name][idx], float) or isinstance(
                        eval_results[eval_name][idx], int
                    ):
                        scores[eval_name] = eval_results[eval_name][idx]
                    else:
                        labels[eval_name] = eval_results[eval_name][idx]
                cases.append(
                    PydanticReportCase(
                        name=f"case_{idx}",
                        inputs=input_data,
                        metadata=eval_context.metadata[idx],
                        expected_output=eval_context.expected_outputs[idx],
                        output=eval_context.outputs[idx],
                        assertions=assertions,
                        scores=scores,
                        labels=labels,
                        task_duration=0,
                        total_duration=0,
                        attributes={},
                        metrics={},
                    )
                )
            report_eval_context = PydanticReportEvaluatorContext(
                name="",
                report=PydanticEvaluationReport(
                    name=evaluator.get_serialization_name(),
                    cases=cases,
                    experiment_metadata=eval_context.metadata,
                ),
                experiment_metadata=eval_context.metadata,
            )
            result = await evaluator.evaluate_async(report_eval_context)
            if not isinstance(result, PydanticScalarResult):
                raise TypeError(
                    "Pydantic report evaluator returned a non-scalar result; only a scalar result is allowed"
                )
            return result.value

        wrapped_evaluator.__name__ = evaluator.get_serialization_name()
        return wrapped_evaluator
else:

    def _pydantic_evaluator_wrapper(evaluator: Any, duration: Optional[float] = None, idx: int = 1) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking."""
        return evaluator

    def _pydantic_async_evaluator_wrapper(evaluator: Any, duration: Optional[float] = None, idx: int = 1) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking."""
        return evaluator

    def _pydantic_report_evaluator_wrapper(evaluator: Any) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking."""
        return evaluator

    def _pydantic_async_report_evaluator_wrapper(evaluator: Any) -> Any:
        """Dummy wrapper; should never be called but used to satisfy type checking."""
        return evaluator


class Project(TypedDict):
    name: str
    _id: str


class _DatasetRecordOptional(TypedDict, total=False):
    expected_output: JSONType
    metadata: dict[str, Any]
    tags: list[str]
    canonical_id: Optional[str]


class DatasetRecord(_DatasetRecordOptional):
    input_data: JSONType
    record_id: str  # ID for the record, either from user or system


class _DatasetRecordNewOptional(TypedDict, total=False):
    id: str  # optional user-defined ID for the new record
    expected_output: JSONType
    metadata: dict[str, Any]
    tags: list[str]


class DatasetRecordNew(_DatasetRecordNewOptional):
    input_data: JSONType


class _TagOperations(TypedDict, total=False):
    add: list[str]
    remove: list[str]
    replace: list[str]


class DatasetRecordUpdate(TypedDict, total=False):
    input_data: JSONType
    expected_output: JSONType
    metadata: dict[str, Any]
    tags: list[str]
    tag_operations: _TagOperations


class DatasetRecordUpdateWithId(DatasetRecordUpdate):
    record_id: str


class _TaskResultRequired(TypedDict):
    idx: int
    span_id: str
    trace_id: str
    timestamp: int
    output: JSONType
    metadata: dict[str, JSONType]
    error: dict[str, Optional[str]]


class TaskResult(_TaskResultRequired, total=False):
    duration: int
    span_name: str


class EvaluationResult(TypedDict):
    idx: int
    evaluations: dict[str, dict[str, JSONType]]


class _ExperimentRunInfo:
    def __init__(self, run_interation: int):
        self._id = uuid.uuid4()
        # always increment the representation of iteration by 1 for readability
        self._run_iteration = run_interation + 1


class _ExperimentRowResultRequired(TypedDict):
    idx: int
    record_id: Optional[str]
    span_id: str
    trace_id: str
    timestamp: int
    duration: int
    span_name: str
    input: JSONType
    output: JSONType
    expected_output: JSONType
    evaluations: dict[str, dict[str, JSONType]]
    metadata: dict[str, JSONType]
    error: dict[str, Optional[str]]


class ExperimentRowResult(_ExperimentRowResultRequired, total=False):
    pass


class ExperimentRun:
    def __init__(
        self,
        run: _ExperimentRunInfo,
        summary_evaluations: dict[str, dict[str, JSONType]],
        rows: list[ExperimentRowResult],
    ):
        self.run_id = run._id
        self.run_iteration = run._run_iteration
        self.summary_evaluations = summary_evaluations or {}
        self.rows = rows or []

    def as_dataframe(self) -> "pd.DataFrame":
        """Convert experiment run rows to a pandas DataFrame with MultiIndex columns.

        Each top-level group (``input``, ``output``, ``expected_output``,
        ``evaluations``, ``metadata``, ``error``, ``span_id``, ``trace_id``) becomes
        the first level of the column MultiIndex.  Dict-valued fields are flattened
        one level deep; scalar fields use an empty string as the sub-column name.

        Evaluation cells contain the full evaluation dict
        (``value``, ``type``, ``reasoning``, ``assessment``).

        :raises ImportError: if ``pandas`` is not installed.
        :return: ``pd.DataFrame`` with ``pd.MultiIndex`` columns.
        """
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError(
                "pandas is required to convert experiment results to a DataFrame. "
                "Please install it via `pip install pandas`."
            ) from e

        column_tuples: set = set()
        data_rows = []
        for row in self.rows:
            flat: dict = {}

            input_data = row.get("input", {})
            if isinstance(input_data, dict):
                for k, v in input_data.items():
                    flat[("input", k)] = v
                    column_tuples.add(("input", k))
            else:
                flat[("input", "")] = input_data
                column_tuples.add(("input", ""))

            output_data = row.get("output")
            if isinstance(output_data, dict):
                for k, v in output_data.items():
                    flat[("output", k)] = v
                    column_tuples.add(("output", k))
            else:
                flat[("output", "")] = output_data
                column_tuples.add(("output", ""))

            expected = row.get("expected_output", {})
            if isinstance(expected, dict):
                for k, v in expected.items():
                    flat[("expected_output", k)] = v
                    column_tuples.add(("expected_output", k))
            else:
                flat[("expected_output", "")] = expected
                column_tuples.add(("expected_output", ""))

            metadata = row.get("metadata", {})
            if isinstance(metadata, dict):
                for k, v in metadata.items():
                    flat[("metadata", k)] = v
                    column_tuples.add(("metadata", k))

            for eval_name, eval_data in (row.get("evaluations") or {}).items():
                flat[("evaluations", eval_name)] = eval_data
                column_tuples.add(("evaluations", eval_name))

            flat[("error", "")] = row.get("error")
            flat[("span_id", "")] = row.get("span_id")
            flat[("trace_id", "")] = row.get("trace_id")
            column_tuples.update([("error", ""), ("span_id", ""), ("trace_id", "")])

            data_rows.append(flat)

        records = [[flat.get(col) for col in column_tuples] for flat in data_rows]
        return pd.DataFrame(data=records, columns=pd.MultiIndex.from_tuples(column_tuples))


class ExperimentResult(TypedDict):
    # TODO: remove these fields (summary_evaluations, rows) in the next major release (5.x)
    summary_evaluations: dict[str, dict[str, JSONType]]
    rows: list[ExperimentRowResult]
    runs: list[ExperimentRun]


def _build_span_copies(
    prior_rows: "list[ExperimentRowResult]",
    new_experiment_id: str,
    base_tags: list[str],
) -> "tuple[list[dict], dict[str, str], str]":
    """Build semantically identical span dicts with fresh UUIDs and an offset timestamp.

    For each row with a span_id, a new span dict is produced with:
    - A fresh UUID for span_id (old → new mapping stored in id_map)
    - A single shared new trace_id for the entire batch
    - start_ns offset uniformly to the current wall clock while preserving relative ordering
    - duration preserved from the original span (stored on the row from run() or pull())
    - name preserved from the original span (stored on the row from run() or pull())
    - All meta content (input, output, expected_output, metadata) copied verbatim
    - Tags updated to reference new_experiment_id instead of the original

    Returns ``(span_dicts, id_map, new_trace_id)`` where ``id_map`` maps each original
    span_id to its new UUID so eval metrics can reference the correct new span IDs.
    """
    rows_with_span = [r for r in prior_rows if r.get("span_id")]
    if not rows_with_span:
        return [], {}, ""

    timestamps = [r["timestamp"] for r in rows_with_span if r.get("timestamp")]
    min_start_ns = min(timestamps) if timestamps else 0
    offset_ns = time.time_ns() - min_start_ns
    new_trace_id = str(uuid.uuid4())
    id_map: dict[str, str] = {r["span_id"]: str(uuid.uuid4()) for r in rows_with_span}

    spans = []
    for row in rows_with_span:
        # Replace experiment_id tag; preserve every other tag from base_tags.
        tags = [t for t in base_tags if not t.startswith("experiment_id:")]
        record_id = row.get("record_id")
        if record_id:
            tags.append(f"dataset_record_id:{record_id}")
        tags.append(f"experiment_id:{new_experiment_id}")
        tags.append(f"parent_experiment_span_id:{row['span_id']}")

        span: dict = {
            "span_id": id_map[row["span_id"]],
            "trace_id": new_trace_id,
            "name": row.get("span_name") or "experiment_record",
            "start_ns": (row["timestamp"] or 0) + offset_ns,
            "duration": row.get("duration") or 1,  # backend requires non-zero (Go `required` tag)
            "meta": {
                "input": row.get("input"),
                "output": row.get("output"),
                "expected_output": row.get("expected_output"),
                "metadata": row.get("metadata"),
                "parent_experiment_span_id": row["span_id"],
            },
            "status": row.get("status", "ok"),
            "tags": tags,
        }
        spans.append(span)

    return spans, id_map, new_trace_id


def _task_results_from_previous(
    rows: "list[ExperimentRowResult]",
    id_map: "dict[str, str]",
    new_trace_id: str,
) -> "list[TaskResult]":
    """Reconstruct TaskResult objects from a previous ExperimentRun's rows.

    Used by eval-only re-runs to reuse prior task outputs without re-executing the task.
    span_id and trace_id are replaced with new values from id_map / new_trace_id so that
    eval metrics emitted for the re-run reference the newly created span copies.
    """
    return [
        {
            "idx": row["idx"],
            "span_id": id_map.get(row["span_id"], row["span_id"]),
            "trace_id": new_trace_id or row["trace_id"],
            "timestamp": row["timestamp"],
            "duration": row["duration"],
            "span_name": row["span_name"],
            "output": row["output"],
            "metadata": row["metadata"],
            "error": row["error"],
        }
        for row in rows
    ]


def _parse_experiment_result(response: dict) -> "ExperimentResult":
    """Deserialise a backend ``/events`` response into an ``ExperimentResult``.

    Response shape (single JSON:API object):
    ``{"data": {"id": "<experiment_id>", "type": "experiment_events",
    "attributes": {"spans": [...], "summary_metrics": [...]}}}``

    Each item in ``attributes.spans`` is one experiment span with flat fields
    (``span_id``, ``trace_id``, ``name``, ``start_ns``, ``duration``, ``tags``,
    ``meta`` containing ``input``/``output``/``expected_output``/``metadata``/``error``,
    and ``eval_metrics``).  ``eval_metrics`` on each span (when present) are converted
    to the ``evaluations`` dict format used by ``ExperimentRowResult``.  When multiple
    eval events share the same label (e.g. original run + re-run), the one with the
    latest ``timestamp_ms`` wins.
    """
    data = response.get("data") or {}
    attributes = data.get("attributes") or {}
    spans = attributes.get("spans") or []

    rows: list[ExperimentRowResult] = []
    for i, span in enumerate(spans):
        meta = span.get("meta") or {}
        metadata: dict = meta.get("metadata") or {}

        # idx: use array position (no dataset_record_index in the new response shape)
        idx: int = i

        # record_id is stored as a "dataset_record_id:<uuid>" tag
        record_id: Optional[str] = None
        for tag in span.get("tags") or []:
            if isinstance(tag, str) and tag.startswith("dataset_record_id:"):
                record_id = tag.split(":", 1)[1]
                break

        # Convert eval_metrics list → evaluations dict keyed by label.
        # Multiple events per label are possible (original run + re-run); keep latest by timestamp_ms.
        evaluations: dict = {}
        latest_ts: dict[str, int] = {}
        for em in span.get("eval_metrics") or []:
            label = em.get("label", "")
            if not label:
                continue
            ts = em.get("timestamp_ms", 0) or 0
            if label in latest_ts and ts < latest_ts[label]:
                continue
            latest_ts[label] = ts
            metric_type = em.get("metric_type", "score")
            value = em.get("score_value") if metric_type == "score" else em.get("boolean_value")
            evaluations[label] = {
                "value": value,
                "type": metric_type,
                "reasoning": em.get("reasoning"),
                "assessment": em.get("assessment"),
            }

        # Normalise error: the API returns an empty dict {} when there is no error.
        raw_error = meta.get("error") or {}
        normalised_error: dict[str, Optional[str]] = {
            "type": raw_error.get("type") or None,
            "message": raw_error.get("message") or None,
            "stack": raw_error.get("stack") or None,
        }

        duration = span.get("duration") or 0
        if not duration:
            logger.warning(
                "Span %s from experiment pull response is missing a duration field; "
                "rerun_evaluators() will use a fallback of 1 ns.",
                span.get("span_id", "<unknown>"),
            )

        row: ExperimentRowResult = {
            "idx": idx,
            "record_id": record_id,
            "span_id": span.get("span_id", ""),
            "trace_id": span.get("trace_id", ""),
            "timestamp": span.get("start_ns", 0),
            "duration": duration,
            "span_name": span.get("name", "experiment_record"),
            "input": meta.get("input") or {},
            "output": meta.get("output"),
            "expected_output": meta.get("expected_output"),
            "evaluations": evaluations,
            "metadata": metadata,
            "error": normalised_error,
        }
        rows.append(row)

    rows.sort(key=lambda r: r["idx"])

    run_info = _ExperimentRunInfo(run_interation=0)
    experiment_run = ExperimentRun(run_info, summary_evaluations={}, rows=rows)
    return Experiment._build_result([experiment_run])


class Dataset:
    name: str
    description: str
    filter_tags: Optional[list[str]]
    _id: str
    _records: list[DatasetRecord]
    _records_by_id: dict[str, DatasetRecord]
    _version: int
    _latest_version: int
    _dne_client: "LLMObsExperimentsClient"
    _new_records_by_record_id: dict[str, DatasetRecord]
    _updated_record_ids_to_new_fields: dict[str, DatasetRecordUpdateWithId]
    _deleted_record_ids: list[str]

    BATCH_UPDATE_THRESHOLD = 5 * 1024 * 1024  # 5MB
    BATCH_UPDATE_MAX_RECORDS = 1000

    def __init__(
        self,
        name: str,
        project: Project,
        dataset_id: str,
        records: list[DatasetRecord],
        description: str,
        latest_version: int,
        version: int,
        _dne_client: "LLMObsExperimentsClient",
        filter_tags: Optional[list[str]] = None,
    ) -> None:
        self.name = name
        self.project = project
        self.description = description
        self.filter_tags = filter_tags or []
        self._id = dataset_id
        self._latest_version = latest_version
        self._version = version
        self._dne_client = _dne_client
        self._records = records
        self._records_by_id = {r["record_id"]: r for r in records}
        self._new_records_by_record_id = {}
        self._updated_record_ids_to_new_fields = {}
        self._deleted_record_ids = []
        self._pending_tag_operations: dict[str, _TagOperations] = {}

    def push(
        self,
        deduplicate: bool = True,
        create_new_version: bool = True,
        bulk_upload: Optional[bool] = None,
    ):
        """Pushes any local changes in this dataset since the last push.

        :param deduplicate:
            Whether to deduplicate the records or not. Does not deduplicate against existing
            data if bulk_upload is False.
        :param create_new_version:
            Whether to create a new version of the dataset when changes are detected, or update the
            existing version.
        :param bulk_upload:
            - True:
                Uploads all records in a single request. This method does not support deduplication
                against existing data and is best suited for initial uploads.
            - False:
                Splits the data into batches and uploads them individually. This method supports
                deduplication against existing records but does not provide transactional guarantees
                when the same dataset is modified concurrently by multiple clients.
            - None:
                The SDK chooses between the above two approaches using data size.
        """
        self._push(deduplicate, create_new_version, bulk_upload)

    def _push(
        self,
        deduplicate: bool = True,
        create_new_version: bool = True,
        bulk_upload: Optional[bool] = None,
    ) -> bool:
        if not self._id:
            raise ValueError(
                (
                    "Dataset ID is required to push data to Experiments. "
                    "Use LLMObs.create_dataset() or LLMObs.pull_dataset() to create a dataset."
                )
            )
        if not self._dne_client:
            raise ValueError(
                (
                    "LLMObs client is required to push data to Experiments. "
                    "Use LLMObs.create_dataset() or LLMObs.pull_dataset() to create a dataset."
                )
            )

        data_changed = False
        delta_size = self._estimate_delta_size()
        if bulk_upload or (bulk_upload is None and delta_size > self.BATCH_UPDATE_THRESHOLD):
            logger.debug("dataset delta is %d, using bulk upload", delta_size)
            # TODO must return version too
            self._dne_client.dataset_bulk_upload(self._id, self._records, deduplicate=deduplicate)
        else:
            logger.debug("dataset delta is %d, using batch update", delta_size)
            # Inject accumulated tag operations into update records before sending
            for record_id, tag_ops in self._pending_tag_operations.items():
                if record_id in self._updated_record_ids_to_new_fields:
                    self._updated_record_ids_to_new_fields[record_id]["tag_operations"] = tag_ops
            updated_records = list(self._updated_record_ids_to_new_fields.values())
            (
                new_version,
                new_record_ids,
                new_canonical_ids,
            ) = self._dne_client.dataset_batch_update(
                dataset_id=self._id,
                project_id=self.project["_id"],
                insert_records=list(self._new_records_by_record_id.values()),
                update_records=updated_records,
                delete_record_ids=self._deleted_record_ids,
                deduplicate=deduplicate,
                create_new_version=create_new_version,
            )

            for returned_id, canonical_id in zip(new_record_ids, new_canonical_ids):
                if canonical_id and returned_id in self._records_by_id:
                    self._records_by_id[returned_id]["canonical_id"] = canonical_id
                if returned_id in self._new_records_by_record_id:
                    del self._new_records_by_record_id[returned_id]

            data_changed = len(new_record_ids) > 0 or len(self._deleted_record_ids) > 0
            if new_version != -1:
                self._latest_version = new_version
            logger.debug("new_version %d latest_version %d", new_version, self._latest_version)
            # no matter what the version was before the push, pushing will result in the dataset being on the current
            # version tracked by the backend
            self._version = self._latest_version
        self._deleted_record_ids = []
        self._updated_record_ids_to_new_fields = {}
        self._pending_tag_operations = {}
        return data_changed

    def update(self, index: int, record: DatasetRecordUpdate) -> None:
        if all(k not in record for k in ("input_data", "expected_output", "metadata", "tags")):
            raise ValueError(
                "invalid update, record should contain at least one of "
                "input_data, expected_output, metadata, or tags to update"
            )
        # If tags are provided, delegate to replace_tags for that record
        tags = record.get("tags", None) if "tags" in record else None
        if tags is not None:
            self.replace_tags(index, tags)

        record_id = self._records[index]["record_id"]
        if any(k in record for k in ("input_data", "expected_output", "metadata")):
            self._updated_record_ids_to_new_fields[record_id] = {
                **self._updated_record_ids_to_new_fields.get(record_id, {"record_id": record_id}),
                **record,
                "record_id": record_id,
            }
            self._records[index] = cast(
                DatasetRecord,
                {
                    **self._records[index],
                    **record,
                    "record_id": record_id,
                },
            )

    def append(self, record: DatasetRecordNew) -> None:
        if record.get("tags"):
            validate_tags_list(record["tags"])
        record_id: str = record.get("id") or uuid.uuid4().hex
        if record_id in self._records_by_id:
            raise ValueError(f"Record id {record_id!r} used more than once. ")
        # convert to DatasetRecord with required record_id
        r = DatasetRecord(
            record_id=record_id,
            input_data=record.get("input_data"),
        )
        if "expected_output" in record:
            r["expected_output"] = record["expected_output"]
        if "metadata" in record:
            r["metadata"] = record["metadata"]
        if "tags" in record:
            r["tags"] = record["tags"]
        # keep the same reference in both lists to enable us to update the record_id after push
        self._new_records_by_record_id[record_id] = r
        self._records.append(r)
        self._records_by_id[record_id] = r

    def extend(self, records: Sequence[DatasetRecordNew]) -> None:
        for record in records:
            self.append(record)

    def add_tags(self, index: int, tags: list[str]) -> None:
        """Add tags to an existing record. Tags are merged with any existing tags on the record."""
        validate_tags_list(tags)
        record = self._records[index]
        record_id = record["record_id"]

        # For not-yet-pushed records, modify tags directly
        if record_id in self._new_records_by_record_id:
            existing = set(record.get("tags") or [])
            existing.update(tags)
            record["tags"] = sorted(existing)
            return

        self._accumulate_tag_operations(record_id, "add", tags)
        # Update in-memory state for read consistency
        existing = set(record.get("tags") or [])
        existing.update(tags)
        record["tags"] = sorted(existing)
        # Ensure the record is tracked for updates
        if record_id not in self._updated_record_ids_to_new_fields:
            self._updated_record_ids_to_new_fields[record_id] = {"record_id": record_id}

    def remove_tags(self, index: int, tags: list[str]) -> None:
        """Remove tags from an existing record."""
        validate_tags_list(tags)
        record = self._records[index]
        record_id = record["record_id"]

        # For not-yet-pushed records, modify tags directly
        if record_id in self._new_records_by_record_id:
            existing = set(record.get("tags") or [])
            existing -= set(tags)
            record["tags"] = sorted(existing)
            return

        self._accumulate_tag_operations(record_id, "remove", tags)
        # Update in-memory state for read consistency
        existing = set(record.get("tags") or [])
        existing -= set(tags)
        record["tags"] = sorted(existing)
        # Ensure the record is tracked for updates
        if record_id not in self._updated_record_ids_to_new_fields:
            self._updated_record_ids_to_new_fields[record_id] = {"record_id": record_id}

    def replace_tags(self, index: int, tags: list[str]) -> None:
        """Replace all tags on an existing record with the given tags."""
        validate_tags_list(tags)
        record = self._records[index]
        record_id = record["record_id"]

        # For not-yet-pushed records, modify tags directly
        if record_id in self._new_records_by_record_id:
            record["tags"] = list(tags)
            return

        self._accumulate_tag_operations(record_id, "replace", tags)
        # Update in-memory state for read consistency
        record["tags"] = list(tags)
        # Ensure the record is tracked for updates
        if record_id not in self._updated_record_ids_to_new_fields:
            self._updated_record_ids_to_new_fields[record_id] = {"record_id": record_id}

    def _accumulate_tag_operations(self, record_id: str, operation: str, tags: list[str]) -> None:
        """Accumulate tag operations for a record before push.

        Handles merging logic:
        - replace overrides all prior add/remove operations
        - After a replace, subsequent add/remove fold into the replace list
        - Without replace, add and remove accumulate independently with cancellation
        """
        ops = self._pending_tag_operations.get(record_id, _TagOperations())

        if operation == "replace":
            # Replace overrides everything
            ops = _TagOperations(replace=list(tags))
        elif operation == "add":
            if "replace" in ops:
                # After a replace, fold adds into the replace list
                existing = set(ops["replace"])
                existing.update(tags)
                ops["replace"] = sorted(existing)
            else:
                add_set = set(ops.get("add", []))
                remove_set = set(ops.get("remove", []))
                for tag in tags:
                    if tag in remove_set:
                        remove_set.discard(tag)
                    else:
                        add_set.add(tag)
                ops = _TagOperations()
                if add_set:
                    ops["add"] = sorted(add_set)
                if remove_set:
                    ops["remove"] = sorted(remove_set)
        elif operation == "remove":
            if "replace" in ops:
                # After a replace, fold removes into the replace list
                existing = set(ops["replace"])
                existing -= set(tags)
                ops["replace"] = sorted(existing)
            else:
                add_set = set(ops.get("add", []))
                remove_set = set(ops.get("remove", []))
                for tag in tags:
                    if tag in add_set:
                        add_set.discard(tag)
                    else:
                        remove_set.add(tag)
                ops = _TagOperations()
                if add_set:
                    ops["add"] = sorted(add_set)
                if remove_set:
                    ops["remove"] = sorted(remove_set)

        self._pending_tag_operations[record_id] = ops

    def delete(self, index: int) -> None:
        record_id = self._records[index]["record_id"]
        should_append_to_be_deleted = True

        del self._records[index]

        if record_id is None or record_id == "":
            logger.warning("encountered unexpected record_id on deletion %s", record_id)
            return

        if record_id in self._updated_record_ids_to_new_fields:
            del self._updated_record_ids_to_new_fields[record_id]

        if record_id in self._pending_tag_operations:
            del self._pending_tag_operations[record_id]

        if record_id in self._records_by_id:
            del self._records_by_id[record_id]

        if record_id in self._new_records_by_record_id:
            del self._new_records_by_record_id[record_id]
            should_append_to_be_deleted = False

        if should_append_to_be_deleted:
            self._deleted_record_ids.append(record_id)

    @property
    def url(self) -> str:
        # FIXME: will not work for subdomain orgs
        return f"{_get_base_url()}/llm/datasets/{self._id}"

    @property
    def latest_version(self) -> int:
        return self._latest_version

    @property
    def version(self) -> int:
        return self._version

    def _estimate_delta_size(self) -> int:
        """rough estimate (in bytes) of the size of the next batch update call if it happens"""
        size = (
            len(safe_json(self._new_records_by_record_id))
            + len(safe_json(self._updated_record_ids_to_new_fields))
            + len(safe_json(self._pending_tag_operations))
        )
        logger.debug("estimated delta size %d", size)
        return size

    @overload
    def __getitem__(self, index: int) -> DatasetRecord: ...

    @overload
    def __getitem__(self, index: slice) -> list[DatasetRecord]: ...

    def __getitem__(self, index: Union[int, slice]) -> Union[DatasetRecord, list[DatasetRecord]]:
        return self._records.__getitem__(index)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[DatasetRecord]:
        return iter(self._records)

    def as_dataframe(self) -> None:
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError(
                "pandas is required to convert dataset to DataFrame. Please install via `pip install pandas`"
            ) from e

        column_tuples = set()
        data_rows = []
        for record in self._records:
            flat_record = {}  # type: dict[Union[str, tuple[str, str]], Any]

            input_data = record.get("input_data", {})
            if isinstance(input_data, dict):
                for input_data_col, input_data_val in input_data.items():
                    flat_record[("input_data", input_data_col)] = input_data_val
                    column_tuples.add(("input_data", input_data_col))
            else:
                flat_record[("input_data", "")] = input_data
                column_tuples.add(("input_data", ""))

            expected_output = record.get("expected_output", {})
            if isinstance(expected_output, dict):
                for expected_output_col, expected_output_val in expected_output.items():
                    flat_record[("expected_output", expected_output_col)] = expected_output_val
                    column_tuples.add(("expected_output", expected_output_col))
            else:
                flat_record[("expected_output", "")] = expected_output
                column_tuples.add(("expected_output", ""))

            metadata = record.get("metadata", {})
            if isinstance(metadata, dict):
                for metadata_col, metadata_val in metadata.items():
                    flat_record[("metadata", metadata_col)] = metadata_val
                    column_tuples.add(("metadata", metadata_col))
            else:
                logger.warning("unexpected metadata format %s", type(metadata))

            tags = record.get("tags", [])
            flat_record[("tags", "")] = tags
            column_tuples.add(("tags", ""))

            data_rows.append(flat_record)

        records_list = []
        for flat_record in data_rows:
            row = [flat_record.get(col, None) for col in column_tuples]
            records_list.append(row)

        return pd.DataFrame(data=records_list, columns=pd.MultiIndex.from_tuples(column_tuples))


class Experiment:
    """Async-native experiment supporting both sync and async tasks, evaluators, and summary evaluators.

    This is the core experiment class. Sync evaluators are run via asyncio.to_thread().
    Sync tasks are also supported and will be run via asyncio.to_thread().

    Use ``LLMObs.async_experiment()`` to create an instance directly (for async callers),
    or ``LLMObs.experiment()`` to get a ``SyncExperiment`` wrapper (for sync callers).
    """

    _task: Union[TaskType, AsyncTaskType]
    _evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]]
    _summary_evaluators: Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]

    @classmethod
    def _NO_OP_TASK(cls, input_data, config):
        """No-op task used when initializing distributed experiment objects on remote hosts."""
        return None

    def __init__(
        self,
        name: str,
        task: Optional[Union[TaskType, AsyncTaskType]] = None,
        dataset: Optional[Dataset] = None,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]] = (),
        project_name: str = "",
        description: str = "",
        tags: Optional[dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]] = None,
        runs: Optional[int] = None,
        is_distributed: Optional[bool] = False,
    ) -> None:
        self.name = name
        self._task = task
        self._task_accepts_metadata = "metadata" in inspect.signature(task).parameters if task is not None else False
        self._dataset = dataset
        self._evaluators = list(evaluators)
        self._remote_evaluator_names: set[str] = {
            evaluator.name  # type: ignore[union-attr]
            for evaluator in self._evaluators
            if hasattr(evaluator, "_is_remote_evaluator") and evaluator._is_remote_evaluator
        }
        self._summary_evaluators = list(summary_evaluators) if summary_evaluators else []
        self._description = description
        self._tags: dict[str, str] = tags or {}
        self._tags["ddtrace.version"] = str(__version__)
        self._tags["project_name"] = project_name
        self._tags["dataset_name"] = dataset.name if dataset is not None else ""
        self._tags["experiment_name"] = name
        self._config: dict[str, JSONType] = config or {}
        # Write dataset tags to experiment config
        if dataset is not None and dataset.filter_tags:
            self._config["filtered_record_tags"] = dataset.filter_tags
        self._runs: int = runs or 1
        self._llmobs_instance = _llmobs_instance
        self._is_distributed = is_distributed
        self._retries: list[str] = []

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None
        # Populated from dataset if provided, or extracted from span tags during pull()
        # so that _create_rerun_experiment() can reference them without a dataset.
        self._dataset_id: Optional[str] = dataset._id if dataset is not None else None
        self._dataset_version: Optional[int] = dataset._version if dataset is not None else None
        self._run_name: Optional[str] = None
        self.experiment_span: Optional["ExportedLLMObsSpan"] = None
        self._interrupted: bool = False

    @property
    def url(self) -> str:
        # FIXME: will not work for subdomain orgs
        return f"{_get_base_url()}/llm/experiments/{self._id}"

    def _merge_results(
        self,
        run: _ExperimentRunInfo,
        task_results: list[TaskResult],
        evaluations: list[EvaluationResult],
        summary_evaluations: Optional[list[EvaluationResult]],
    ) -> ExperimentRun:
        experiment_results = []
        for idx, task_result in enumerate(task_results):
            output_data = task_result["output"]
            metadata: dict[str, JSONType] = {"tags": convert_tags_dict_to_list(self._tags)}
            metadata.update(task_result.get("metadata") or {})
            record: DatasetRecord = self._dataset[idx]
            evals = evaluations[idx]["evaluations"]
            exp_result: ExperimentRowResult = {
                "idx": idx,
                "span_id": task_result.get("span_id", ""),
                "trace_id": task_result.get("trace_id", ""),
                "timestamp": task_result.get("timestamp", 0),
                "duration": task_result.get("duration", 0),
                "span_name": task_result.get("span_name", "experiment_record"),
                "record_id": record.get("record_id", ""),
                "input": record["input_data"],
                "expected_output": record["expected_output"],
                "output": output_data,
                "evaluations": evals,
                "metadata": metadata,
                "error": task_result["error"],
            }
            experiment_results.append(exp_result)

        summary_evals: dict[str, dict[str, JSONType]] = {}
        if summary_evaluations:
            for summary_evaluation in summary_evaluations:
                for name, eval_data in summary_evaluation["evaluations"].items():
                    summary_evals[name] = eval_data

        return ExperimentRun(run, summary_evals, experiment_results)

    def _generate_metric_from_evaluation(
        self,
        eval_name: str,
        eval_value: JSONType,
        err: JSONType,
        span_id: str,
        trace_id: str,
        timestamp_ns: int,
        status: Optional[str] = None,
        source: str = "custom",
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        metadata: Optional[dict[str, JSONType]] = None,
        tags: Optional[dict[str, str]] = None,
        eval_source_type: Optional[str] = None,
    ) -> "LLMObsExperimentEvalMetricEvent":
        if eval_value is None:
            metric_type = "categorical"
        elif isinstance(eval_value, bool):
            metric_type = "boolean"
        elif isinstance(eval_value, (int, float)):
            metric_type = "score"
        elif isinstance(eval_value, dict):
            metric_type = "json"
        else:
            metric_type = "categorical"
            eval_value = str(eval_value).lower()
        eval_metric: "LLMObsExperimentEvalMetricEvent" = {
            "metric_source": source,
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp_ms": int(timestamp_ns / 1e6),
            "metric_type": metric_type,
            "label": eval_name,
            f"{metric_type}_value": eval_value,  # type: ignore[misc]
            "error": err,
            "tags": convert_tags_dict_to_list(tags),
            "experiment_id": self._id,
        }
        if status:
            eval_metric["status"] = status
        if reasoning:
            eval_metric["reasoning"] = reasoning
        if assessment:
            eval_metric["assessment"] = assessment
        if metadata:
            eval_metric["metadata"] = metadata
        if eval_source_type:
            eval_metric["eval_source_type"] = eval_source_type
        return eval_metric

    def _generate_metrics_from_exp_results(
        self, experiment_result: ExperimentRun
    ) -> list["LLMObsExperimentEvalMetricEvent"]:
        eval_metrics = []
        latest_timestamp: int = 0

        for exp_result in experiment_result.rows:
            evaluations = exp_result.get("evaluations") or {}
            span_id = exp_result.get("span_id", "")
            trace_id = exp_result.get("trace_id", "")
            timestamp_ns = cast(int, exp_result.get("timestamp", 0))
            if timestamp_ns > latest_timestamp:
                latest_timestamp = timestamp_ns

            for eval_name, eval_data in evaluations.items():
                if not eval_data:
                    continue
                eval_value = eval_data.get("value")
                eval_metric = self._generate_metric_from_evaluation(
                    eval_name,
                    eval_value,
                    eval_data.get("error"),
                    span_id,
                    trace_id,
                    timestamp_ns,
                    reasoning=str(eval_data.get("reasoning")) if isinstance(eval_data.get("reasoning"), str) else None,
                    assessment=str(eval_data.get("assessment"))
                    if isinstance(eval_data.get("assessment"), str)
                    else None,
                    metadata=cast(dict[str, JSONType], eval_data.get("metadata"))
                    if isinstance(eval_data.get("metadata"), dict)
                    else None,
                    tags=cast(dict[str, str], eval_data.get("tags"))
                    if isinstance(eval_data.get("tags"), dict)
                    else None,
                    eval_source_type="managed" if eval_name in self._remote_evaluator_names else None,
                    status=str(eval_data.get("status")) if isinstance(eval_data.get("status"), str) else "OK",
                )
                eval_metrics.append(eval_metric)

        for name, summary_eval_data in experiment_result.summary_evaluations.items():
            if not summary_eval_data:
                continue
            eval_metric = self._generate_metric_from_evaluation(
                name,
                summary_eval_data.get("value"),
                summary_eval_data.get("error"),
                "",
                "",
                latest_timestamp,
                source="summary",
            )
            eval_metrics.append(eval_metric)
        return eval_metrics

    def _generate_metrics_for_record(
        self,
        task_result: TaskResult,
        evaluation: EvaluationResult,
    ) -> list["LLMObsExperimentEvalMetricEvent"]:
        evaluations = evaluation.get("evaluations") or {}
        span_id = task_result.get("span_id", "")
        trace_id = task_result.get("trace_id", "")
        timestamp_ns = cast(int, task_result.get("timestamp", 0))
        metrics: list["LLMObsExperimentEvalMetricEvent"] = []
        for eval_name, eval_data in evaluations.items():
            if not eval_data:
                continue
            metrics.append(
                self._generate_metric_from_evaluation(
                    eval_name,
                    eval_data.get("value"),
                    eval_data.get("error"),
                    span_id,
                    trace_id,
                    timestamp_ns,
                    reasoning=str(eval_data.get("reasoning")) if isinstance(eval_data.get("reasoning"), str) else None,
                    assessment=str(eval_data.get("assessment"))
                    if isinstance(eval_data.get("assessment"), str)
                    else None,
                    metadata=cast(dict[str, JSONType], eval_data.get("metadata"))
                    if isinstance(eval_data.get("metadata"), dict)
                    else None,
                    tags=cast(dict[str, str], eval_data.get("tags"))
                    if isinstance(eval_data.get("tags"), dict)
                    else None,
                    eval_source_type="managed" if eval_name in self._remote_evaluator_names else None,
                    status=str(eval_data.get("status")) if isinstance(eval_data.get("status"), str) else "OK",
                )
            )
        return metrics

    def _get_subset_dataset(self, sample_size: Optional[int]) -> Dataset:
        """Get dataset containing the first sample_size records of the original dataset."""
        if sample_size is not None and sample_size < len(self._dataset):
            subset_records = [deepcopy(record) for record in self._dataset._records[:sample_size]]
            subset_name = "[Test subset of {} records] {}".format(sample_size, self._dataset.name)
            return Dataset(
                name=subset_name,
                project=self._dataset.project,
                dataset_id=self._dataset._id,
                records=subset_records,
                description=self._dataset.description,
                latest_version=self._dataset._latest_version,
                version=self._dataset._version,
                _dne_client=self._dataset._dne_client,
            )
        return self._dataset

    def _extract_evaluator_result(
        self, eval_result: Union[JSONType, EvaluatorResult]
    ) -> tuple[JSONType, dict[str, JSONType]]:
        extra_return_values: dict[str, JSONType] = {}
        if isinstance(eval_result, EvaluatorResult):
            if eval_result.reasoning:
                extra_return_values["reasoning"] = eval_result.reasoning
            if eval_result.assessment:
                extra_return_values["assessment"] = eval_result.assessment
            if eval_result.metadata:
                extra_return_values["metadata"] = eval_result.metadata
            if eval_result.tags:
                extra_return_values["tags"] = eval_result.tags
            if isinstance(eval_result, RemoteEvaluatorResult) and eval_result.status:
                extra_return_values["status"] = eval_result.status
            return eval_result.value, extra_return_values
        return eval_result, extra_return_values

    def _build_evaluator_error(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, RemoteEvaluatorError) and exc.backend_error:
            return exc.backend_error
        exc_type, exc_value, exc_tb = sys.exc_info()
        exc_type_name = type(exc).__name__ if exc_type is not None else "Unknown Exception"
        exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        return {
            "message": str(exc_value),
            "type": exc_type_name,
            "stack": exc_stack,
        }

    def _build_evaluator_status(self, exc: Exception) -> str:
        if isinstance(exc, RemoteEvaluatorError):
            return exc.status
        return "ERROR"

    def _prepare_summary_evaluator_data(
        self, task_results: list[TaskResult], eval_results: list[EvaluationResult]
    ) -> tuple[
        list[JSONType],
        list[JSONType],
        list[JSONType],
        list[dict[str, Any]],
        dict[str, list[JSONType]],
    ]:
        inputs: list[JSONType] = []
        outputs: list[JSONType] = []
        expected_outputs: list[JSONType] = []
        metadata_list: list[dict[str, Any]] = []
        eval_results_by_name: dict[str, list[JSONType]] = {}

        for idx, task_result in enumerate(task_results):
            outputs.append(task_result["output"])
            record: DatasetRecord = self._dataset[idx]
            inputs.append(record["input_data"])
            expected_outputs.append(record["expected_output"])
            record_metadata = record.get("metadata") or {}
            metadata_list.append({**record_metadata, "experiment_config": self._config})

            eval_result_at_idx_by_name = eval_results[idx]["evaluations"]
            for name, eval_value in eval_result_at_idx_by_name.items():
                if name not in eval_results_by_name:
                    eval_results_by_name[name] = []
                eval_results_by_name[name].append(eval_value.get("value"))

        return inputs, outputs, expected_outputs, metadata_list, eval_results_by_name

    def _update_status(self, status: str, error: Optional[str] = None) -> None:
        if not self._llmobs_instance or not self._id:
            return
        try:
            self._llmobs_instance._dne_client.experiment_update(cast(str, self._id), status=status, error=error)
        except Exception:
            logger.debug("Failed to update experiment status to %s", status, exc_info=True)

    def _setup_experiment(self, llmobs_not_enabled_error: str, ensure_unique: bool = True) -> None:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(llmobs_not_enabled_error)

        project = self._llmobs_instance._dne_client.project_create_or_get(self._project_name)
        self._project_id = project.get("_id", "")
        self._tags["project_id"] = self._project_id

        (
            experiment_id,
            experiment_run_name,
        ) = self._llmobs_instance._dne_client.experiment_create(
            self.name,
            self._dataset._id,
            self._project_id,
            self._dataset._version,
            self._config,
            convert_tags_dict_to_list(self._tags),
            self._description,
            self._runs,
            ensure_unique,
        )
        self._id = experiment_id
        self._tags["experiment_id"] = str(experiment_id)
        self._run_name = experiment_run_name

    def _create_rerun_experiment(
        self,
        evaluators: "Sequence[Union[EvaluatorType, AsyncEvaluatorType]]",
    ) -> "tuple[str, str]":
        """Create a new experiment entity for this re-run.

        The new experiment has ``parent_experiment_id`` set to the current experiment's ID,
        making the lineage traversable. Returns ``(experiment_id, experiment_name)``.

        :raises ValueError: if LLMObs is not enabled, if the current experiment has no ID,
                            or if dataset information cannot be resolved from the loaded
                            dataset or span tags from a prior pull().
        """
        assert self._llmobs_instance is not None and self._llmobs_instance.enabled

        # Resolve project_id lazily — not set if the experiment was never run() in this session
        if not self._project_id:
            project = self._llmobs_instance._dne_client.project_create_or_get(self._project_name)
            self._project_id = project.get("_id", "")
            self._tags["project_id"] = self._project_id

        # Resolve dataset_id — prefer the loaded dataset, fall back to what was extracted
        # from span tags during pull() and stored on self._dataset_id.
        dataset_id = (self._dataset._id if self._dataset is not None else None) or self._dataset_id
        if not dataset_id:
            raise ValueError(
                "Cannot create re-run experiment: dataset_id is not available. "
                "Either provide a dataset when creating the experiment or ensure "
                "the original spans include a 'dataset_id:<uuid>' tag (populated "
                "automatically when the original run was performed with a dataset)."
            )

        dataset_version = (self._dataset._version if self._dataset is not None else None) or self._dataset_version or 0

        evaluator_names = [
            e.name if hasattr(e, "name") else getattr(e, "__name__", str(e))  # type: ignore[union-attr]
            for e in evaluators
        ]
        new_name = f"{self.name}-rerun-{int(time.time() * 1000)}"

        experiment_id, _ = self._llmobs_instance._dne_client.experiment_create(
            name=new_name,
            dataset_id=dataset_id,
            project_id=self._project_id,
            dataset_version=dataset_version,
            exp_config={"evaluators": evaluator_names},
            tags=convert_tags_dict_to_list(self._tags),
            parent_experiment_id=str(self._id),
        )
        return experiment_id, new_name

    async def run(
        self,
        jobs: int = 10,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
        max_retries: int = 0,
        retry_delay: Optional[Callable[[int], float]] = None,
    ) -> ExperimentResult:
        """Run the experiment by executing the task on all dataset records and evaluating the results.

        :param jobs: Maximum number of concurrent task and evaluator executions (default: 10)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors (default: False)
        :param sample_size: Optional number of dataset records to sample for testing
                            (default: None, uses full dataset)
        :param max_retries: Maximum number of retries for failed tasks and evaluators (default: 0)
        :param retry_delay: Callable that takes the attempt number (0-based) and returns the delay
                            in seconds before the next retry. Default: ``0.1 * (attempt + 1)``
        :return: ExperimentResult containing evaluation results and metadata
        """

        def _default_retry_delay(attempt: int) -> float:
            return 0.1 * (attempt + 1)

        if retry_delay is None:
            retry_delay = _default_retry_delay
        elif not callable(retry_delay):
            raise TypeError("retry_delay must be a callable, got {}".format(type(retry_delay).__name__))
        if jobs < 1:
            raise ValueError("jobs must be at least 1")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self._task is None or self._dataset is None:
            raise ValueError(
                "task and dataset are required to run an experiment from scratch. "
                "Either provide them when creating the experiment, or call `pull()` first "
                "and use `rerun_evaluators()` to re-score the stored results."
            )

        self._setup_experiment(
            "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
            "and create the experiment via `LLMObs.async_experiment(...)` before running the experiment."
        )

        self._run_results = []
        self._retries.clear()
        self._interrupted = False
        self._has_errors = False
        self._update_status("running")
        try:
            for run_iteration in range(self._runs):
                run = _ExperimentRunInfo(run_iteration)
                self._tags["run_id"] = str(run._id)
                self._tags["run_iteration"] = str(run._run_iteration)
                task_results, evaluations = await self._run_tasks_with_evaluators(
                    jobs, run, raise_errors, sample_size, max_retries=max_retries, retry_delay=retry_delay
                )
                summary_evals = await self._run_summary_evaluators(task_results, evaluations, raise_errors, jobs=jobs)
                run_result = self._merge_results(run, task_results, evaluations, summary_evals)
                self._run_results.append(run_result)
        except BaseException:
            self._interrupted = True
            self._update_status("interrupted")
            raise
        finally:
            result = self._build_result(self._run_results)
            self._log_experiment_summary(result)
        if self._has_errors:
            self._update_status("failed", error=self._build_error_summary(result))
        else:
            self._update_status("completed")
        return result

    @staticmethod
    def _build_result(run_results: list) -> ExperimentResult:
        return {
            "summary_evaluations": run_results[0].summary_evaluations if run_results else {},
            "rows": run_results[0].rows if run_results else [],
            "runs": run_results,
        }

    def _format_experiment_summary(self, result: ExperimentResult) -> str:
        runs = result.get("runs", [])
        parts: list[str] = []

        if self._interrupted:
            parts.append("Experiment '{}' was interrupted after {}/{} runs.".format(self.name, len(runs), self._runs))

        for run_idx, run in enumerate(runs):
            rows = run.rows
            run_label = "Run {}/{}".format(run_idx + 1, self._runs) if self._runs > 1 else ""
            task_error_count = sum(
                1 for row in rows if isinstance(row.get("error"), dict) and row["error"].get("message")
            )
            eval_stats: dict[str, dict[str, int]] = {}
            for row in rows:
                for name, data in (row.get("evaluations") or {}).items():
                    stats = eval_stats.setdefault(name, {"total": 0, "errors": 0})
                    stats["total"] += 1
                    if isinstance(data, dict) and data.get("error"):
                        stats["errors"] += 1

            header = "Experiment '{}'".format(self.name)
            if run_label:
                header += " - {}".format(run_label)
            parts.append("{}: {} rows, {} evaluator(s).".format(header, len(rows), len(eval_stats)))
            if task_error_count:
                parts.append("  Task errors: {}/{}".format(task_error_count, len(rows)))
            for eval_name, stats in eval_stats.items():
                if stats["errors"]:
                    parts.append(
                        "  {}: {}/{} evaluated, {} error(s)".format(
                            eval_name, stats["total"], len(rows), stats["errors"]
                        )
                    )
                else:
                    parts.append("  {}: {}/{} evaluated".format(eval_name, stats["total"], len(rows)))

        if self._retries:
            parts.append("Retries ({}):\n  {}".format(len(self._retries), "\n  ".join(self._retries)))

        return "\n".join(parts)

    @staticmethod
    def _build_error_summary(result: ExperimentResult) -> str:
        errors = []
        for run in result.get("runs", []):
            for row in run.rows:
                err = row.get("error")
                if isinstance(err, dict) and err.get("message"):
                    errors.append("{}: {}".format(err.get("type", "Error"), err["message"]))
                for name, data in (row.get("evaluations") or {}).items():
                    if not isinstance(data, dict):
                        continue
                    eval_err = data.get("error")
                    if isinstance(eval_err, dict) and eval_err.get("message"):
                        errors.append("{} ({}): {}".format(name, eval_err.get("type", "Error"), eval_err["message"]))
        if not errors:
            return "unknown error"
        return "; ".join(set(errors))

    def _log_experiment_summary(self, result: ExperimentResult) -> None:
        msg = self._format_experiment_summary(result)
        if not msg:
            return
        log_fn = logger.warning if self._interrupted else logger.info
        log_fn(msg, extra={"product": "llmobs"})

    async def _process_record(
        self,
        idx_record: tuple[int, DatasetRecord],
        run: _ExperimentRunInfo,
        semaphore,
        max_retries: int = 0,
        retry_delay: Callable[[int], float] = lambda attempt: 0.1 * (attempt + 1),
    ) -> Optional[TaskResult]:
        """Process single record asynchronously."""
        asyncio = get_asyncio()
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return None
        async with semaphore:
            idx, record = idx_record
            with self._llmobs_instance._experiment(
                name=self._task.__name__,
                experiment_id=self._id,
                run_id=str(run._id),
                run_iteration=run._run_iteration,
                dataset_name=self._dataset.name,
                project_name=self._project_name,
                project_id=self._project_id,
                experiment_name=self.name,
            ) as span:
                span_context = self._llmobs_instance.export_span(span=span)
                if self._is_distributed:
                    self.experiment_span = span_context
                if span_context:
                    span_id = span_context.get("span_id", "")
                    trace_id = span_context.get("trace_id", "")
                else:
                    span_id, trace_id = "", ""
                input_data = record["input_data"]
                record_id = record.get("record_id", "")
                canonical_id = record.get("canonical_id")
                tags = {
                    **self._tags,
                    "dataset_id": str(self._dataset._id),
                    "dataset_record_id": str(record_id),
                    "experiment_id": str(self._id),
                }
                # Propagate dataset record tags to the experiment span
                record_tags = record.get("tags", [])
                for tag in record_tags:
                    if ":" in tag:
                        key, value = tag.split(":", 1)
                        tags[key] = value
                if canonical_id:
                    tags["dataset_record_canonical_id"] = canonical_id
                output_data = None
                last_exc_info = None
                record_metadata = record.get("metadata") or {}
                task_args: list = [input_data, self._config]
                if self._task_accepts_metadata:
                    task_args.append(record_metadata)
                for attempt in range(1 + max_retries):
                    try:
                        if asyncio.iscoroutinefunction(self._task):
                            output_data = await self._task(*task_args)  # type: ignore[misc]
                        else:
                            output_data = await asyncio.to_thread(self._task, *task_args)
                        last_exc_info = None
                        break
                    except Exception as e:
                        last_exc_info = sys.exc_info()
                        if attempt < max_retries:
                            self._retries.append(
                                "task row {}: attempt {}/{} failed: {}".format(idx, attempt + 1, max_retries + 1, e)
                            )
                            semaphore.release()
                            try:
                                await asyncio.sleep(retry_delay(attempt))
                            finally:
                                await semaphore.acquire()
                if attempt > 0:
                    tags["retries"] = str(attempt)
                if last_exc_info:
                    self._has_errors = True
                    span.set_exc_info(*last_exc_info)
                self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)
                _annotate_llmobs_span_data(
                    span,
                    expected_output=record.get("expected_output"),
                    metadata=record.get("metadata"),
                    config=self._config or None,
                )
            # span.__exit__ has now been called and span.finish() has run;
            # span.duration_ns is the real wall-clock duration in nanoseconds, not 0.
            return {
                "idx": idx,
                "span_id": span_id,
                "trace_id": trace_id,
                "timestamp": span.start_ns,
                "duration": span.duration_ns,
                "span_name": self._task.__name__,
                "output": output_data,
                "metadata": {
                    "dataset_record_index": idx,
                    "experiment_name": self.name,
                    "dataset_name": self._dataset.name,
                },
                "error": {
                    "message": span.get_tag(ERROR_MSG),
                    "stack": span.get_tag(ERROR_STACK),
                    "type": span.get_tag(ERROR_TYPE),
                },
            }

    def _check_task_result_error(self, task_result: TaskResult, raise_errors: bool) -> None:
        err_dict = task_result.get("error") or {}
        if isinstance(err_dict, dict):
            err_msg = err_dict.get("message")
            err_stack = err_dict.get("stack")
            err_type = err_dict.get("type")
            if raise_errors and err_msg:
                raise RuntimeError(
                    "Error on record {}: {}\n{}\n{}".format(task_result["idx"], err_msg, err_type, err_stack)
                )

    async def _run_task(
        self,
        jobs: int,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
        max_retries: int = 0,
        retry_delay: Callable[[int], float] = lambda attempt: 0.1 * (attempt + 1),
    ) -> list[TaskResult]:
        asyncio = get_asyncio()
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        subset_dataset = self._get_subset_dataset(sample_size)

        semaphore = asyncio.Semaphore(jobs)
        coros = [
            self._process_record(
                idx_record,
                run,
                semaphore,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            for idx_record in enumerate(subset_dataset)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        task_results: list[TaskResult] = []
        for result in results:
            if isinstance(result, BaseException):
                if raise_errors:
                    raise result
                continue
            if not result:
                continue
            task_result: TaskResult = result
            task_results.append(task_result)
            self._check_task_result_error(task_result, raise_errors)

        self._llmobs_instance.flush()  # Ensure spans get submitted in serverless environments
        return task_results

    async def _evaluate_record(
        self,
        record: DatasetRecord,
        task_result: TaskResult,
        semaphore,
        raise_errors: bool = False,
        max_retries: int = 0,
        retry_delay: Callable[[int], float] = lambda attempt: 0.1 * (attempt + 1),
        _override_evaluators: Optional[Sequence[Union[EvaluatorType, AsyncEvaluatorType]]] = None,
    ) -> EvaluationResult:
        asyncio = get_asyncio()
        idx = task_result["idx"]
        input_data = record["input_data"]
        output_data = task_result["output"]
        expected_output = record["expected_output"]
        metadata = record.get("metadata", {})

        async def _run_single_evaluator(
            evaluator: Union[EvaluatorType, AsyncEvaluatorType],
        ) -> tuple[str, dict[str, JSONType]]:
            async with semaphore:
                eval_result_value: JSONType = None
                eval_err: JSONType = None
                extra_return_values: dict[str, JSONType] = {}
                evaluator_name = ""

                for attempt in range(1 + max_retries):
                    eval_result_value = None
                    eval_err = None
                    try:
                        if isinstance(evaluator, BaseAsyncEvaluator):
                            evaluator_name = evaluator.name
                            combined_metadata = {
                                **metadata,
                                "experiment_config": self._config,
                            }
                            context = EvaluatorContext(
                                input_data=input_data,
                                output_data=output_data,
                                expected_output=expected_output,
                                metadata=combined_metadata,
                                span_id=task_result.get("span_id"),
                                trace_id=task_result.get("trace_id"),
                            )
                            eval_result = await evaluator.evaluate(context)
                        elif asyncio.iscoroutinefunction(evaluator):
                            evaluator_name = evaluator.__name__  # type: ignore[union-attr]
                            eval_result = await evaluator(input_data, output_data, expected_output)  # type: ignore[misc, operator]
                        elif _is_class_evaluator(evaluator):
                            evaluator_name = evaluator.name  # type: ignore[union-attr]
                            combined_metadata = {
                                **metadata,
                                "experiment_config": self._config,
                            }
                            context = EvaluatorContext(
                                input_data=input_data,
                                output_data=output_data,
                                expected_output=expected_output,
                                metadata=combined_metadata,
                                span_id=task_result.get("span_id"),
                                trace_id=task_result.get("trace_id"),
                            )
                            eval_result = await asyncio.to_thread(
                                evaluator.evaluate,  # type: ignore[union-attr]
                                context,
                            )
                        elif _is_function_evaluator(evaluator):
                            evaluator_name = evaluator.__name__  # type: ignore[union-attr]
                            eval_result = await asyncio.to_thread(
                                evaluator,
                                input_data,
                                output_data,
                                expected_output,
                            )
                        else:
                            logger.warning(
                                "Evaluator %s is neither a BaseEvaluator instance nor a callable function",
                                evaluator,
                            )
                            evaluator_name = str(evaluator)
                            eval_result = None

                        eval_result_value, extra_return_values = self._extract_evaluator_result(eval_result)
                        break

                    except Exception as e:
                        extra_return_values = {}
                        eval_err = self._build_evaluator_error(e)
                        if attempt < max_retries:
                            self._retries.append(
                                "evaluator '{}' row {}: attempt {}/{} failed: {}".format(
                                    evaluator_name, idx, attempt + 1, max_retries + 1, e
                                )
                            )
                            semaphore.release()
                            try:
                                await asyncio.sleep(retry_delay(attempt))
                            finally:
                                await semaphore.acquire()
                            continue
                        extra_return_values = {"status": self._build_evaluator_status(e)}
                        self._has_errors = True
                        if raise_errors:
                            raise RuntimeError(f"Evaluator {evaluator_name} failed on row {idx}") from e

                return evaluator_name, {
                    "value": eval_result_value,
                    "error": eval_err,
                    **extra_return_values,
                }

        evaluators_to_use = _override_evaluators if _override_evaluators is not None else self._evaluators
        results = await asyncio.gather(
            *[_run_single_evaluator(ev) for ev in evaluators_to_use],
            return_exceptions=True,
        )
        row_results: dict[str, dict[str, JSONType]] = {}
        for r in results:
            if isinstance(r, BaseException):
                if raise_errors:
                    raise r
                continue
            name, result = r
            row_results[name] = result
        return {"idx": idx, "evaluations": row_results}

    async def _run_evaluators(
        self,
        task_results: list[TaskResult],
        raise_errors: bool = False,
        jobs: int = 10,
        max_retries: int = 0,
        retry_delay: Callable[[int], float] = lambda attempt: 0.1 * (attempt + 1),
    ) -> list[EvaluationResult]:
        asyncio = get_asyncio()
        semaphore = asyncio.Semaphore(jobs)
        coros = [
            self._evaluate_record(self._dataset[idx], task_result, semaphore, raise_errors, max_retries, retry_delay)
            for idx, task_result in enumerate(task_results)
        ]
        return list(await asyncio.gather(*coros))

    async def _run_tasks_with_evaluators(
        self,
        jobs: int,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
        max_retries: int = 0,
        retry_delay: Callable[[int], float] = lambda attempt: 0.1 * (attempt + 1),
    ) -> tuple[list[TaskResult], list[EvaluationResult]]:
        asyncio = get_asyncio()
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return [], []
        subset_dataset = self._get_subset_dataset(sample_size)
        semaphore = asyncio.Semaphore(jobs)
        pending_evals: list["LLMObsExperimentEvalMetricEvent"] = []
        flush_threshold = jobs

        async def _process_and_evaluate(
            idx_record: tuple[int, DatasetRecord],
        ) -> tuple[Optional[TaskResult], Optional[EvaluationResult]]:
            task_result = await self._process_record(
                idx_record, run, semaphore, max_retries=max_retries, retry_delay=retry_delay
            )
            if task_result is None:
                return None, None
            evaluation = await self._evaluate_record(
                idx_record[1],
                task_result,
                semaphore,
                raise_errors=raise_errors,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            if evaluation:
                metrics = self._generate_metrics_for_record(task_result, evaluation)
                pending_evals.extend(metrics)
                if len(pending_evals) >= flush_threshold:
                    try:
                        self._llmobs_instance._dne_client.experiment_eval_post(  # type: ignore[union-attr]
                            cast(str, self._id),
                            list(pending_evals),
                            convert_tags_dict_to_list(self._tags),
                        )
                    except Exception:
                        logger.debug("Failed to flush pending eval metrics", exc_info=True)
                    else:
                        pending_evals.clear()
                        self._llmobs_instance.flush()  # type: ignore[union-attr]
            return task_result, evaluation

        coros = [_process_and_evaluate(idx_record) for idx_record in enumerate(subset_dataset)]
        results = await asyncio.gather(*coros, return_exceptions=True)

        task_results: list[TaskResult] = []
        evaluations: list[EvaluationResult] = []
        for result in results:
            if isinstance(result, BaseException):
                if raise_errors:
                    raise result
                continue
            if result is None:
                continue
            task_result, evaluation = result
            if task_result is None:
                continue
            task_results.append(task_result)
            self._check_task_result_error(task_result, raise_errors)
            if evaluation is not None:
                evaluations.append(evaluation)

        if pending_evals and self._llmobs_instance:
            try:
                self._llmobs_instance._dne_client.experiment_eval_post(
                    cast(str, self._id),
                    list(pending_evals),
                    convert_tags_dict_to_list(self._tags),
                )
            except Exception:
                logger.debug("Failed to flush pending eval metrics", exc_info=True)
            else:
                pending_evals.clear()
        self._llmobs_instance.flush()
        return task_results, evaluations

    async def _run_summary_evaluators(
        self,
        task_results: list[TaskResult],
        eval_results: list[EvaluationResult],
        raise_errors: bool = False,
        jobs: int = 10,
    ) -> list[EvaluationResult]:
        if not self._summary_evaluators:
            return []
        (
            inputs,
            outputs,
            expected_outputs,
            metadata_list,
            eval_results_by_name,
        ) = self._prepare_summary_evaluator_data(task_results, eval_results)
        asyncio = get_asyncio()
        semaphore = asyncio.Semaphore(jobs)

        async def _evaluate_summary_single(
            summary_evaluator: Any,
        ) -> tuple[str, dict[str, JSONType]]:
            async with semaphore:
                eval_result_value: JSONType = None
                eval_err: JSONType = None
                evaluator_name = ""

                try:
                    if isinstance(summary_evaluator, BaseAsyncSummaryEvaluator):
                        evaluator_name = summary_evaluator.name
                        context = SummaryEvaluatorContext(
                            inputs=inputs,
                            outputs=outputs,
                            expected_outputs=expected_outputs,
                            evaluation_results=eval_results_by_name,
                            metadata=metadata_list,
                        )
                        eval_result = await summary_evaluator.evaluate(context)
                    elif asyncio.iscoroutinefunction(summary_evaluator):
                        evaluator_name = summary_evaluator.__name__
                        signature = inspect.signature(summary_evaluator)
                        if len(signature.parameters) == 1:
                            context = SummaryEvaluatorContext(
                                inputs=inputs,
                                outputs=outputs,
                                expected_outputs=expected_outputs,
                                evaluation_results=eval_results_by_name,
                                metadata=metadata_list,
                            )
                            eval_result = await summary_evaluator(context)
                        else:
                            eval_result = await summary_evaluator(
                                inputs, outputs, expected_outputs, eval_results_by_name
                            )
                    elif _is_class_summary_evaluator(summary_evaluator):
                        evaluator_name = summary_evaluator.name
                        context = SummaryEvaluatorContext(
                            inputs=inputs,
                            outputs=outputs,
                            expected_outputs=expected_outputs,
                            evaluation_results=eval_results_by_name,
                            metadata=metadata_list,
                        )
                        eval_result = await asyncio.to_thread(summary_evaluator.evaluate, context)
                    else:
                        evaluator_name = summary_evaluator.__name__
                        signature = inspect.signature(summary_evaluator)
                        if len(signature.parameters) == 1:
                            context = SummaryEvaluatorContext(
                                inputs=inputs,
                                outputs=outputs,
                                expected_outputs=expected_outputs,
                                evaluation_results=eval_results_by_name,
                                metadata=metadata_list,
                            )
                            eval_result = summary_evaluator(context)
                        else:
                            eval_result = await asyncio.to_thread(
                                summary_evaluator,
                                inputs,
                                outputs,
                                expected_outputs,
                                eval_results_by_name,
                            )
                    eval_result_value = eval_result
                except Exception as e:
                    self._has_errors = True
                    eval_err = self._build_evaluator_error(e)
                    if raise_errors:
                        raise RuntimeError(f"Summary evaluator {evaluator_name} failed") from e

                return (
                    evaluator_name,
                    {
                        "value": eval_result_value,
                        "error": eval_err,
                    },
                )

        coros = [_evaluate_summary_single(summary_evaluator) for summary_evaluator in self._summary_evaluators]
        results = await asyncio.gather(*coros, return_exceptions=not raise_errors)

        evaluations: list[EvaluationResult] = []
        evals_dict: dict[str, dict[str, JSONType]] = {}

        for idx, result in enumerate(results):
            if isinstance(result, BaseException):
                continue
            evaluator_name, eval_data = cast(tuple[str, dict[str, JSONType]], result)
            evals_dict[evaluator_name] = eval_data
            evaluations.append({"idx": idx, "evaluations": evals_dict})

        if evals_dict and self._id and self._llmobs_instance:
            latest_timestamp = max(
                (cast(int, r.get("timestamp", 0)) for r in task_results),
                default=0,
            )
            metrics: list["LLMObsExperimentEvalMetricEvent"] = []
            for name, summary_eval_data in evals_dict.items():
                if not summary_eval_data:
                    continue
                metrics.append(
                    self._generate_metric_from_evaluation(
                        name,
                        summary_eval_data.get("value"),
                        summary_eval_data.get("error"),
                        "",
                        "",
                        latest_timestamp,
                        source="summary",
                    )
                )
            if metrics:
                self._llmobs_instance._dne_client.experiment_eval_post(
                    cast(str, self._id),
                    metrics,
                    convert_tags_dict_to_list(self._tags),
                )

        return evaluations

    async def _run_task_single_iteration(
        self,
        jobs: int = 1,
        raise_errors: bool = False,
        run_iteration: Optional[int] = 0,
    ) -> ExperimentResult:
        run = _ExperimentRunInfo(run_iteration or 0)
        self._tags["run_id"] = str(run._id)
        self._tags["run_iteration"] = str(run._run_iteration)
        task_results = await self._run_task(jobs, run, raise_errors, None)
        evaluations = await self._run_evaluators(task_results, raise_errors=raise_errors, jobs=jobs)
        run_result = self._merge_results(run, task_results, evaluations, [])
        experiment_evals = self._generate_metrics_from_exp_results(run_result)
        self._llmobs_instance._dne_client.experiment_eval_post(  # type: ignore[union-attr]
            cast(str, self._id), experiment_evals, convert_tags_dict_to_list(self._tags)
        )
        return {
            "summary_evaluations": {},
            "rows": [],
            "runs": [run_result],
        }

    def _submit_eval_metric(
        self,
        eval_name: str,
        eval_value: JSONType,
        span: Optional["ExportedLLMObsSpan"] = None,
        timestamp_ms: Optional[int] = None,
        is_summary_eval: Optional[bool] = None,
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        metadata: Optional[dict[str, JSONType]] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> None:
        """Submit an evaluation metric for a distributed experiment.

        :param eval_name: Name of the evaluation metric
        :param eval_value: Value of the evaluation metric
        :param span: Optional span context dict with span_id and trace_id. If None and not a
                     summary eval, uses the last span from _run_task_single_iteration.
        :param timestamp_ms: Optional timestamp in milliseconds
        :param is_summary_eval: Whether this is a summary-level evaluation
        :param reasoning: Optional reasoning string
        :param assessment: Optional assessment string
        :param metadata: Optional metadata dict
        :param tags: Optional tags dict
        """
        if not self._is_distributed:
            raise ValueError("this method is only used for distributed experiments")

        if span is not None and (
            not isinstance(span, dict)
            or not isinstance(span.get("span_id"), str)
            or not isinstance(span.get("trace_id"), str)
        ):
            raise TypeError(
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )

        if span is None and not is_summary_eval and self.experiment_span is None:
            raise TypeError("unexpected state, must supply span or must run the experiment first")

        if span is None and not is_summary_eval:
            span = self.experiment_span

        timestamp_ns = int(timestamp_ms * 1e6) if timestamp_ms is not None else int(time.time() * 1e9)

        eval_metric = self._generate_metric_from_evaluation(
            eval_name,
            eval_value,
            None,
            span.get("span_id", "") if span else "",
            span.get("trace_id", "") if span else "",
            timestamp_ns,
            source="summary" if is_summary_eval else "custom",
            reasoning=reasoning,
            assessment=assessment,
            metadata=metadata,
            tags=tags,
        )

        self._llmobs_instance._dne_client.experiment_eval_post(  # type: ignore[union-attr]
            cast(str, self._id), [eval_metric], convert_tags_dict_to_list(self._tags)
        )


class SyncExperiment:
    """Thin synchronous wrapper around the async-native ``Experiment``.

    Provides a blocking ``run()`` method for callers that do not have an event loop.
    """

    def __init__(
        self,
        name: str,
        task: Optional[Union[TaskType, AsyncTaskType]] = None,
        dataset: Optional[Dataset] = None,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]] = (),
        project_name: str = "",
        description: str = "",
        tags: Optional[dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        self.result: Optional[ExperimentResult] = None
        self._experiment = Experiment(
            name=name,
            task=task,
            dataset=dataset,
            evaluators=evaluators,
            project_name=project_name,
            description=description,
            tags=tags,
            config=config,
            _llmobs_instance=_llmobs_instance,
            summary_evaluators=summary_evaluators,
            runs=runs,
        )
        self.result: Optional[ExperimentResult] = None

    @classmethod
    def _for_rerun(
        cls,
        parent: "SyncExperiment",
        new_id: str,
        new_name: str,
        evaluators: "Sequence[Union[EvaluatorType, AsyncEvaluatorType]]",
        result: ExperimentResult,
    ) -> "SyncExperiment":
        """Build a SyncExperiment clone representing a completed rerun.

        Shallow-copies the parent's Experiment, overriding only the fields that differ
        for the rerun (id, name, tags, evaluators). All other state — dataset, task,
        llmobs_instance, config, project info — is shared by reference.
        """
        rerun = cls.__new__(cls)
        exp = copy(parent._experiment)
        exp._id = new_id
        exp.name = new_name
        exp._tags = {**parent._experiment._tags, "experiment_id": new_id}
        exp._evaluators = list(evaluators)
        exp._remote_evaluator_names = {
            e.name  # type: ignore[union-attr]
            for e in evaluators
            if hasattr(e, "_is_remote_evaluator") and e._is_remote_evaluator
        }
        exp._run_results = []
        exp._retries = []
        exp._has_errors = False
        exp._interrupted = False
        rerun._experiment = exp
        rerun.result = result
        return rerun

    def run(
        self,
        jobs: int = 1,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
        max_retries: int = 0,
        retry_delay: Optional[Callable[[int], float]] = None,
    ) -> ExperimentResult:
        """Run the experiment synchronously.

        :param jobs: Maximum number of concurrent task and evaluator executions (default: 1)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors (default: False)
        :param sample_size: Optional number of dataset records to sample for testing
                            (default: None, uses full dataset)
        :param max_retries: Maximum number of retries for failed tasks and evaluators (default: 0)
        :param retry_delay: Callable that takes the attempt number (0-based) and returns the delay
                            in seconds before the next retry. Default: ``0.1 * (attempt + 1)``
        :return: ExperimentResult containing evaluation results and metadata. Also stored on
                 ``self.result``.
        """
        asyncio = get_asyncio()
        coro = self._experiment.run(
            jobs=jobs,
            raise_errors=raise_errors,
            sample_size=sample_size,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(coro)
        else:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, coro).result()
        self.result = result
        return result

    def as_dataframe(self) -> "pd.DataFrame":
        """Return all runs stacked into a single MultiIndex DataFrame.

        Rows from every run in ``self.result["runs"]`` are concatenated in
        run-iteration order. Two extra top-level column groups are prepended:

        - ``("run_id", "")``        — UUID of the run (str)
        - ``("run_iteration", "")`` — 1-based run counter (int)

        These columns let callers filter or group by run:

        .. code-block:: python

            df = experiment.as_dataframe()
            run1 = df[df[("run_iteration", "")] == 1]
            df.groupby(("run_iteration", ""))[(\"evaluations\", \"exact_match\")].apply(...)

        :raises ValueError: if ``self.result`` is ``None`` (experiment not yet run or pulled).
        :raises ImportError: if ``pandas`` is not installed.
        :return: ``pd.DataFrame`` with ``pd.MultiIndex`` columns and a reset integer index.
        """
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError(
                "pandas is required to convert experiment results to a DataFrame. "
                "Please install it via `pip install pandas`."
            ) from e

        if self.result is None:
            raise ValueError("No result found. Call run() or pull() before as_dataframe().")

        frames = []
        for run in self.result.get("runs", []):
            df = run.as_dataframe()
            df.insert(0, ("run_id", ""), str(run.run_id))
            df.insert(1, ("run_iteration", ""), run.run_iteration)
            df.columns = pd.MultiIndex.from_tuples(df.columns)
            frames.append(df)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    def rerun_evaluators(
        self,
        evaluators: "Optional[Sequence[Union[EvaluatorType, AsyncEvaluatorType]]]" = None,
        missing_task_strategy: str = "raise",
        jobs: int = 1,
        raise_errors: bool = False,
        max_retries: int = 0,
        retry_delay: Optional[Callable[[int], float]] = None,
        summary_evaluators: Optional["Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]"] = None,
    ) -> "SyncExperiment":
        """Re-run evaluators on the stored task outputs from the previous run.

        Creates a new experiment entity on the backend (linked via ``parent_experiment_id``)
        and returns it as a new ``SyncExperiment`` object, leaving the original experiment
        unchanged.  This means multiple independent reruns can be created from the same
        original without losing track of any of them:

        .. code-block:: python

            rerun1 = exp.rerun_evaluators([evaluator_v2])
            rerun2 = exp.rerun_evaluators([evaluator_v3])
            # exp, rerun1, rerun2 all have their own IDs and results

        Reads task outputs from ``self.result`` — call ``run()`` or ``pull()`` first.

        :param evaluators: Evaluators to run against the stored outputs. When omitted (or ``None``),
                           the experiment's own evaluators (set at construction time) are reused.
                           Pass an explicit list to override — for example to fix a bug in an
                           evaluator or add a new one — without modifying the original experiment.
        :param missing_task_strategy: How to handle rows with task errors.
                                      ``"raise"`` (default) raises immediately, ``"skip"``
                                      drops failed rows, ``"retry"`` re-executes failed tasks.
        :param jobs: Maximum number of concurrent evaluator executions (default: 1)
        :param raise_errors: Whether to raise exceptions on evaluator errors (default: False)
        :param max_retries: Maximum number of retries for failed evaluators (default: 0)
        :param retry_delay: Callable that takes the attempt number (0-based) and returns the delay
                            in seconds before the next retry. Default: ``0.1 * (attempt + 1)``
        :param summary_evaluators: Optional summary evaluators to run after all rows are evaluated.
                                   When omitted, the experiment's own summary evaluators are used.
        :return: New ``SyncExperiment`` representing the rerun, with its own ID and result.
        :raises ValueError: if no evaluators are available (neither provided nor set on the experiment),
                            or ``self.result`` is ``None``.
        """
        # Resolve evaluators: use provided list or fall back to the experiment's own evaluators.
        resolved_evaluators: "Sequence[Union[EvaluatorType, AsyncEvaluatorType]]"
        if evaluators is not None:
            resolved_evaluators = evaluators
        else:
            resolved_evaluators = self._experiment._evaluators
        if not resolved_evaluators:
            raise ValueError(
                "No evaluators available for rerun_evaluators(). "
                "Either pass an evaluators list or set evaluators when creating the experiment."
            )
        if self.result is None:
            raise ValueError(
                "No previous result found. Run the experiment first via `run()` or load a prior run via `pull()`."
            )
        return self._rerun_evaluators(
            self.result,
            list(resolved_evaluators),
            missing_task_strategy,
            jobs,
            raise_errors,
            max_retries,
            retry_delay,
            list(summary_evaluators) if summary_evaluators else None,
        )

    def pull(self, include_eval_metrics: bool = True) -> None:
        """Fetch prior experiment results from the backend and store them on ``self.result``.

        Uses the experiment's own name and project to find the latest run (or uses the
        known ``_id`` directly if the experiment has already been run in this session).
        Returns ``None`` — results are accessible via ``self.result``.

        After a successful ``pull()``, ``rerun_evaluators()`` can be called immediately
        to re-score the fetched outputs with updated evaluators.

        :param include_eval_metrics: Whether to include evaluator scores from the original
                                     run. Default: ``True``.
        :raises ValueError: If LLMObs is not enabled.
        :raises ValueError: If neither an experiment ID nor name+project are available.
        :raises ValueError: If the backend call fails.
        """
        if not self._experiment._llmobs_instance or not self._experiment._llmobs_instance.enabled:
            raise ValueError("LLMObs is not enabled. Enable LLMObs before calling pull().")

        client = self._experiment._llmobs_instance._dne_client
        experiment_id = self._experiment._id

        if experiment_id:
            raw = client.experiment_events_get(
                experiment_id=str(experiment_id),
                include_eval_metrics=include_eval_metrics,
            )
        else:
            experiment_name = self._experiment.name
            project_name = self._experiment._project_name
            if not experiment_name or not project_name:
                raise ValueError(
                    "experiment name and project_name are required to pull results when "
                    "the experiment has not been run in the current session."
                )
            raw = client.experiment_events_get(
                project_name=project_name,
                experiment_name=experiment_name,
                include_eval_metrics=include_eval_metrics,
            )

        self.result = _parse_experiment_result(raw)

        # Populate _id from data.id and dataset_id / project_id from the first span's tags.
        # The /events API returns a single JSON:API object whose primary id is the experiment UUID;
        # span-level tags carry the dataset_id / project_id.
        data = raw.get("data") or {}
        attributes = data.get("attributes") or {}
        if not self._experiment._id:
            experiment_id_from_response = data.get("id")
            if experiment_id_from_response:
                self._experiment._id = experiment_id_from_response
        spans = attributes.get("spans") or []
        first_span = spans[0] if spans else {}
        for tag in first_span.get("tags") or []:
            if not isinstance(tag, str):
                continue
            if not self._experiment._id and tag.startswith("experiment_id:"):
                self._experiment._id = tag.split(":", 1)[1]
            elif tag.startswith("dataset_id:") and self._experiment._dataset_id is None:
                self._experiment._dataset_id = tag.split(":", 1)[1]
            elif tag.startswith("project_id:") and not self._experiment._project_id:
                self._experiment._project_id = tag.split(":", 1)[1]

    def _rerun_evaluators(
        self,
        previous_result: ExperimentResult,
        evaluators: "list[Union[EvaluatorType, AsyncEvaluatorType]]",
        missing_task_strategy: str,
        jobs: int,
        raise_errors: bool,
        max_retries: int,
        retry_delay: Optional[Callable[[int], float]],
        summary_evaluators: "Optional[list[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]]",
    ) -> "SyncExperiment":
        """Re-run evaluators on task outputs from a previous experiment run.

        Creates a new experiment entity on the backend linked to the original via
        ``parent_experiment_id``.  Span copies with fresh UUIDs are emitted so the
        re-run is fully independent.  The original experiment's state is never mutated —
        all rerun-specific state (ID, tags, evaluators) is carried locally and only written
        to the returned ``SyncExperiment`` clone.

        :param previous_result: ExperimentResult whose task outputs to reuse.
        :param evaluators: Row-level evaluators to run (already resolved by ``rerun_evaluators``).
        :param missing_task_strategy: ``"raise"`` (default), ``"skip"``, or ``"retry"``.
        :param summary_evaluators: Optional summary evaluators; if None uses the experiment's own.
        :raises ValueError: if ``missing_task_strategy`` is invalid, ``"raise"`` is used
                            with rows that have task errors, LLMObs is not enabled, or the
                            experiment has not been run yet (``self._experiment._id`` is None).
        """
        if missing_task_strategy not in ("raise", "skip", "retry"):
            raise ValueError(
                "missing_task_strategy must be 'raise', 'skip', or 'retry', got '{}'.".format(missing_task_strategy)
            )

        rows = previous_result["runs"][0].rows if previous_result.get("runs") else previous_result["rows"]

        if missing_task_strategy == "raise":
            for row in rows:
                if (row.get("error") or {}).get("message"):
                    raise ValueError(
                        "Cannot re-run evaluators: row {} has a task error. "
                        "Use missing_task_strategy='skip' or 'retry' to handle failed rows.".format(row["idx"])
                    )
            rows_to_eval = rows
            rows_to_retry: list[ExperimentRowResult] = []
        elif missing_task_strategy == "skip":
            rows_to_eval = [r for r in rows if not (r.get("error") or {}).get("message")]
            rows_to_retry = []
        else:  # "retry"
            rows_to_eval = [r for r in rows if not (r.get("error") or {}).get("message")]
            rows_to_retry = [r for r in rows if (r.get("error") or {}).get("message")]

        def _default_retry_delay(attempt: int) -> float:
            return 0.1 * (attempt + 1)

        if retry_delay is None:
            retry_delay = _default_retry_delay

        if not self._experiment._llmobs_instance or not self._experiment._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before re-running evaluators."
            )
        if not self._experiment._id:
            raise ValueError(
                "Experiment has no ID. Run the experiment via `run()` or call `pull()` first "
                "before calling `rerun_evaluators()`."
            )

        # Build a fallback record lookup from prior rows when no dataset is loaded
        # (e.g. after pull() without a dataset). Keys are row idx values.
        fallback_records: dict[int, DatasetRecord] = {}
        if self._experiment._dataset is None:
            all_rows = (rows_to_eval or []) + (rows_to_retry or [])
            for row in all_rows:
                fallback_records[row["idx"]] = {
                    "record_id": row.get("record_id"),
                    "canonical_id": None,
                    "input_data": cast(dict, row.get("input") or {}),
                    "expected_output": row.get("expected_output"),
                    "metadata": cast(dict, row.get("metadata") or {}),
                    "tags": [],
                }

        def _get_record(idx: int) -> DatasetRecord:
            if self._experiment._dataset is not None:
                return self._experiment._dataset[idx]
            return fallback_records[idx]

        # Compute remote evaluator names from the provided evaluators so that
        # _generate_metrics_for_record() correctly marks managed (LLM-as-judge) evals.
        rerun_remote_names: set[str] = {
            e.name  # type: ignore[union-attr]
            for e in evaluators
            if hasattr(e, "_is_remote_evaluator") and e._is_remote_evaluator
        }

        async def _run() -> "SyncExperiment":
            run = _ExperimentRunInfo(0)

            # Step 1 — create a new experiment entity with parent_experiment_id so the
            # lineage is traversable and the re-run accumulates into a clean aggregate.
            new_experiment_id, new_name = self._experiment._create_rerun_experiment(evaluators)

            # Temporarily update _id and _tags on the shared Experiment so that
            # _process_record (for retried rows), _generate_metric_from_evaluation,
            # and _run_summary_evaluators all stamp the new experiment ID.
            # Both are restored unconditionally in the finally block below so the
            # original experiment is left untouched regardless of success or failure.
            original_id = self._experiment._id
            original_tags = dict(self._experiment._tags)
            self._experiment._id = new_experiment_id
            self._experiment._tags["experiment_id"] = new_experiment_id

            try:
                # Step 2 — build span copies: new UUIDs, shared trace_id, offset timestamps.
                # span_name and duration are read from each row (stored during run() or pull()).
                span_copies, id_map, new_trace_id = _build_span_copies(
                    rows_to_eval,
                    new_experiment_id,
                    convert_tags_dict_to_list(self._experiment._tags),
                )

                # Step 3 — reconstruct task results pointing at the new span IDs so that
                # eval metrics emitted later reference the freshly created span copies.
                task_results = _task_results_from_previous(rows_to_eval, id_map, new_trace_id)

                # Step 4 — re-execute failed rows (creates new spans via the normal task path).
                if rows_to_retry:
                    if self._experiment._dataset is None:
                        raise ValueError(
                            "Cannot retry failed rows without a dataset. "
                            "Provide a dataset when creating the experiment or use "
                            "missing_task_strategy='skip'."
                        )
                    semaphore = asyncio.Semaphore(jobs)
                    retry_coros = [
                        self._experiment._process_record(
                            (row["idx"], self._experiment._dataset[row["idx"]]),
                            run,
                            semaphore,
                            max_retries=max_retries,
                            retry_delay=retry_delay,
                        )
                        for row in rows_to_retry
                    ]
                    retried = list(await asyncio.gather(*retry_coros, return_exceptions=True))
                    for r in retried:
                        if isinstance(r, BaseException):
                            if raise_errors:
                                raise r
                            continue
                        if r is not None:
                            task_results.append(r)
                            self._experiment._check_task_result_error(r, raise_errors)
                    task_results.sort(key=lambda t: t["idx"])

                # Step 5 — run only the provided evaluators, not the experiment's own list.
                semaphore = asyncio.Semaphore(jobs)
                eval_coros = [
                    self._experiment._evaluate_record(
                        _get_record(task_result["idx"]),
                        task_result,
                        semaphore,
                        raise_errors,
                        max_retries,
                        retry_delay,
                        _override_evaluators=evaluators,
                    )
                    for task_result in task_results
                ]
                evaluations = list(await asyncio.gather(*eval_coros))

                # Step 6 — post span copies and eval metrics to the NEW experiment.
                eval_post_failed = False
                if self._experiment._llmobs_instance:
                    pending_metrics: list["LLMObsExperimentEvalMetricEvent"] = []
                    original_remote_names = self._experiment._remote_evaluator_names
                    self._experiment._remote_evaluator_names = rerun_remote_names
                    try:
                        for task_result, evaluation in zip(task_results, evaluations):
                            if evaluation:
                                pending_metrics.extend(
                                    self._experiment._generate_metrics_for_record(task_result, evaluation)
                                )
                    finally:
                        self._experiment._remote_evaluator_names = original_remote_names

                    if pending_metrics:
                        try:
                            self._experiment._llmobs_instance._dne_client.experiment_eval_post(
                                new_experiment_id,
                                pending_metrics,
                                convert_tags_dict_to_list(self._experiment._tags),
                                spans=span_copies if span_copies else None,
                            )
                        except Exception:
                            eval_post_failed = True
                            logger.debug("Failed to post eval metrics for re-run", exc_info=True)
                            self._experiment._update_status("failed")
                        else:
                            self._experiment._llmobs_instance.flush()

                # Step 7 — run summary evaluators (provided ones or experiment's own set).
                original_summary_evs = self._experiment._summary_evaluators
                if summary_evaluators is not None:
                    self._experiment._summary_evaluators = summary_evaluators
                try:
                    summary_evals = await self._experiment._run_summary_evaluators(
                        task_results, evaluations, raise_errors, jobs=jobs
                    )
                finally:
                    self._experiment._summary_evaluators = original_summary_evs

                # Step 8 — build the ExperimentRun result rows.
                experiment_rows: list[ExperimentRowResult] = []
                for task_result, evaluation in zip(task_results, evaluations):
                    idx = task_result["idx"]
                    record = _get_record(idx)
                    metadata: dict[str, JSONType] = {
                        "tags": cast(list[JSONType], convert_tags_dict_to_list(self._experiment._tags))
                    }
                    metadata.update(task_result.get("metadata") or {})
                    exp_row: ExperimentRowResult = {
                        "idx": idx,
                        "span_id": task_result.get("span_id", ""),
                        "trace_id": task_result.get("trace_id", ""),
                        "timestamp": task_result.get("timestamp", 0),
                        "duration": task_result.get("duration", 0),
                        "span_name": task_result.get("span_name", "experiment_record"),
                        "record_id": record.get("record_id", ""),
                        "input": record["input_data"],
                        "expected_output": record["expected_output"],
                        "output": task_result["output"],
                        "evaluations": evaluation["evaluations"] if evaluation else {},
                        "metadata": metadata,
                        "error": task_result["error"],
                    }
                    experiment_rows.append(exp_row)

                summary_eval_dict: dict[str, dict[str, JSONType]] = {}
                if summary_evals:
                    for se in summary_evals:
                        for name, data in se["evaluations"].items():
                            summary_eval_dict[name] = data

                run_obj = ExperimentRun(run, summary_eval_dict, experiment_rows)
                result = Experiment._build_result([run_obj])
                self._experiment._log_experiment_summary(result)
                logger.info("Rerun experiment URL: %s", self._experiment.url, extra={"product": "llmobs"})
                if not eval_post_failed:
                    self._experiment._update_status("completed")
                return SyncExperiment._for_rerun(self, new_experiment_id, new_name, evaluators, result)
            finally:
                # Restore original identity so this experiment object is unchanged.
                self._experiment._id = original_id
                self._experiment._tags = original_tags

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        else:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _run()).result()

    @property
    def url(self) -> str:
        return self._experiment.url


def _get_base_url() -> str:
    subdomain = ""
    if config._dd_site in DD_SITES_NEEDING_APP_SUBDOMAIN:
        subdomain = "app."
    elif config._dd_site == DD_SITE_STAGING:
        subdomain = "dd."

    return f"https://{subdomain}{config._dd_site}"
