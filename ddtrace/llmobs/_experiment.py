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
from typing import Iterator
from typing import Optional
from typing import Sequence
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

JSONType = Union[str, int, float, bool, None, list["JSONType"], dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, list[JSONType], dict[str, JSONType]]
ConfigType = dict[str, JSONType]
DatasetRecordInputType = dict[str, NonNoneJSONType]

TaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], JSONType]
AsyncTaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], Awaitable[JSONType]]


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

    input_data: dict[str, Any]
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

    inputs: list[DatasetRecordInputType]
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
EvaluatorType = Union[
    Callable[[DatasetRecordInputType, JSONType, JSONType], Union[JSONType, "EvaluatorResult"]],
    BaseEvaluator,
]
AsyncEvaluatorType = Union[
    Callable[[DatasetRecordInputType, JSONType, JSONType], Awaitable[Union[JSONType, "EvaluatorResult"]]],
    BaseAsyncEvaluator,
]

# Summary evaluator types
SummaryEvaluatorType = Union[
    Callable[
        [Sequence[DatasetRecordInputType], Sequence[JSONType], Sequence[JSONType], dict[str, Sequence[JSONType]]],
        JSONType,
    ],
    BaseSummaryEvaluator,
]
AsyncSummaryEvaluatorType = Union[
    Callable[
        [Sequence[DatasetRecordInputType], Sequence[JSONType], Sequence[JSONType], dict[str, Sequence[JSONType]]],
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


class Project(TypedDict):
    name: str
    _id: str


class DatasetRecordRaw(TypedDict):
    input_data: DatasetRecordInputType
    expected_output: JSONType
    metadata: dict[str, Any]


class _UpdatableDatasetRecordOptional(TypedDict, total=False):
    input_data: DatasetRecordInputType
    expected_output: JSONType
    metadata: dict[str, Any]


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
    metadata: dict[str, JSONType]
    error: dict[str, Optional[str]]


class EvaluationResult(TypedDict):
    idx: int
    evaluations: dict[str, dict[str, JSONType]]


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
    input: dict[str, NonNoneJSONType]
    output: JSONType
    expected_output: JSONType
    evaluations: dict[str, dict[str, JSONType]]
    metadata: dict[str, JSONType]
    error: dict[str, Optional[str]]


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


class ExperimentResult(TypedDict):
    # TODO: remove these fields (summary_evaluations, rows) in the next major release (5.x)
    summary_evaluations: dict[str, dict[str, JSONType]]
    rows: list[ExperimentRowResult]
    runs: list[ExperimentRun]


class Dataset:
    name: str
    description: str
    _id: str
    _records: list[DatasetRecord]
    _version: int
    _latest_version: int
    _dne_client: "LLMObsExperimentsClient"
    _new_records_by_record_id: dict[str, DatasetRecordRaw]
    _updated_record_ids_to_new_fields: dict[str, UpdatableDatasetRecord]
    _deleted_record_ids: list[str]

    BATCH_UPDATE_THRESHOLD = 5 * 1024 * 1024  # 5MB

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

    def push(
        self, deduplicate: bool = True, create_new_version: bool = True, bulk_upload: Optional[bool] = None
    ):
        """Pushes any local changes in this dataset since the last push.

        :param deduplicate:
            Wether to deduplicate the records or not. Does not deduplicate against existing
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
        self, deduplicate: bool = True, create_new_version: bool = True, bulk_upload: Optional[bool] = None
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
            updated_records = list(self._updated_record_ids_to_new_fields.values())
            new_version, new_record_ids = self._dne_client.dataset_batch_update(
                dataset_id=self._id,
                insert_records=list(self._new_records_by_record_id.values()),
                update_records=updated_records,
                delete_record_ids=self._deleted_record_ids,
                deduplicate=deduplicate,
                create_new_version=create_new_version,
            )

            # Attach server-assigned record ids to newly created records.
            # Use a snapshot of the keys so we can selectively remove only the records
            # that the server acknowledged. Records the server did not return (e.g. because
            # they were deduplicated against records in another dataset) keep their local
            # placeholder id and stay in _new_records_by_record_id so that a subsequent
            # delete() call treats them as local-only rather than sending the non-deterministic
            # placeholder id to the server as a delete_record_id.
            pending_keys = list(self._new_records_by_record_id.keys())
            for key, record_id in zip(pending_keys, new_record_ids):
                self._new_records_by_record_id[key]["record_id"] = record_id  # type: ignore
                del self._new_records_by_record_id[key]

            data_changed = len(new_record_ids) > 0
            if new_version != -1:
                self._latest_version = new_version
            else:
                # FIXME: we don't get version numbers in responses to deletion requests
                self._latest_version = self._latest_version + 1
            logger.debug("new_version %d latest_version %d", new_version, self._latest_version)
            # no matter what the version was before the push, pushing will result in the dataset being on the current
            # version tracked by the backend
            self._version = self._latest_version
        self._deleted_record_ids = []
        self._updated_record_ids_to_new_fields = {}
        return data_changed

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

    def extend(self, records: list[DatasetRecordRaw]) -> None:
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

            data_rows.append(flat_record)

        records_list = []
        for flat_record in data_rows:
            row = [flat_record.get(col, None) for col in column_tuples]
            records_list.append(row)

        return pd.DataFrame(data=records_list, columns=pd.MultiIndex.from_tuples(column_tuples))


class BaseExperiment(ABC):
    """Base class for sync and async experiments."""

    _task: Union[TaskType, AsyncTaskType]
    _evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]]
    _summary_evaluators: Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]

    def __init__(
        self,
        name: str,
        task: Union[TaskType, AsyncTaskType],
        dataset: Dataset,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]],
        project_name: str,
        description: str = "",
        tags: Optional[dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = list(evaluators)
        self._summary_evaluators = list(summary_evaluators) if summary_evaluators else []
        self._description = description
        self._tags: dict[str, str] = tags or {}
        self._tags["ddtrace.version"] = str(__version__)
        self._tags["project_name"] = project_name
        self._tags["dataset_name"] = dataset.name
        self._tags["experiment_name"] = name
        self._config: dict[str, JSONType] = config or {}
        self._runs: int = runs or 1
        self._llmobs_instance = _llmobs_instance

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None
        self._run_name: Optional[str] = None

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
            metadata: dict[str, JSONType] = {"tags": cast(list[JSONType], convert_tags_dict_to_list(self._tags))}
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
        source: str = "custom",
        reasoning: Optional[str] = None,
        assessment: Optional[str] = None,
        metadata: Optional[dict[str, JSONType]] = None,
        tags: Optional[dict[str, str]] = None,
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
            return eval_result.value, extra_return_values
        return eval_result, extra_return_values

    def _build_evaluator_error(self, exc: Exception) -> dict[str, Any]:
        exc_type, exc_value, exc_tb = sys.exc_info()
        exc_type_name = type(exc).__name__ if exc_type is not None else "Unknown Exception"
        exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        return {
            "message": str(exc_value),
            "type": exc_type_name,
            "stack": exc_stack,
        }

    def _prepare_summary_evaluator_data(
        self, task_results: list[TaskResult], eval_results: list[EvaluationResult]
    ) -> tuple[
        list[DatasetRecordInputType],
        list[JSONType],
        list[JSONType],
        list[dict[str, Any]],
        dict[str, list[JSONType]],
    ]:
        inputs: list[DatasetRecordInputType] = []
        outputs: list[JSONType] = []
        expected_outputs: list[JSONType] = []
        metadata_list: list[dict[str, Any]] = []
        eval_results_by_name: dict[str, list[JSONType]] = {}

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

    def _setup_experiment(self, llmobs_not_enabled_error: str) -> None:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(llmobs_not_enabled_error)

        project = self._llmobs_instance._dne_client.project_create_or_get(self._project_name)
        self._project_id = project.get("_id", "")
        self._tags["project_id"] = self._project_id

        experiment_id, experiment_run_name = self._llmobs_instance._dne_client.experiment_create(
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

    def _build_experiment_result(self, run_results: list[ExperimentRun]) -> ExperimentResult:
        return {
            "summary_evaluations": run_results[0].summary_evaluations if run_results else {},
            "rows": run_results[0].rows if run_results else [],
            "runs": run_results,
        }

    def _build_task_result(self, idx: int, span: Any, span_id: str, trace_id: str, output_data: JSONType) -> TaskResult:
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

    def _get_record_tags(self, record_id: str) -> dict[str, str]:
        return {
            **self._tags,
            "dataset_id": str(self._dataset._id),
            "dataset_record_id": str(record_id),
            "experiment_id": str(self._id),
        }

    def _check_and_raise_task_error(self, task_result: TaskResult, raise_errors: bool) -> None:
        err_dict = task_result.get("error") or {}
        if isinstance(err_dict, dict):
            err_msg = err_dict.get("message")
            err_stack = err_dict.get("stack")
            err_type = err_dict.get("type")
            if raise_errors and err_msg:
                raise RuntimeError(
                    "Error on record {}: {}\n{}\n{}".format(task_result["idx"], err_msg, err_type, err_stack)
                )


class Experiment(BaseExperiment):
    """Synchronous experiment using ThreadPoolExecutor for concurrent execution."""

    # Narrower type declarations for sync experiment
    _task: TaskType
    _evaluators: Sequence[EvaluatorType]
    _summary_evaluators: Sequence[SummaryEvaluatorType]

    def __init__(
        self,
        name: str,
        task: TaskType,
        dataset: Dataset,
        evaluators: Sequence[EvaluatorType],
        project_name: str,
        description: str = "",
        tags: Optional[dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[Sequence[SummaryEvaluatorType]] = None,
        runs: Optional[int] = None,
    ) -> None:
        super().__init__(
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

        self._setup_experiment(
            "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
            "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
        )

        run_results = []
        for run_iteration in range(self._runs):
            run = _ExperimentRunInfo(run_iteration)
            self._tags["run_id"] = str(run._id)
            self._tags["run_iteration"] = str(run._run_iteration)
            task_results = self._run_task(jobs, run, raise_errors, sample_size)
            evaluations = self._run_evaluators(task_results, raise_errors=raise_errors, jobs=jobs)
            summary_evals = self._run_summary_evaluators(task_results, evaluations, raise_errors, jobs=jobs)
            run_result = self._merge_results(run, task_results, evaluations, summary_evals)
            experiment_evals = self._generate_metrics_from_exp_results(run_result)
            self._llmobs_instance._dne_client.experiment_eval_post(  # type: ignore[union-attr]
                cast(str, self._id), experiment_evals, convert_tags_dict_to_list(self._tags)
            )
            run_results.append(run_result)

        return self._build_experiment_result(run_results)

    def _process_record(self, idx_record: tuple[int, DatasetRecord], run: _ExperimentRunInfo) -> Optional[TaskResult]:
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
            tags = self._get_record_tags(record_id)
            output_data: JSONType = None
            try:
                output_data = self._task(input_data, self._config)
            except Exception:
                span.set_exc_info(*sys.exc_info())
            self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

            span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
            if "metadata" in record:
                span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

            return self._build_task_result(idx, span, span_id, trace_id, output_data)

    def _run_task(
        self,
        jobs: int,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> list[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        subset_dataset = self._get_subset_dataset(sample_size)
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
                self._check_and_raise_task_error(result, raise_errors)
        self._llmobs_instance.flush()  # Ensure spans get submitted in serverless environments
        return task_results

    def _run_evaluators(
        self, task_results: list[TaskResult], raise_errors: bool = False, jobs: int = 1
    ) -> list[EvaluationResult]:
        def _evaluate_row(idx: int, task_result: TaskResult) -> dict[str, dict[str, JSONType]]:
            record: DatasetRecord = self._dataset[idx]
            input_data = record["input_data"]
            output_data = task_result["output"]
            expected_output = record["expected_output"]
            metadata = record.get("metadata", {})

            row_results: dict[str, dict[str, JSONType]] = {}

            for evaluator in self._evaluators:
                eval_result_value: JSONType = None
                eval_err: JSONType = None
                evaluator_name = ""

                try:
                    if _is_class_evaluator(evaluator):
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
                        eval_result = evaluator.evaluate(context)  # type: ignore[union-attr]
                    elif _is_function_evaluator(evaluator):
                        evaluator_name = evaluator.__name__  # type: ignore[union-attr]
                        eval_result = evaluator(input_data, output_data, expected_output)  # type: ignore[operator]
                    else:
                        logger.warning(
                            "Evaluator %s is neither a BaseEvaluator instance nor a callable function", evaluator
                        )
                        evaluator_name = str(evaluator)
                        eval_result = None

                    eval_result_value, extra_return_values = self._extract_evaluator_result(eval_result)

                except Exception as e:
                    extra_return_values = {}
                    eval_err = self._build_evaluator_error(e)
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

        evaluations: list[EvaluationResult] = [
            {"idx": idx, "evaluations": row_results} for idx, row_results in enumerate(results)
        ]
        return evaluations

    def _run_summary_evaluators(
        self,
        task_results: list[TaskResult],
        eval_results: list[EvaluationResult],
        raise_errors: bool = False,
        jobs: int = 1,
    ) -> list[EvaluationResult]:
        inputs, outputs, expected_outputs, metadata_list, eval_results_by_name = self._prepare_summary_evaluator_data(
            task_results, eval_results
        )

        def _evaluate_summary_single(summary_evaluator: Any) -> tuple[str, dict[str, JSONType]]:
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

        evaluations: list[EvaluationResult] = []
        evals_dict: dict[str, dict[str, JSONType]] = {}

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            results = list(executor.map(_evaluate_summary_single, self._summary_evaluators))

        for idx, (evaluator_name, eval_data) in enumerate(results):
            evals_dict[evaluator_name] = eval_data
            evaluations.append({"idx": idx, "evaluations": evals_dict})

        return evaluations


class AsyncExperiment(BaseExperiment):
    """Async experiment using asyncio for concurrent execution.

    This class supports async tasks, evaluators, and summary evaluators.
    Sync evaluators are also supported and will be run via asyncio.to_thread().
    """

    # Narrower type declarations for async experiment
    _task: AsyncTaskType
    _evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]]
    _summary_evaluators: Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]

    def __init__(
        self,
        name: str,
        task: AsyncTaskType,
        dataset: Dataset,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]],
        project_name: str,
        description: str = "",
        tags: Optional[dict[str, str]] = None,
        config: Optional[ConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[Sequence[Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType]]] = None,
        runs: Optional[int] = None,
    ) -> None:
        super().__init__(
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
        jobs: int = 10,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> ExperimentResult:
        """Run the async experiment by executing the task on all dataset records and evaluating the results.

        :param jobs: Maximum number of concurrent task and evaluator executions (default: 10)
        :param raise_errors: Whether to raise exceptions on task or evaluator errors (default: False)
        :param sample_size: Optional number of dataset records to sample for testing
                            (default: None, uses full dataset)
        :return: ExperimentResult containing evaluation results and metadata
        """
        if jobs < 1:
            raise ValueError("jobs must be at least 1")

        self._setup_experiment(
            "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
            "and create the experiment via `LLMObs.async_experiment(...)` before running the experiment."
        )

        run_results = []
        for run_iteration in range(self._runs):
            run = _ExperimentRunInfo(run_iteration)
            self._tags["run_id"] = str(run._id)
            self._tags["run_iteration"] = str(run._run_iteration)
            task_results = await self._run_task(jobs, run, raise_errors, sample_size)
            evaluations = await self._run_evaluators(task_results, raise_errors=raise_errors, jobs=jobs)
            summary_evals = await self._run_summary_evaluators(task_results, evaluations, raise_errors, jobs=jobs)
            run_result = self._merge_results(run, task_results, evaluations, summary_evals)
            experiment_evals = self._generate_metrics_from_exp_results(run_result)
            self._llmobs_instance._dne_client.experiment_eval_post(  # type: ignore[union-attr]
                cast(str, self._id), experiment_evals, convert_tags_dict_to_list(self._tags)
            )
            run_results.append(run_result)

        return self._build_experiment_result(run_results)

    async def _process_record(
        self,
        idx_record: tuple[int, DatasetRecord],
        run: _ExperimentRunInfo,
        semaphore: asyncio.Semaphore,
    ) -> Optional[TaskResult]:
        """Process single record asynchronously."""
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
                if span_context:
                    span_id = span_context.get("span_id", "")
                    trace_id = span_context.get("trace_id", "")
                else:
                    span_id, trace_id = "", ""
                input_data = record["input_data"]
                record_id = record.get("record_id", "")
                tags = self._get_record_tags(record_id)
                output_data = None
                try:
                    output_data = await self._task(input_data, self._config)
                except Exception:
                    span.set_exc_info(*sys.exc_info())
                self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

                span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
                if "metadata" in record:
                    span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

                return self._build_task_result(idx, span, span_id, trace_id, output_data)

    async def _run_task(
        self,
        jobs: int,
        run: _ExperimentRunInfo,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> list[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        subset_dataset = self._get_subset_dataset(sample_size)

        semaphore = asyncio.Semaphore(jobs)
        coros = [self._process_record(idx_record, run, semaphore) for idx_record in enumerate(subset_dataset)]
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
            self._check_and_raise_task_error(task_result, raise_errors)

        self._llmobs_instance.flush()  # Ensure spans get submitted in serverless environments
        return task_results

    async def _run_evaluators(
        self, task_results: list[TaskResult], raise_errors: bool = False, jobs: int = 10
    ) -> list[EvaluationResult]:
        semaphore = asyncio.Semaphore(jobs)

        async def _evaluate_row(idx: int, task_result: TaskResult) -> dict[str, dict[str, JSONType]]:
            async with semaphore:
                record: DatasetRecord = self._dataset[idx]
                input_data = record["input_data"]
                output_data = task_result["output"]
                expected_output = record["expected_output"]
                metadata = record.get("metadata", {})

                row_results: dict[str, dict[str, JSONType]] = {}

                for evaluator in self._evaluators:
                    eval_result_value: JSONType = None
                    eval_err: JSONType = None
                    evaluator_name = ""

                    try:
                        if isinstance(evaluator, BaseAsyncEvaluator):
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
                            eval_result = await evaluator.evaluate(context)
                        elif asyncio.iscoroutinefunction(evaluator):
                            evaluator_name = evaluator.__name__
                            eval_result = await evaluator(input_data, output_data, expected_output)
                        elif _is_class_evaluator(evaluator):
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
                            eval_result = await asyncio.to_thread(evaluator.evaluate, context)  # type: ignore[union-attr]
                        elif _is_function_evaluator(evaluator):
                            evaluator_name = evaluator.__name__  # type: ignore[union-attr]
                            eval_result = await asyncio.to_thread(
                                evaluator,  # type: ignore[arg-type]
                                input_data,
                                output_data,
                                expected_output,
                            )
                        else:
                            logger.warning(
                                "Evaluator %s is neither a BaseEvaluator instance nor a callable function", evaluator
                            )
                            evaluator_name = str(evaluator)
                            eval_result = None

                        eval_result_value, extra_return_values = self._extract_evaluator_result(eval_result)

                    except Exception as e:
                        extra_return_values = {}
                        eval_err = self._build_evaluator_error(e)
                        if raise_errors:
                            raise RuntimeError(f"Evaluator {evaluator_name} failed on row {idx}") from e

                    row_results[evaluator_name] = {
                        "value": eval_result_value,
                        "error": eval_err,
                        **extra_return_values,
                    }

                return row_results

        coros = [_evaluate_row(idx, task_result) for idx, task_result in enumerate(task_results)]
        results: list[dict[str, dict[str, JSONType]]] = await asyncio.gather(*coros)

        evaluations: list[EvaluationResult] = []
        for idx, row_results in enumerate(results):
            evaluations.append({"idx": idx, "evaluations": row_results})

        return evaluations

    async def _run_summary_evaluators(
        self,
        task_results: list[TaskResult],
        eval_results: list[EvaluationResult],
        raise_errors: bool = False,
        jobs: int = 10,
    ) -> list[EvaluationResult]:
        inputs, outputs, expected_outputs, metadata_list, eval_results_by_name = self._prepare_summary_evaluator_data(
            task_results, eval_results
        )

        semaphore = asyncio.Semaphore(jobs)

        async def _evaluate_summary_single(summary_evaluator: Any) -> tuple[str, dict[str, JSONType]]:
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
                        eval_result = await summary_evaluator(inputs, outputs, expected_outputs, eval_results_by_name)
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
                        eval_result = await asyncio.to_thread(
                            summary_evaluator, inputs, outputs, expected_outputs, eval_results_by_name
                        )
                    eval_result_value = eval_result
                except Exception as e:
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

        return evaluations


def _get_base_url() -> str:
    subdomain = ""
    if config._dd_site in DD_SITES_NEEDING_APP_SUBDOMAIN:
        subdomain = "app."

    return f"https://{subdomain}{config._dd_site}"
