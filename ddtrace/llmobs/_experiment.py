from typing import TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sys
import time
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union

from typing_extensions import NotRequired

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.llmobs._constants import EXPERIMENT_EXPECTED_OUTPUT_KEY


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentsClient


JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]
API_PROCESSING_TIME_SLEEP = 6  # median events processor processing time in seconds
FLUSH_EVERY = 10  # default number of records to process before flushing


class DatasetRecord(TypedDict):
    input_data: NonNoneJSONType
    expected_output: JSONType
    metadata: Dict[str, Any]
    record_id: NotRequired[Optional[str]]


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
        evaluators: List[Callable[[NonNoneJSONType, JSONType, JSONType], JSONType]],
        project_name: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._description = description
        self._tags = tags or []
        self._config: Dict[str, Any] = config or {}
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

    def run(self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None) -> None:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )
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
        self._run_evaluators(task_results, raise_errors=raise_errors)
        return

    def _process_record(self, idx_record: Tuple[int, DatasetRecord]) -> Dict[str, Any]:
        idx, record = idx_record
        start_ns = time.time_ns()
        with self._llmobs_instance._experiment(name=self._task.__name__, experiment_id=self._id) as span:
            span_context = self._llmobs_instance.export_span(span=span)
            span_id = span_context.get("span_id", "")
            trace_id = span_context.get("trace_id", "")
            input_data = record["input_data"]
            record_id = record.get("record_id", "")
            expected_output = record["expected_output"]
            tags = {"dataset_id": self._dataset._id, "dataset_record_id": record_id, "experiment_id": self._id}
            output_data = None
            try:
                output_data = self._task(input_data)  # FIXME: support config?
            except Exception:
                span.set_exc_info(*sys.exc_info())
            self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)
            span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT_KEY, expected_output)  # FIXME: should we be doing this here?
            return {
                "idx": idx,
                "output": output_data,
                "metadata": {
                    "timestamp": start_ns,
                    "duration": time.time_ns() - start_ns,
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

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[Any]:
        if sample_size is not None and sample_size < len(self._dataset):
            subset_data = [deepcopy(record) for record in self._dataset._data[:sample_size]]
            subset_name = "[Test subset of {} records] {}".format(sample_size, self._dataset.name)
            subset_dataset = Dataset(name=subset_name, dataset_id=self._dataset._id, data=subset_data)
        else:
            subset_dataset = self._dataset
        task_results = []
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            for result in executor.map(self._process_record, enumerate(subset_dataset)):
                task_results.append(result)
                if raise_errors and result["error"]["message"]:
                    raise RuntimeError("Error on record {}: {}".format(result["idx"], result["error"]["message"]))
        self._llmobs_instance.flush()
        time.sleep(API_PROCESSING_TIME_SLEEP)
        return task_results

    def _run_evaluators(self, task_results, raise_errors: bool = False) -> None:
        pass
