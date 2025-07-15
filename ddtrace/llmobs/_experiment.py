from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sys
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union
from typing import cast
from typing import overload

from typing_extensions import NotRequired

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentsClient


logger = get_logger(__name__)

JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]


class DatasetRecord(TypedDict):
    input_data: Dict[str, NonNoneJSONType]
    expected_output: JSONType
    metadata: Dict[str, Any]
    record_id: NotRequired[Optional[str]]


class _TaskResult(TypedDict):
    idx: int
    output: JSONType
    metadata: Dict[str, Optional[Union[str, int]]]
    error: Dict[str, Optional[str]]


class _EvaluationResult(TypedDict):
    idx: int
    evaluations: Dict[str, Dict[str, JSONType]]


class ExperimentResult(TypedDict):
    idx: int
    record_id: Optional[str]
    input: Dict[str, NonNoneJSONType]
    output: JSONType
    expected_output: JSONType
    evaluations: Dict[str, Dict[str, JSONType]]
    metadata: Dict[str, JSONType]
    error: Dict[str, Optional[str]]


class Dataset:
    name: str
    description: str
    _id: str
    _records: List[DatasetRecord]
    _version: int
    _dne_client: Optional["LLMObsExperimentsClient"]

    def __init__(
        self, name: str, dataset_id: str, records: List[DatasetRecord], description: str, version: int
    ) -> None:
        self.name = name
        self.description = description
        self._id = dataset_id
        self._records = records
        self._dne_client = None
        self._version = version

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
        new_version = self._dne_client.dataset_batch_update(self._id, self._records)
        self._version = new_version

    @overload
    def __getitem__(self, index: int) -> DatasetRecord:
        ...

    @overload
    def __getitem__(self, index: slice) -> List[DatasetRecord]:
        ...

    def __getitem__(self, index: Union[int, slice]) -> Union[DatasetRecord, List[DatasetRecord]]:
        return self._records.__getitem__(index)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[DatasetRecord]:
        return iter(self._records)


class Experiment:
    def __init__(
        self,
        name: str,
        task: Callable[[Dict[str, NonNoneJSONType], Optional[Dict[str, JSONType]]], JSONType],
        dataset: Dataset,
        evaluators: List[Callable[[Dict[str, NonNoneJSONType], JSONType, JSONType], JSONType]],
        project_name: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        config: Optional[Dict[str, JSONType]] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._description = description
        self._tags: List[str] = tags or []
        self._config: Dict[str, JSONType] = config or {}
        self._llmobs_instance = _llmobs_instance

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        # Below values are set at experiment creation time
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None
        self._run_name: Optional[str] = None

    def run(
        self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None
    ) -> List[ExperimentResult]:
        if not self._llmobs_instance:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )
        if not self._llmobs_instance.enabled:
            logger.warning(
                "Skipping experiment as LLMObs is not enabled. "
                "Ensure LLM Observability is enabled via `LLMObs.enable(...)` or set `DD_LLMOBS_ENABLED=1`."
            )
            return []
        experiment_id, experiment_run_name = self._llmobs_instance._create_experiment(
            name=self.name,
            dataset_id=self._dataset._id,
            project_name=self._project_name,
            dataset_version=self._dataset._version,
            exp_config=self._config,
            tags=self._tags,
            description=self._description,
        )
        self._id = experiment_id
        self._run_name = experiment_run_name
        task_results = self._run_task(jobs, raise_errors, sample_size)
        evaluations = self._run_evaluators(task_results, raise_errors=raise_errors)
        experiment_results = self._merge_results(task_results, evaluations)
        return experiment_results

    def _process_record(self, idx_record: Tuple[int, DatasetRecord]) -> Optional[_TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return None
        idx, record = idx_record
        with self._llmobs_instance._experiment(name=self._task.__name__, experiment_id=self._id) as span:
            span_context = self._llmobs_instance.export_span(span=span)
            if span_context:
                span_id = span_context.get("span_id", "")
                trace_id = span_context.get("trace_id", "")
            else:
                span_id, trace_id = "", ""
            input_data = record["input_data"]
            record_id = record.get("record_id", "")
            tags = {"dataset_id": self._dataset._id, "dataset_record_id": record_id, "experiment_id": self._id}
            output_data = None
            try:
                output_data = self._task(input_data, self._config)
            except Exception:
                span.set_exc_info(*sys.exc_info())
            self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)
            return {
                "idx": idx,
                "output": output_data,
                "metadata": {
                    "timestamp": span.start_ns,
                    "duration": span.duration_ns,
                    "dataset_record_index": idx,
                    "experiment_name": self.name,
                    "dataset_name": self._dataset.name,
                    "span_id": span_id,
                    "trace_id": trace_id,
                },
                "error": {
                    "message": span.get_tag(ERROR_MSG),
                    "stack": span.get_tag(ERROR_STACK),
                    "type": span.get_tag(ERROR_TYPE),
                },
            }

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[_TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        if sample_size is not None and sample_size < len(self._dataset):
            subset_records = [deepcopy(record) for record in self._dataset._records[:sample_size]]
            subset_name = "[Test subset of {} records] {}".format(sample_size, self._dataset.name)
            subset_dataset = Dataset(
                name=subset_name,
                dataset_id=self._dataset._id,
                records=subset_records,
                description=self._dataset.description,
                version=self._dataset._version,
            )
        else:
            subset_dataset = self._dataset
        task_results = []
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            for result in executor.map(self._process_record, enumerate(subset_dataset)):
                if not result:
                    continue
                task_results.append(result)
                err_dict = result.get("error") or {}
                err_msg = err_dict.get("message") if isinstance(err_dict, dict) else None
                if raise_errors and err_msg:
                    raise RuntimeError("Error on record {}: {}".format(result["idx"], err_msg))
        self._llmobs_instance.flush()  # Ensure spans get submitted in serverless environments
        return task_results

    def _run_evaluators(self, task_results: List[_TaskResult], raise_errors: bool = False) -> List[_EvaluationResult]:
        if not task_results:
            raise ValueError("No task results to evaluate.")
        evaluations: List[_EvaluationResult] = []
        for idx, task_result in enumerate(task_results):
            output_data = task_result["output"]
            record: DatasetRecord = self._dataset[idx]
            input_data = record["input_data"]
            expected_output = record["expected_output"]
            evals_dict = {}
            for evaluator in self._evaluators:
                eval_result: JSONType = None
                eval_err: JSONType = None
                try:
                    eval_result = evaluator(input_data, output_data, expected_output)
                except Exception as e:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
                    exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                    eval_err = {"message": str(exc_value), "type": exc_type_name, "stack": exc_stack}
                    if raise_errors:
                        raise RuntimeError(f"Evaluator {evaluator.__name__} failed on row {idx}")
                evals_dict[evaluator.__name__] = {"value": eval_result, "error": eval_err}
            evaluation: _EvaluationResult = {"idx": idx, "evaluations": evals_dict}
            evaluations.append(evaluation)
        return evaluations

    def _merge_results(
        self, task_results: List[_TaskResult], evaluations: List[_EvaluationResult]
    ) -> List[ExperimentResult]:
        experiment_results = []
        for idx, task_result in enumerate(task_results):
            output_data = task_result["output"]
            metadata: Dict[str, JSONType] = {"tags": cast(List[JSONType], self._tags)}
            _metadata = task_result.get("metadata") or {}
            if isinstance(_metadata, dict):
                metadata.update(_metadata)
            record: DatasetRecord = self._dataset[idx]
            evals = evaluations[idx]["evaluations"]
            exp_result: ExperimentResult = {
                "idx": idx,
                "record_id": record.get("record_id", ""),
                "input": record["input_data"],
                "expected_output": record["expected_output"],
                "output": output_data,
                "evaluations": evals,
                "metadata": metadata,
                "error": task_result["error"],
            }
            experiment_results.append(exp_result)
        return experiment_results
