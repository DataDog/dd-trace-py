from abc import ABC
from abc import abstractmethod
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
import itertools
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
from typing import cast
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

TaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], JSONType]
AsyncTaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], Awaitable[JSONType]]
EvaluatorFunctionType = Callable[[DatasetRecordInputType, JSONType, JSONType], Union[JSONType, "EvaluatorResult"]]
AsyncEvaluatorFunctionType = Callable[
    [DatasetRecordInputType, JSONType, JSONType], Awaitable[Union[JSONType, "EvaluatorResult"]]
]
SummaryEvaluatorFunctionType = Callable[
    [List[DatasetRecordInputType], List[JSONType], List[JSONType], Dict[str, List[JSONType]]], JSONType
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


class BaseAsyncEvaluator(ABC):
    """Base class for async evaluators.

    Subclasses must implement the async `evaluate` method.

    Example::

        class AsyncSemanticSimilarityEvaluator(BaseAsyncEvaluator):
            def __init__(self, threshold=0.8):
                super().__init__(name="semantic_similarity")
                self.threshold = threshold

            async def evaluate(self, context: EvaluatorContext):
                score = await self.model.compare(context.output_data, context.expected_output)
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


def _is_function_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is a function-based evaluator.

    :param evaluator: The evaluator to check
    :return: True if it's a function evaluator, False otherwise
    """
    return not isinstance(evaluator, BaseEvaluator) and not isinstance(evaluator, BaseSummaryEvaluator)


def _is_async_class_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is an async class-based evaluator (inherits from BaseAsyncEvaluator).

    :param evaluator: The evaluator to check
    :return: True if it's an async class-based evaluator, False otherwise
    """
    return isinstance(evaluator, BaseAsyncEvaluator)


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


class _ExperimentBase:
    """Base class containing common experiment functionality for sync and async experiments."""

    name: str
    _task: Union[TaskType, AsyncTaskType]
    _dataset: Dataset
    _evaluators: Sequence[Any]
    _summary_evaluators: List[Any]
    _description: str
    _tags: Dict[str, str]
    _config: Dict[str, JSONType]
    _runs: int
    _llmobs_instance: Optional["LLMObs"]
    _project_name: str
    _project_id: Optional[str]
    _id: Optional[str]
    _run_name: Optional[str]

    def _init_common(
        self,
        name: str,
        task: Union[TaskType, AsyncTaskType],
        dataset: Dataset,
        evaluators: Sequence[Any],
        project_name: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[List[Any]] = None,
        runs: Optional[int] = None,
    ) -> None:
        """Initialize common experiment attributes."""
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators or []
        self._description = description
        self._tags = tags or {}
        self._tags["ddtrace.version"] = str(__version__)
        self._tags["project_name"] = project_name
        self._tags["dataset_name"] = dataset.name
        self._tags["experiment_name"] = name
        self._config = config or {}
        self._runs = runs or 1
        self._llmobs_instance = _llmobs_instance

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        self._project_id = None
        self._id = None
        self._run_name = None

    @property
    def url(self) -> str:
        return f"{_get_base_url()}/llm/experiments/{self._id}"

    def _setup_experiment(self) -> None:
        """Set up experiment by creating project and experiment in backend."""
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )

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
        )
        self._id = experiment_id
        self._tags["experiment_id"] = str(experiment_id)
        self._run_name = experiment_run_name

    def _create_subset_dataset(self, sample_size: Optional[int]) -> Dataset:
        """Create a subset dataset if sample_size is specified."""
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
    ) -> Tuple[JSONType, Dict[str, JSONType]]:
        """Extract value and extra metadata from evaluator result."""
        extra_return_values: Dict[str, JSONType] = {}
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

    def _prepare_summary_data(
        self, task_results: List[TaskResult], eval_results: List[EvaluationResult]
    ) -> Tuple[
        List[DatasetRecordInputType],
        List[JSONType],
        List[JSONType],
        List[Dict[str, Any]],
        Dict[str, List[JSONType]],
    ]:
        """Prepare data for summary evaluators."""
        inputs: List[DatasetRecordInputType] = []
        outputs: List[JSONType] = []
        expected_outputs: List[JSONType] = []
        metadata_list: List[Dict[str, Any]] = []
        eval_results_by_name: Dict[str, List[JSONType]] = {}

        for idx, task_result in enumerate(task_results):
            outputs.append(task_result["output"])
            record: DatasetRecord = self._dataset[idx]
            inputs.append(record["input_data"])
            expected_outputs.append(record["expected_output"])
            record_metadata = record.get("metadata", {})
            metadata_list.append({**record_metadata, "experiment_config": self._config})

            eval_result_at_idx_by_name = eval_results[idx]["evaluations"]
            for name, eval_value in eval_result_at_idx_by_name.items():
                if name not in eval_results_by_name:
                    eval_results_by_name[name] = []
                eval_results_by_name[name].append(eval_value.get("value"))

        return inputs, outputs, expected_outputs, metadata_list, eval_results_by_name

    def _build_task_result(
        self,
        idx: int,
        span: Any,
        span_id: str,
        trace_id: str,
        output_data: JSONType,
    ) -> TaskResult:
        """Build a TaskResult dictionary from span and output data."""
        return {
            "idx": idx,
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp": span.start_ns,
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

    def _merge_results(
        self,
        run: _ExperimentRunInfo,
        task_results: List[TaskResult],
        evaluations: List[EvaluationResult],
        summary_evaluations: Optional[List[EvaluationResult]],
    ) -> ExperimentRun:
        """Merge task results and evaluations into an ExperimentRun."""
        experiment_results = []
        for idx, task_result in enumerate(task_results):
            output_data = task_result["output"]
            metadata: Dict[str, JSONType] = {"tags": cast(List[JSONType], convert_tags_dict_to_list(self._tags))}
            metadata.update(task_result.get("metadata") or {})
            record: DatasetRecord = self._dataset[idx]
            evals = evaluations[idx]["evaluations"]
            exp_result: ExperimentRowResult = {
                "idx": idx,
                "span_id": task_result.get("span_id", ""),
                "trace_id": task_result.get("trace_id", ""),
                "timestamp": task_result.get("timestamp", 0),
                "record_id": record.get("record_id", ""),
                "input": record["input_data"],
                "expected_output": record["expected_output"],
                "output": output_data,
                "evaluations": evals,
                "metadata": metadata,
                "error": task_result["error"],
            }
            experiment_results.append(exp_result)

        summary_evals: Dict[str, Dict[str, JSONType]] = {}
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
        source: str = "custom",
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        metadata: Optional[Dict[str, JSONType]] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> "LLMObsExperimentEvalMetricEvent":
        """Generate a metric event from evaluation data."""
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
        eval_metric: LLMObsExperimentEvalMetricEvent = {
            "metric_source": source,
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp_ms": int(timestamp_ns / 1e6),
            "metric_type": metric_type,
            "label": eval_name,
            f"{metric_type}_value": eval_value,  # type: ignore
            "error": err,
            "tags": convert_tags_dict_to_list(tags),
            "experiment_id": self._id,
        }
        if reasoning:
            eval_metric["reasoning"] = reasoning
        if assessment:
            eval_metric["assessment"] = assessment
        if metadata:
            eval_metric["metadata"] = metadata
        return eval_metric

    def _generate_metrics_from_exp_results(
        self, experiment_result: ExperimentRun
    ) -> List["LLMObsExperimentEvalMetricEvent"]:
        """Generate metric events from experiment results."""
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
                    metadata=cast(Dict[str, JSONType], eval_data.get("metadata"))
                    if isinstance(eval_data.get("metadata"), Dict)
                    else None,
                    tags=cast(Dict[str, str], eval_data.get("tags"))
                    if isinstance(eval_data.get("tags"), Dict)
                    else None,
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


class Experiment(_ExperimentBase):
    def __init__(
        self,
        name: str,
        task: TaskType,
        dataset: Dataset,
        evaluators: Sequence[Union[EvaluatorFunctionType, BaseEvaluator]],
        project_name: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[List[Union[SummaryEvaluatorFunctionType, BaseSummaryEvaluator]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        self._init_common(
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

    def run(
        self,
        jobs: int = 1,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> ExperimentResult:
        """Run the experiment by executing the task on all dataset records and evaluating the results.

        :param jobs: Maximum number of concurrent task and evaluator executions (default: 1)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors (default: False)
        :param sample_size: Optional number of dataset records to sample for testing
                            (default: None, uses full dataset)
        :return: ExperimentResult containing evaluation results and metadata
        """
        if jobs < 1:
            raise ValueError("jobs must be at least 1")

        self._setup_experiment()
        run_results = []
        # for backwards compatibility
        for run_iteration in range(self._runs):
            run = _ExperimentRunInfo(run_iteration)
            self._tags["run_id"] = str(run._id)
            self._tags["run_iteration"] = str(run._run_iteration)
            task_results = self._run_task(jobs, run, raise_errors, sample_size)
            evaluations = self._run_evaluators(task_results, raise_errors=raise_errors, jobs=jobs)
            summary_evals = self._run_summary_evaluators(task_results, evaluations, raise_errors, jobs=jobs)
            run_result = self._merge_results(run, task_results, evaluations, summary_evals)
            experiment_evals = self._generate_metrics_from_exp_results(run_result)
            if self._llmobs_instance is None or self._id is None:
                raise RuntimeError("Experiment not properly initialized")
            self._llmobs_instance._dne_client.experiment_eval_post(
                self._id, experiment_evals, convert_tags_dict_to_list(self._tags)
            )
            run_results.append(run_result)

        experiment_result: ExperimentResult = {
            # for backwards compatibility, the first result fills the old fields of rows and summary evals
            "summary_evaluations": run_results[0].summary_evaluations if len(run_results) > 0 else {},
            "rows": run_results[0].rows if len(run_results) > 0 else [],
            "runs": run_results,
        }
        return experiment_result

    def _process_record(self, idx_record: Tuple[int, DatasetRecord], run: _ExperimentRunInfo) -> Optional[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return None
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
            if span_context:
                span_id = span_context.get("span_id", "")
                trace_id = span_context.get("trace_id", "")
            else:
                span_id, trace_id = "", ""
            input_data = record["input_data"]
            record_id = record.get("record_id", "")
            tags = {
                **self._tags,
                "dataset_id": str(self._dataset._id),
                "dataset_record_id": str(record_id),
                "experiment_id": str(self._id),
            }
            output_data = None
            try:
                output_data = self._task(input_data, self._config)
            except Exception:
                span.set_exc_info(*sys.exc_info())
            self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

            span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
            if "metadata" in record:
                span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

            return self._build_task_result(idx, span, span_id, trace_id, cast(JSONType, output_data))

    def _run_task(
        self,
        jobs: int,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> List[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        subset_dataset = self._create_subset_dataset(sample_size)
        task_results = []
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            for result in executor.map(
                self._process_record,
                enumerate(subset_dataset),
                itertools.repeat(run, len(subset_dataset)),
            ):
                if not result:
                    continue
                task_results.append(result)
                err_dict = result.get("error") or {}
                if isinstance(err_dict, dict):
                    err_msg = err_dict.get("message")
                    err_stack = err_dict.get("stack")
                    err_type = err_dict.get("type")
                if raise_errors and err_msg:
                    raise RuntimeError(
                        "Error on record {}: {}\n{}\n{}".format(result["idx"], err_msg, err_type, err_stack)
                    )
        self._llmobs_instance.flush()  # Ensure spans get submitted in serverless environments
        return task_results

    def _run_evaluators(
        self, task_results: List[TaskResult], raise_errors: bool = False, jobs: int = 1
    ) -> List[EvaluationResult]:
        """Run evaluators on task results with concurrent execution using ThreadPoolExecutor.

        Supports both class-based (BaseEvaluator) and literal function evaluators.

        :param task_results: List of task results to evaluate
        :param raise_errors: Whether to raise exceptions on evaluation errors
        :param jobs: Maximum number of concurrent evaluator executions (default: 1)
        """

        def _evaluate_row(idx: int, task_result: TaskResult) -> Dict[str, Dict[str, JSONType]]:
            record: DatasetRecord = self._dataset[idx]
            input_data = record["input_data"]
            output_data = task_result["output"]
            expected_output = record["expected_output"]
            metadata = record.get("metadata", {})

            row_results: Dict[str, Dict[str, JSONType]] = {}

            for evaluator in self._evaluators:
                eval_result_value: JSONType = None
                eval_err: JSONType = None
                evaluator_name = ""
                extra_return_values: Dict[str, JSONType] = {}

                try:
                    if _is_class_evaluator(evaluator):
                        evaluator_name = evaluator.name
                        combined_metadata = {**metadata, "experiment_config": self._config}
                        context = EvaluatorContext(
                            input_data=input_data,
                            output_data=output_data,
                            expected_output=expected_output,
                            metadata=combined_metadata,
                            span_id=task_result.get("span_id"),
                            trace_id=task_result.get("trace_id"),
                        )
                        eval_result = evaluator.evaluate(context)
                    elif _is_function_evaluator(evaluator):
                        evaluator_name = evaluator.__name__
                        eval_result = evaluator(input_data, output_data, expected_output)
                    else:
                        logger.warning(
                            "Evaluator %s is neither a BaseEvaluator instance nor a callable function", evaluator
                        )
                        evaluator_name = str(evaluator)
                        eval_result = None

                    eval_result_value, extra_return_values = self._extract_evaluator_result(eval_result)

                except Exception as e:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
                    exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                    eval_err = {
                        "message": str(exc_value),
                        "type": exc_type_name,
                        "stack": exc_stack,
                    }
                    if raise_errors:
                        raise RuntimeError(f"Evaluator {evaluator_name} failed on row {idx}") from e

                row_results[evaluator_name] = {
                    "value": eval_result_value,
                    "error": eval_err,
                    **extra_return_values,
                }

            return row_results

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            results = list(executor.map(_evaluate_row, range(len(task_results)), task_results))

        evaluations: List[EvaluationResult] = [
            {"idx": idx, "evaluations": row_results} for idx, row_results in enumerate(results)
        ]
        return evaluations

    def _run_summary_evaluators(
        self,
        task_results: List[TaskResult],
        eval_results: List[EvaluationResult],
        raise_errors: bool = False,
        jobs: int = 1,
    ) -> List[EvaluationResult]:
        inputs, outputs, expected_outputs, metadata_list, eval_results_by_name = self._prepare_summary_data(
            task_results, eval_results
        )

        def _evaluate_summary_single(summary_evaluator: Any) -> Tuple[str, Dict[str, JSONType]]:
            eval_result_value: JSONType = None
            eval_err: JSONType = None
            evaluator_name = ""

            try:
                if _is_class_summary_evaluator(summary_evaluator):
                    evaluator_name = summary_evaluator.name
                    context = SummaryEvaluatorContext(
                        inputs=inputs,
                        outputs=outputs,
                        expected_outputs=expected_outputs,
                        evaluation_results=eval_results_by_name,
                        metadata=metadata_list,
                    )
                    eval_result = summary_evaluator.evaluate(context)
                else:
                    evaluator_name = summary_evaluator.__name__
                    eval_result = summary_evaluator(inputs, outputs, expected_outputs, eval_results_by_name)
                eval_result_value = eval_result
            except Exception as e:
                exc_type, exc_value, exc_tb = sys.exc_info()
                exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
                exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                eval_err = {
                    "message": str(exc_value),
                    "type": exc_type_name,
                    "stack": exc_stack,
                }
                if raise_errors:
                    raise RuntimeError(f"Summary evaluator {evaluator_name} failed") from e

            return (
                evaluator_name,
                {
                    "value": eval_result_value,
                    "error": eval_err,
                },
            )

        evaluations: List[EvaluationResult] = []
        evals_dict: Dict[str, Dict[str, JSONType]] = {}

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            results = list(executor.map(_evaluate_summary_single, self._summary_evaluators))

        for idx, (evaluator_name, eval_data) in enumerate(results):
            evals_dict[evaluator_name] = eval_data
            evaluations.append({"idx": idx, "evaluations": evals_dict})

        return evaluations


class AsyncExperiment(_ExperimentBase):
    def __init__(
        self,
        name: str,
        task: AsyncTaskType,
        dataset: Dataset,
        evaluators: Sequence[Union[AsyncEvaluatorFunctionType, BaseAsyncEvaluator]],
        project_name: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[List[Union[SummaryEvaluatorFunctionType, BaseSummaryEvaluator]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        self._init_common(
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

    async def run(
        self,
        concurrency: int = 1,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> ExperimentResult:
        """Run the experiment by executing the async task on all dataset records and evaluating the results.

        :param concurrency: Maximum number of concurrent task and evaluator executions (default: 1)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors (default: False)
        :param sample_size: Optional number of dataset records to sample for testing
                            (default: None, uses full dataset)
        :return: ExperimentResult containing evaluation results and metadata
        """
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")

        self._setup_experiment()
        semaphore = asyncio.Semaphore(concurrency)

        async def run_single_iteration(run_iteration: int) -> ExperimentRun:
            run = _ExperimentRunInfo(run_iteration)
            task_results = await self._run_task(semaphore, run, raise_errors, sample_size)
            evaluations = await self._run_evaluators(semaphore, task_results, raise_errors=raise_errors)
            summary_evals = self._run_summary_evaluators(task_results, evaluations, raise_errors)
            run_result = self._merge_results(run, task_results, evaluations, summary_evals)
            experiment_evals = self._generate_metrics_from_exp_results(run_result)
            if self._llmobs_instance is None or self._id is None:
                raise RuntimeError("Experiment not properly initialized")
            self._llmobs_instance._dne_client.experiment_eval_post(
                self._id, experiment_evals, convert_tags_dict_to_list(self._tags)
            )
            return run_result

        run_results = await asyncio.gather(*[run_single_iteration(i) for i in range(self._runs)])

        experiment_result: ExperimentResult = {
            "summary_evaluations": run_results[0].summary_evaluations if len(run_results) > 0 else {},
            "rows": run_results[0].rows if len(run_results) > 0 else [],
            "runs": run_results,
        }
        return experiment_result

    async def _process_record(self, idx: int, record: DatasetRecord, run: _ExperimentRunInfo) -> Optional[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return None
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
                **self._tags,
                "dataset_id": str(self._dataset._id),
                "dataset_record_id": str(record_id),
                "experiment_id": str(self._id),
            }
            output_data: JSONType = None
            try:
                output_data = await cast(Awaitable[JSONType], self._task(input_data, self._config))
            except Exception:
                span.set_exc_info(*sys.exc_info())
            self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

            span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
            if "metadata" in record:
                span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

            return self._build_task_result(idx, span, span_id, trace_id, output_data)

    async def _run_task(
        self,
        semaphore: asyncio.Semaphore,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> List[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        subset_dataset = self._create_subset_dataset(sample_size)

        async def process_with_semaphore(idx: int, record: DatasetRecord) -> Optional[TaskResult]:
            async with semaphore:
                return await self._process_record(idx, record, run)

        tasks = [process_with_semaphore(idx, record) for idx, record in enumerate(subset_dataset)]
        results = await asyncio.gather(*tasks)

        task_results = []
        for result in results:
            if not result:
                continue
            task_results.append(result)
            err_dict = result.get("error") or {}
            if isinstance(err_dict, dict):
                err_msg = err_dict.get("message")
                err_stack = err_dict.get("stack")
                err_type = err_dict.get("type")
            if raise_errors and err_msg:
                raise RuntimeError("Error on record {}: {}\n{}\n{}".format(result["idx"], err_msg, err_type, err_stack))

        self._llmobs_instance.flush()
        return task_results

    async def _evaluate_single(
        self,
        evaluator: Union[AsyncEvaluatorFunctionType, BaseAsyncEvaluator],
        idx: int,
        input_data: DatasetRecordInputType,
        output_data: JSONType,
        expected_output: JSONType,
        metadata: Dict[str, Any],
        task_result: TaskResult,
        raise_errors: bool,
    ) -> Tuple[str, Dict[str, JSONType]]:
        """Run a single async evaluator and return (name, result_dict)."""
        eval_result_value: JSONType = None
        eval_err: JSONType = None
        evaluator_name = ""
        extra_return_values: Dict[str, JSONType] = {}

        try:
            if _is_async_class_evaluator(evaluator):
                evaluator_name = evaluator.name  # type: ignore[union-attr]
                combined_metadata = {**metadata, "experiment_config": self._config}
                context = EvaluatorContext(
                    input_data=input_data,
                    output_data=output_data,
                    expected_output=expected_output,
                    metadata=combined_metadata,
                    span_id=task_result.get("span_id"),
                    trace_id=task_result.get("trace_id"),
                )
                eval_result = await evaluator.evaluate(context)  # type: ignore[union-attr]
            else:
                evaluator_name = evaluator.__name__  # type: ignore[union-attr]
                eval_result = await evaluator(input_data, output_data, expected_output)  # type: ignore[operator]

            eval_result_value, extra_return_values = self._extract_evaluator_result(eval_result)

        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
            exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            eval_err = {
                "message": str(exc_value),
                "type": exc_type_name,
                "stack": exc_stack,
            }
            if raise_errors:
                raise RuntimeError(f"Evaluator {evaluator_name} failed on row {idx}") from e

        return evaluator_name, {
            "value": eval_result_value,
            "error": eval_err,
            **extra_return_values,
        }

    async def _run_evaluators(
        self, semaphore: asyncio.Semaphore, task_results: List[TaskResult], raise_errors: bool = False
    ) -> List[EvaluationResult]:
        """Run async evaluators on task results with concurrent execution.

        :param semaphore: Semaphore to control concurrency
        :param task_results: List of task results to evaluate
        :param raise_errors: Whether to raise exceptions on evaluation errors
        """

        async def _evaluate_row(idx: int, task_result: TaskResult) -> Dict[str, Dict[str, JSONType]]:
            record: DatasetRecord = self._dataset[idx]
            input_data = record["input_data"]
            output_data = task_result["output"]
            expected_output = record["expected_output"]
            metadata = record.get("metadata", {})

            async def run_with_semaphore(
                evaluator: Union[AsyncEvaluatorFunctionType, BaseAsyncEvaluator],
            ) -> Tuple[str, Dict[str, JSONType]]:
                async with semaphore:
                    return await self._evaluate_single(
                        evaluator, idx, input_data, output_data, expected_output, metadata, task_result, raise_errors
                    )

            results = await asyncio.gather(*[run_with_semaphore(ev) for ev in self._evaluators])
            return {name: result for name, result in results}

        tasks = [_evaluate_row(idx, tr) for idx, tr in enumerate(task_results)]
        results = await asyncio.gather(*tasks)

        return [{"idx": idx, "evaluations": row_results} for idx, row_results in enumerate(results)]

    def _run_summary_evaluators(
        self,
        task_results: List[TaskResult],
        eval_results: List[EvaluationResult],
        raise_errors: bool = False,
    ) -> List[EvaluationResult]:
        """Run sync summary evaluators on aggregated results."""
        inputs, outputs, expected_outputs, metadata_list, eval_results_by_name = self._prepare_summary_data(
            task_results, eval_results
        )

        evaluations: List[EvaluationResult] = []
        evals_dict: Dict[str, Dict[str, JSONType]] = {}

        for summary_evaluator in self._summary_evaluators:
            eval_result_value: JSONType = None
            eval_err: JSONType = None
            evaluator_name = ""

            try:
                if _is_class_summary_evaluator(summary_evaluator):
                    evaluator_name = summary_evaluator.name
                    context = SummaryEvaluatorContext(
                        inputs=inputs,
                        outputs=outputs,
                        expected_outputs=expected_outputs,
                        evaluation_results=eval_results_by_name,
                        metadata=metadata_list,
                    )
                    eval_result = summary_evaluator.evaluate(context)
                else:
                    evaluator_name = summary_evaluator.__name__
                    eval_result = summary_evaluator(inputs, outputs, expected_outputs, eval_results_by_name)
                eval_result_value = eval_result
            except Exception as e:
                exc_type, exc_value, exc_tb = sys.exc_info()
                exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
                exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                eval_err = {
                    "message": str(exc_value),
                    "type": exc_type_name,
                    "stack": exc_stack,
                }
                if raise_errors:
                    raise RuntimeError(f"Summary evaluator {evaluator_name} failed") from e

            evals_dict[evaluator_name] = {
                "value": eval_result_value,
                "error": eval_err,
            }

        if evals_dict:
            evaluations.append({"idx": 0, "evaluations": evals_dict})

        return evaluations


def _get_base_url() -> str:
    subdomain = ""
    if config._dd_site in DD_SITES_NEEDING_APP_SUBDOMAIN:
        subdomain = "app."

    return f"https://{subdomain}{config._dd_site}"
