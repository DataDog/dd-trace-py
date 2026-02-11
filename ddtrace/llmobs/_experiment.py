from abc import ABC
from abc import abstractmethod
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
import re
import sys
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import TypedDict
from typing import Union
from typing import overload
import uuid

from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import DD_SITES_NEEDING_APP_SUBDOMAIN
from ddtrace.llmobs._constants import EXPERIMENT_EXPECTED_OUTPUT
from ddtrace.llmobs._constants import EXPERIMENT_RECORD_METADATA
from ddtrace.llmobs._utils import convert_tags_dict_to_list
from ddtrace.llmobs._utils import safe_json
from ddtrace.version import __version__


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentEvalMetricEvent
    from ddtrace.llmobs._writer import LLMObsExperimentsClient


logger = get_logger(__name__)

JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]
ConfigType = Dict[str, JSONType]
DatasetRecordInputType = Dict[str, NonNoneJSONType]

# Task type alias for experiment task functions
TaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], JSONType]

# Evaluator type aliases - forward references resolved after class definitions
EvaluatorType = Union[
    Callable[[DatasetRecordInputType, JSONType, JSONType], Union[JSONType, "EvaluatorResult"]],
    "BaseEvaluator",
]

SummaryEvaluatorType = Union[
    Callable[
        [
            list[DatasetRecordInputType],
            list[JSONType],
            list[JSONType],
            dict[str, list[JSONType]],
        ],
        JSONType,
    ],
    "BaseSummaryEvaluator",
]

# Async task type
AsyncTaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], Awaitable[JSONType]]

# Async-only evaluator types
AsyncEvaluatorType = Union[
    Callable[[DatasetRecordInputType, JSONType, JSONType], Awaitable[Union[JSONType, "EvaluatorResult"]]],
    "BaseAsyncEvaluator",
]

AsyncSummaryEvaluatorType = Union[
    Callable[..., Awaitable[JSONType]],
    "BaseAsyncSummaryEvaluator",
]


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
        metadata: Optional[Dict[str, JSONType]] = None,
        tags: Optional[Dict[str, JSONType]] = None,
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
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError(
            f"Evaluator name '{name}' is invalid. Name must contain only alphanumeric characters and underscores."
        )


@dataclass(frozen=True)
class EvaluatorContext:
    """Context object containing all data needed for evaluation.

    This frozen dataclass wraps all metadata needed to run an evaluation,
    providing better state management and extensibility compared to individual parameters.

    :param input_data: The input data that was provided to the task (read-only).
                       Dictionary with string keys mapping to JSON-serializable values.
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

    input_data: Dict[str, Any]
    output_data: Any
    expected_output: Optional[JSONType] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    span_id: Optional[str] = None
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class SummaryEvaluatorContext:
    """Context object containing all data needed for summary evaluation.

    :param inputs: List of all input data from the dataset records (read-only).
    :param outputs: List of all outputs produced by the task (read-only).
    :param expected_outputs: List of all expected outputs (read-only).
    :param evaluation_results: Dictionary mapping evaluator names to their results (read-only).
    :param metadata: List of metadata for each dataset record, each combined with experiment configuration (read-only).
                     Each element contains the record's metadata merged with {"experiment_config": ...}.
    """

    inputs: List[DatasetRecordInputType]
    outputs: List[JSONType]
    expected_outputs: List[JSONType]
    evaluation_results: Dict[str, List[JSONType]]
    metadata: List[Dict[str, Any]] = field(default_factory=list)


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


class BaseAsyncEvaluator(ABC):
    """Base class for async evaluators.

    Subclasses must implement the async `evaluate` method.

    Example::

        class AsyncSemanticSimilarityEvaluator(BaseAsyncEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="async_semantic_similarity")
                self.threshold = threshold

            async def evaluate(self, context: EvaluatorContext):
                score = await self.model.compare_async(context.output_data, context.expected_output)
                return score
    """

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
        """Perform async evaluation.

        This method must be implemented by all subclasses.

        :param context: The evaluation context containing input, output, and metadata
        :return: Evaluation results - can be a JSONType value or an EvaluatorResult object
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")


class BaseAsyncSummaryEvaluator(ABC):
    """Base class for async summary evaluators.

    Subclasses must implement the async `evaluate` method.

    Example::

        class AsyncAverageScoreEvaluator(BaseAsyncSummaryEvaluator):
            def __init__(self, target_evaluator: str):
                super().__init__(name="async_average_score")
                self.target_evaluator = target_evaluator

            async def evaluate(self, context: SummaryEvaluatorContext):
                scores = context.evaluation_results.get(self.target_evaluator, [])
                if not scores:
                    return None
                return sum(scores) / len(scores)
    """

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
        """Perform async summary evaluation.

        This method must be implemented by all subclasses.

        :param context: The summary evaluation context
        :return: Evaluation result as a JSON-serializable value
        """
        raise NotImplementedError("Subclasses must implement the evaluate method")


def _is_class_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a class-based evaluator (inherits from BaseEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's a class-based evaluator, False otherwise
    """
    return isinstance(evaluator, BaseEvaluator)


def _is_class_summary_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a class-based summary evaluator (inherits from BaseSummaryEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's a class-based summary evaluator, False otherwise
    """
    return isinstance(evaluator, BaseSummaryEvaluator)


def _is_async_class_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is an async class-based evaluator."""
    return isinstance(evaluator, BaseAsyncEvaluator)


def _is_async_class_summary_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is an async class-based summary evaluator."""
    return isinstance(evaluator, BaseAsyncSummaryEvaluator)


def _is_function_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a function-based evaluator.

    :param evaluator: The evaluator to check
    :return: True if it's a function evaluator, False otherwise
    """
    return not isinstance(evaluator, BaseEvaluator) and not isinstance(evaluator, BaseSummaryEvaluator)


def _get_evaluator_name(evaluator: Any) -> str:
    """Get the name of an evaluator."""
    if _is_async_class_evaluator(evaluator) or _is_class_evaluator(evaluator):
        return evaluator.name
    return evaluator.__name__


def _get_summary_evaluator_name(evaluator: Any) -> str:
    """Get the name of a summary evaluator."""
    if _is_async_class_summary_evaluator(evaluator) or _is_class_summary_evaluator(evaluator):
        return evaluator.name
    return evaluator.__name__


def _get_or_create_project(dne_client: "LLMObsExperimentsClient", project_name: str) -> str:
    """Get or create a project and return the project_id.

    :param dne_client: The LLMObs experiments client
    :param project_name: The name of the project
    :return: The project ID
    :raises ValueError: If no project ID is returned
    """
    project = dne_client.project_create_or_get(project_name)
    project_id = project.get("_id", "")
    if not project_id:
        raise ValueError("Failed to get or create project: no project ID returned")
    return project_id


def _create_experiment(
    dne_client: "LLMObsExperimentsClient",
    name: str,
    dataset_id: str,
    project_id: str,
    dataset_version: int,
    config: ConfigType,
    tags: list[str],
    description: str,
    runs: int,
) -> tuple[str, str]:
    """Create an experiment and return (experiment_id, run_name).

    :param dne_client: The LLMObs experiments client
    :param name: The experiment name
    :param dataset_id: The dataset ID
    :param project_id: The project ID
    :param dataset_version: The dataset version
    :param config: The experiment configuration
    :param tags: The experiment tags
    :param description: The experiment description
    :param runs: The number of runs
    :return: Tuple of (experiment_id, run_name)
    :raises ValueError: If no experiment ID is returned
    """
    experiment_id, run_name = dne_client.experiment_create(
        name, dataset_id, project_id, dataset_version, config, tags, description, runs
    )
    if not experiment_id:
        raise ValueError("Failed to create experiment: no experiment ID returned")
    return experiment_id, run_name


def _format_error(e: Exception) -> dict[str, JSONType]:
    """Format an exception into an error dictionary.

    :param e: The exception to format
    :return: Dictionary with message, type, and stack keys
    """
    exc_type, exc_value, exc_tb = sys.exc_info()
    exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
    exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    return {
        "message": str(exc_value),
        "type": exc_type_name,
        "stack": exc_stack,
    }


def _extract_evaluator_result(
    eval_result: Union[JSONType, "EvaluatorResult"],
) -> tuple[JSONType, dict[str, JSONType]]:
    """Extract value and extra fields from an evaluator result.

    :param eval_result: The raw evaluator result (JSONType or EvaluatorResult)
    :return: Tuple of (result_value, extra_return_values dict)
    """
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
        return eval_result.value, extra_return_values
    return eval_result, extra_return_values


def _infer_metric_type(value: JSONType) -> tuple[str, JSONType]:
    """Infer the metric type from a value.

    :param value: The evaluation value
    :return: Tuple of (metric_type, typed_value)
    """
    if value is None:
        return "categorical", value
    elif isinstance(value, bool):
        return "boolean", value
    elif isinstance(value, (int, float)):
        return "score", value
    elif isinstance(value, dict):
        return "json", value
    else:
        return "categorical", str(value).lower()


class Project(TypedDict):
    name: str
    _id: str


class DatasetRecordRaw(TypedDict):
    input_data: DatasetRecordInputType
    expected_output: JSONType
    metadata: Dict[str, Any]


class _UpdatableDatasetRecordOptional(TypedDict, total=False):
    input_data: DatasetRecordInputType
    expected_output: JSONType
    metadata: Dict[str, Any]


class UpdatableDatasetRecord(_UpdatableDatasetRecordOptional):
    record_id: str


class DatasetRecord(DatasetRecordRaw):
    record_id: str


class TaskResult(TypedDict):
    idx: int
    span_id: str
    trace_id: str
    timestamp: int
    output: JSONType
    metadata: Dict[str, JSONType]
    error: Dict[str, Optional[str]]


class EvaluationResult(TypedDict):
    idx: int
    evaluations: Dict[str, Dict[str, JSONType]]


class _ExperimentRunInfo:
    def __init__(self, run_interation: int):
        self._id = uuid.uuid4()
        # always increment the representation of iteration by 1 for readability
        self._run_iteration = run_interation + 1


class ExperimentRowResult(TypedDict):
    idx: int
    record_id: Optional[str]
    span_id: str
    trace_id: str
    timestamp: int
    input: Dict[str, NonNoneJSONType]
    output: JSONType
    expected_output: JSONType
    evaluations: Dict[str, Dict[str, JSONType]]
    metadata: Dict[str, JSONType]
    error: Dict[str, Optional[str]]


class ExperimentRun:
    def __init__(
        self,
        run: _ExperimentRunInfo,
        summary_evaluations: Dict[str, Dict[str, JSONType]],
        rows: List[ExperimentRowResult],
    ):
        self.run_id = run._id
        self.run_iteration = run._run_iteration
        self.summary_evaluations = summary_evaluations or {}
        self.rows = rows or []


class ExperimentResult(TypedDict):
    # TODO: remove these fields (summary_evaluations, rows) in the next major release (5.x)
    summary_evaluations: Dict[str, Dict[str, JSONType]]
    rows: List[ExperimentRowResult]
    runs: List[ExperimentRun]


class Dataset:
    name: str
    description: str
    _id: str
    _records: List[DatasetRecord]
    _version: int
    _latest_version: int
    _dne_client: "LLMObsExperimentsClient"
    _new_records_by_record_id: Dict[str, DatasetRecordRaw]
    _updated_record_ids_to_new_fields: Dict[str, UpdatableDatasetRecord]
    _deleted_record_ids: List[str]

    BATCH_UPDATE_THRESHOLD = 5 * 1024 * 1024  # 5MB

    def __init__(
        self,
        name: str,
        project: Project,
        dataset_id: str,
        records: List[DatasetRecord],
        description: str,
        latest_version: int,
        version: int,
        _dne_client: "LLMObsExperimentsClient",
    ) -> None:
        self.name = name
        self.project = project
        self.description = description
        self._id = dataset_id
        self._latest_version = latest_version
        self._version = version
        self._dne_client = _dne_client
        self._records = records
        self._new_records_by_record_id = {}
        self._updated_record_ids_to_new_fields = {}
        self._deleted_record_ids = []

    def push(self) -> None:
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

        delta_size = self._estimate_delta_size()
        if delta_size > self.BATCH_UPDATE_THRESHOLD:
            logger.debug("dataset delta is %d, using bulk upload", delta_size)
            # TODO must return version too
            self._dne_client.dataset_bulk_upload(self._id, self._records)
        else:
            logger.debug("dataset delta is %d, using batch update", delta_size)
            updated_records = list(self._updated_record_ids_to_new_fields.values())
            new_version, new_record_ids = self._dne_client.dataset_batch_update(
                self._id,
                list(self._new_records_by_record_id.values()),
                updated_records,
                self._deleted_record_ids,
            )

            # attach record ids to newly created records
            for record, record_id in zip(self._new_records_by_record_id.values(), new_record_ids):
                record["record_id"] = record_id  # type: ignore

            # FIXME: we don't get version numbers in responses to deletion requests
            self._latest_version = new_version if new_version != -1 else self._latest_version + 1
            # no matter what the version was before the push, pushing will result in the dataset being on the current
            # version tracked by the backend
            self._version = self._latest_version
        self._new_records_by_record_id = {}
        self._deleted_record_ids = []
        self._updated_record_ids_to_new_fields = {}

    def update(self, index: int, record: DatasetRecordRaw) -> None:
        if all(k not in record for k in ("input_data", "expected_output", "metadata")):
            raise ValueError(
                "invalid update, record should contain at least one of "
                "input_data, expected_output, or metadata to update"
            )
        record_id = self._records[index]["record_id"]
        self._updated_record_ids_to_new_fields[record_id] = {
            **self._updated_record_ids_to_new_fields.get(record_id, {"record_id": record_id}),
            **record,
            "record_id": record_id,
        }
        self._records[index] = {
            **self._records[index],
            **record,
            "record_id": record_id,
        }

    def append(self, record: DatasetRecordRaw) -> None:
        record_id: str = uuid.uuid4().hex
        # this record ID will be discarded after push, BE will generate a new one, this is just
        # for tracking new records locally before the push
        r: DatasetRecord = {**record, "record_id": record_id}
        # keep the same reference in both lists to enable us to update the record_id after push
        self._new_records_by_record_id[record_id] = r
        self._records.append(r)

    def extend(self, records: List[DatasetRecordRaw]) -> None:
        for record in records:
            self.append(record)

    def delete(self, index: int) -> None:
        record_id = self._records[index]["record_id"]
        should_append_to_be_deleted = True

        del self._records[index]

        if record_id is None or record_id == "":
            logger.warning("encountered unexpected record_id on deletion %s", record_id)
            return

        if record_id in self._updated_record_ids_to_new_fields:
            del self._updated_record_ids_to_new_fields[record_id]

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
        size = len(safe_json(self._new_records_by_record_id)) + len(safe_json(self._updated_record_ids_to_new_fields))
        logger.debug("estimated delta size %d", size)
        return size

    @overload
    def __getitem__(self, index: int) -> DatasetRecord: ...

    @overload
    def __getitem__(self, index: slice) -> List[DatasetRecord]: ...

    def __getitem__(self, index: Union[int, slice]) -> Union[DatasetRecord, List[DatasetRecord]]:
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
            flat_record = {}  # type: Dict[Union[str, Tuple[str, str]], Any]

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

            data_rows.append(flat_record)

        records_list = []
        for flat_record in data_rows:
            row = [flat_record.get(col, None) for col in column_tuples]
            records_list.append(row)

        return pd.DataFrame(data=records_list, columns=pd.MultiIndex.from_tuples(column_tuples))


def _create_dataset_subset(dataset: Dataset, sample_size: int) -> Dataset:
    """Create a subset of the dataset for testing purposes.

    :param dataset: The original dataset
    :param sample_size: The number of records to include in the subset
    :return: A new Dataset containing only the first sample_size records
    """
    subset_records = [deepcopy(record) for record in dataset._records[:sample_size]]
    subset_name = "[Test subset of {} records] {}".format(sample_size, dataset.name)
    return Dataset(
        name=subset_name,
        project=dataset.project,
        dataset_id=dataset._id,
        records=subset_records,
        description=dataset.description,
        latest_version=dataset._latest_version,
        version=dataset._version,
        _dne_client=dataset._dne_client,
    )


def _get_base_url() -> str:
    subdomain = ""
    if config._dd_site in DD_SITES_NEEDING_APP_SUBDOMAIN:
        subdomain = "app."

    return f"https://{subdomain}{config._dd_site}"


class Experiment:
    """Experiment for running tasks and evaluators.

    Supports both sync and async tasks, and sync and async evaluators.
    Use run() for synchronous execution or arun() for async execution.
    """

    def __init__(
        self,
        name: str,
        task: Union[TaskType, AsyncTaskType],
        dataset: Dataset,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]],
        project_name: str,
        _llmobs_instance: "LLMObs",
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        summary_evaluators: Optional[Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators or []
        self._description = description
        self._tags: Dict[str, str] = tags or {}
        self._tags["ddtrace.version"] = str(__version__)
        self._tags["project_name"] = project_name
        self._tags["dataset_name"] = dataset.name
        self._tags["experiment_name"] = name
        self._config: Dict[str, JSONType] = config or {}
        self._runs: int = runs or 1
        self._llmobs_instance = _llmobs_instance

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        # Below values are set at experiment run time via API calls
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None
        self._run_name: Optional[str] = None

    def run(
        self,
        jobs: int = 1,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> ExperimentResult:
        """Run the experiment synchronously.

        :param jobs: Maximum number of concurrent operations (tasks + evaluators)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors
        :param sample_size: Optionally, use only the first sample_size records in the dataset
        :return: ExperimentResult containing evaluation results and metadata
        :raises RuntimeError: If called from an async context (use arun() instead)
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - this is the expected path for sync run()
            pass
        else:
            # get_running_loop() succeeded = there IS a running loop
            raise RuntimeError(
                "Cannot use run() from an async context (e.g., Jupyter notebook, async function). "
                "Use 'await exp.arun()' instead."
            )
        return asyncio.run(self.arun(jobs=jobs, raise_errors=raise_errors, sample_size=sample_size))

    async def arun(
        self,
        jobs: int = 1,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> ExperimentResult:
        """Run the experiment asynchronously.

        :param jobs: Maximum number of concurrent operations (tasks + evaluators)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors
        :param sample_size: Optionally, use only the first sample_size records in the dataset
        :return: ExperimentResult containing evaluation results and metadata
        """
        if jobs < 1:
            raise ValueError("jobs must be at least 1")

        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )

        self._project_id = _get_or_create_project(self._llmobs_instance._dne_client, self._project_name)
        self._tags["project_id"] = self._project_id

        experiment_id, run_name = _create_experiment(
            self._llmobs_instance._dne_client,
            self.name,
            self._dataset._id,
            self._project_id,
            self._dataset._version,
            self._config,
            convert_tags_dict_to_list(self._tags),
            self._description,
            self._runs,
        )
        self._id = experiment_id
        self._tags["experiment_id"] = str(experiment_id)
        self._run_name = run_name

        # Resolve dataset (handle sample_size)
        if sample_size is not None and sample_size < len(self._dataset):
            subset_dataset = _create_dataset_subset(self._dataset, sample_size)
        else:
            subset_dataset = self._dataset

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(jobs)

        # Launch all iterations concurrently
        all_runs = list(
            await asyncio.gather(
                *[self._run_iteration(i, subset_dataset, semaphore, raise_errors) for i in range(self._runs)]
            )
        )

        # Flush spans in case of serverless
        self._llmobs_instance.flush()

        return ExperimentResult(
            summary_evaluations=all_runs[0].summary_evaluations if all_runs else {},
            rows=all_runs[0].rows if all_runs else [],
            runs=all_runs,
        )

    async def _run_iteration(
        self,
        iteration: int,
        dataset: Dataset,
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> ExperimentRun:
        """Run a single iteration of the experiment.

        :param iteration: The iteration index (0-based)
        :param dataset: The dataset to process
        :param semaphore: Semaphore for concurrency control
        :param raise_errors: Whether to raise exceptions on errors
        :return: ExperimentRun containing the results for this iteration
        """
        run_info = _ExperimentRunInfo(iteration)
        iteration_tags = {
            **self._tags,
            "run_id": str(run_info._id),
            "run_iteration": str(run_info._run_iteration),
        }

        coros = [
            self._process_record(record, idx, run_info, iteration_tags, semaphore, raise_errors)
            for idx, record in enumerate(dataset)
        ]
        results = await asyncio.gather(*coros, return_exceptions=not raise_errors)

        row_results: List[ExperimentRowResult] = []
        all_metrics: List["LLMObsExperimentEvalMetricEvent"] = []
        for result in results:
            if isinstance(result, BaseException):
                if raise_errors:
                    raise result
                continue
            row_result, metrics = result
            row_results.append(row_result)
            all_metrics.extend(metrics)

        # Run summary evaluators
        summary_evals, summary_metrics = await self._run_summary_evaluators(
            row_results, iteration_tags, semaphore, raise_errors
        )
        all_metrics.extend(summary_metrics)

        # Submit metrics for this iteration
        if self._id is None:
            raise ValueError("Experiment ID is not set")
        self._llmobs_instance._dne_client.experiment_eval_post(
            self._id, all_metrics, convert_tags_dict_to_list(iteration_tags)
        )

        return ExperimentRun(run_info, summary_evals, row_results)

    async def _process_record(
        self,
        record: DatasetRecord,
        idx: int,
        run: _ExperimentRunInfo,
        iteration_tags: Dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> Tuple[ExperimentRowResult, List["LLMObsExperimentEvalMetricEvent"]]:
        """Process a single record: run task, run evaluators, build metrics."""
        metrics: List["LLMObsExperimentEvalMetricEvent"] = []

        # Run task (with semaphore)
        task_output, span_id, trace_id, timestamp, task_error = await self._run_task_for_record(
            record, idx, run, iteration_tags, semaphore, raise_errors
        )

        # Run evaluators (each with semaphore)
        evaluations: Dict[str, Dict[str, JSONType]] = {}
        for evaluator in self._evaluators:
            eval_result = await self._run_evaluator(
                evaluator, record, task_output, span_id, trace_id, semaphore, raise_errors
            )

            evaluator_name = _get_evaluator_name(evaluator)
            evaluations[evaluator_name] = eval_result

            # Build metric event immediately
            metric = self._build_metric_event(
                evaluator_name=evaluator_name,
                eval_result=eval_result,
                span_id=span_id,
                trace_id=trace_id,
                timestamp=timestamp,
                metric_source="custom",
                iteration_tags=iteration_tags,
            )
            metrics.append(metric)

        # Build row result
        row_result: ExperimentRowResult = {
            "idx": idx,
            "record_id": record.get("record_id"),
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp": timestamp,
            "input": record["input_data"],
            "output": task_output,
            "expected_output": record["expected_output"],
            "evaluations": evaluations,
            "metadata": self._build_row_metadata(record, idx, iteration_tags),
            "error": task_error,
        }

        return row_result, metrics

    async def _run_task_for_record(
        self,
        record: DatasetRecord,
        idx: int,
        run: _ExperimentRunInfo,
        iteration_tags: Dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> Tuple[JSONType, str, str, int, Dict[str, Optional[str]]]:
        """Run the task for a single record."""
        async with semaphore:
            if not self._llmobs_instance or not self._llmobs_instance.enabled:
                return None, "", "", 0, {"message": None, "stack": None, "type": None}

            if self._id is None:
                raise ValueError("Experiment ID is not set")
            if self._project_id is None:
                raise ValueError("Project ID is not set")

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
                if span_context:
                    span_id = span_context.get("span_id", "")
                    trace_id = span_context.get("trace_id", "")
                else:
                    span_id, trace_id = "", ""

                input_data = record["input_data"]
                record_id = record.get("record_id", "")
                tags = {
                    **iteration_tags,
                    "dataset_id": str(self._dataset._id),
                    "dataset_record_id": str(record_id),
                    "experiment_id": str(self._id),
                }
                output_data = None
                try:
                    if asyncio.iscoroutinefunction(self._task):
                        output_data = await self._task(input_data, self._config)
                    else:
                        output_data = await asyncio.to_thread(self._task, input_data, self._config)
                except Exception:
                    span.set_exc_info(*sys.exc_info())
                    if raise_errors:
                        exc_type, exc_value, exc_tb = sys.exc_info()
                        raise RuntimeError(f"Error on record {idx}: {exc_value}\n{exc_type}\n{exc_tb}") from exc_value

                self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

                span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
                if "metadata" in record:
                    span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

                error_dict: Dict[str, Optional[str]] = {
                    "message": span.get_tag(ERROR_MSG),
                    "stack": span.get_tag(ERROR_STACK),
                    "type": span.get_tag(ERROR_TYPE),
                }

                return output_data, span_id, trace_id, span.start_ns, error_dict

    async def _run_evaluator(
        self,
        evaluator: Union[EvaluatorType, AsyncEvaluatorType],
        record: DatasetRecord,
        output: JSONType,
        span_id: str,
        trace_id: str,
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> Dict[str, JSONType]:
        """Run a single evaluator, handling sync/async transparently."""
        async with semaphore:
            input_data = record["input_data"]
            expected_output = record["expected_output"]
            metadata = record.get("metadata", {})
            combined_metadata = {**metadata, "experiment_config": self._config}

            eval_result_value: JSONType = None
            eval_err: JSONType = None
            extra_return_values: Dict[str, JSONType] = {}

            try:
                if _is_async_class_evaluator(evaluator):
                    # Async class-based evaluator
                    context = EvaluatorContext(
                        input_data=input_data,
                        output_data=output,
                        expected_output=expected_output,
                        metadata=combined_metadata,
                        span_id=span_id,
                        trace_id=trace_id,
                    )
                    eval_result = await evaluator.evaluate(context)  # type: ignore[union-attr, misc]
                elif _is_class_evaluator(evaluator):
                    # Sync class-based evaluator - run in thread
                    context = EvaluatorContext(
                        input_data=input_data,
                        output_data=output,
                        expected_output=expected_output,
                        metadata=combined_metadata,
                        span_id=span_id,
                        trace_id=trace_id,
                    )
                    eval_result = await asyncio.to_thread(evaluator.evaluate, context)  # type: ignore[union-attr]
                elif asyncio.iscoroutinefunction(evaluator):
                    # Async function evaluator
                    eval_result = await evaluator(input_data, output, expected_output)
                else:
                    # Sync function evaluator - run in thread
                    eval_result = await asyncio.to_thread(evaluator, input_data, output, expected_output)  # noqa: E501  # type: ignore[arg-type]

                # Extract EvaluatorResult if applicable
                eval_result_value, extra_return_values = _extract_evaluator_result(eval_result)

            except Exception as e:
                eval_err = _format_error(e)
                if raise_errors:
                    evaluator_name = _get_evaluator_name(evaluator)
                    raise RuntimeError(f"Evaluator {evaluator_name} failed") from e

            return {
                "value": eval_result_value,
                "error": eval_err,
                **extra_return_values,
            }

    async def _run_summary_evaluators(
        self,
        row_results: List[ExperimentRowResult],
        iteration_tags: Dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> Tuple[Dict[str, Dict[str, JSONType]], List["LLMObsExperimentEvalMetricEvent"]]:
        """Run summary evaluators on aggregated results."""
        if not self._summary_evaluators:
            return {}, []

        # Build aggregated data
        inputs: List[DatasetRecordInputType] = []
        outputs: List[JSONType] = []
        expected_outputs: List[JSONType] = []
        metadata_list: List[Dict[str, Any]] = []
        eval_results_by_name: Dict[str, List[JSONType]] = {}

        latest_timestamp: int = 0
        for row in row_results:
            inputs.append(row["input"])
            outputs.append(row["output"])
            expected_outputs.append(row["expected_output"])
            record_metadata = self._dataset[row["idx"]].get("metadata", {})
            metadata_list.append({**record_metadata, "experiment_config": self._config})

            if row["timestamp"] > latest_timestamp:
                latest_timestamp = row["timestamp"]

            for eval_name, eval_data in row["evaluations"].items():
                if eval_name not in eval_results_by_name:
                    eval_results_by_name[eval_name] = []
                eval_results_by_name[eval_name].append(eval_data.get("value"))

        # Run each summary evaluator
        summary_evals: Dict[str, Dict[str, JSONType]] = {}
        metrics: List["LLMObsExperimentEvalMetricEvent"] = []

        async def run_single_summary_evaluator(
            summary_evaluator: Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType],
        ) -> Tuple[str, Dict[str, JSONType]]:
            async with semaphore:
                eval_result_value: JSONType = None
                eval_err: JSONType = None
                evaluator_name = _get_summary_evaluator_name(summary_evaluator)

                try:
                    if _is_async_class_summary_evaluator(summary_evaluator):
                        # Async class-based summary evaluator
                        context = SummaryEvaluatorContext(
                            inputs=inputs,
                            outputs=outputs,
                            expected_outputs=expected_outputs,
                            evaluation_results=eval_results_by_name,
                            metadata=metadata_list,
                        )
                        eval_result_value = await summary_evaluator.evaluate(context)  # type: ignore[union-attr, misc]
                    elif _is_class_summary_evaluator(summary_evaluator):
                        # Sync class-based summary evaluator - run in thread
                        context = SummaryEvaluatorContext(
                            inputs=inputs,
                            outputs=outputs,
                            expected_outputs=expected_outputs,
                            evaluation_results=eval_results_by_name,
                            metadata=metadata_list,
                        )
                        eval_result_value = await asyncio.to_thread(summary_evaluator.evaluate, context)  # noqa: E501  # type: ignore[union-attr, arg-type]
                    elif asyncio.iscoroutinefunction(summary_evaluator):
                        # Async function summary evaluator
                        eval_result_value = await summary_evaluator(
                            inputs, outputs, expected_outputs, eval_results_by_name
                        )
                    else:
                        # Sync function summary evaluator - run in thread
                        eval_result_value = await asyncio.to_thread(
                            summary_evaluator,  # type: ignore[arg-type]
                            inputs,
                            outputs,
                            expected_outputs,
                            eval_results_by_name,
                        )

                except Exception as e:
                    eval_err = _format_error(e)
                    if raise_errors:
                        raise RuntimeError(f"Summary evaluator {evaluator_name} failed") from e

                return evaluator_name, {"value": eval_result_value, "error": eval_err}

        # Run all summary evaluators concurrently
        results = await asyncio.gather(
            *[run_single_summary_evaluator(se) for se in self._summary_evaluators],
            return_exceptions=not raise_errors,
        )

        for result in results:
            if isinstance(result, BaseException):
                if raise_errors:
                    raise result
                continue
            evaluator_name, eval_data = result
            summary_evals[evaluator_name] = eval_data

            # Build metric for summary evaluator
            metric = self._build_metric_event(
                evaluator_name=evaluator_name,
                eval_result=eval_data,
                span_id="",
                trace_id="",
                timestamp=latest_timestamp,
                metric_source="summary",
                iteration_tags=iteration_tags,
            )
            metrics.append(metric)

        return summary_evals, metrics

    def _build_row_metadata(
        self, record: DatasetRecord, idx: int, iteration_tags: Dict[str, str]
    ) -> Dict[str, JSONType]:
        """Build metadata for a row result."""
        tags: List[JSONType] = list(convert_tags_dict_to_list(iteration_tags))
        return {
            "tags": tags,
            "dataset_record_index": idx,
            "experiment_name": self.name,
            "dataset_name": self._dataset.name,
        }

    def _build_metric_event(
        self,
        evaluator_name: str,
        eval_result: Dict[str, JSONType],
        span_id: str,
        trace_id: str,
        timestamp: int,
        metric_source: str,
        iteration_tags: Dict[str, str],
    ) -> "LLMObsExperimentEvalMetricEvent":
        """Build a metric event from an evaluation result."""
        if self._id is None:
            raise ValueError("Experiment ID is not set")

        value = eval_result.get("value")

        # Infer metric type
        metric_type, typed_value = _infer_metric_type(value)

        eval_metric: "LLMObsExperimentEvalMetricEvent" = {
            "experiment_id": self._id,
            "metric_source": metric_source,
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp_ms": int(timestamp / 1e6),
            "metric_type": metric_type,
            "label": evaluator_name,
            f"{metric_type}_value": typed_value,  # type: ignore
            "error": eval_result.get("error"),
            "tags": convert_tags_dict_to_list(iteration_tags),
        }

        # Add optional fields from EvaluatorResult
        if eval_result.get("reasoning"):
            eval_metric["reasoning"] = str(eval_result["reasoning"])
        if eval_result.get("assessment"):
            eval_metric["assessment"] = str(eval_result["assessment"])
        if eval_result.get("metadata"):
            eval_metric["metadata"] = eval_result["metadata"]  # type: ignore

        return eval_metric
