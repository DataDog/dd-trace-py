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
import uuid

import ddtrace
from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import DD_SITES_NEEDING_APP_SUBDOMAIN
from ddtrace.llmobs._constants import EXPERIMENT_EXPECTED_OUTPUT
from ddtrace.llmobs._utils import convert_tags_dict_to_list
from ddtrace.llmobs._utils import safe_json


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentEvalMetricEvent
    from ddtrace.llmobs._writer import LLMObsExperimentsClient


logger = get_logger(__name__)

JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]
ExperimentConfigType = Dict[str, JSONType]
DatasetRecordInputType = Dict[str, NonNoneJSONType]


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


class ExperimentResult(TypedDict):
    summary_evaluations: Dict[str, Dict[str, JSONType]]
    rows: List[ExperimentRowResult]


class Dataset:
    name: str
    description: str
    _id: str
    _records: List[DatasetRecord]
    _version: int
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
        version: int,
        _dne_client: "LLMObsExperimentsClient",
    ) -> None:
        self.name = name
        self.project = project
        self.description = description
        self._id = dataset_id
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
                self._id, list(self._new_records_by_record_id.values()), updated_records, self._deleted_record_ids
            )

            # attach record ids to newly created records
            for record, record_id in zip(self._new_records_by_record_id.values(), new_record_ids):
                record["record_id"] = record_id  # type: ignore

            # FIXME: we don't get version numbers in responses to deletion requests
            self._version = new_version if new_version != -1 else self._version + 1
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
        self._records[index] = {**self._records[index], **record, "record_id": record_id}

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

    def _estimate_delta_size(self) -> int:
        """rough estimate (in bytes) of the size of the next batch update call if it happens"""
        size = len(safe_json(self._new_records_by_record_id)) + len(safe_json(self._updated_record_ids_to_new_fields))
        logger.debug("estimated delta size %d", size)
        return size

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


class Experiment:
    def __init__(
        self,
        name: str,
        task: Callable[[DatasetRecordInputType, Optional[ExperimentConfigType]], JSONType],
        dataset: Dataset,
        evaluators: List[Callable[[DatasetRecordInputType, JSONType, JSONType], JSONType]],
        project_name: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        config: Optional[ExperimentConfigType] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
        summary_evaluators: Optional[
            List[
                Callable[
                    [List[DatasetRecordInputType], List[JSONType], List[JSONType], Dict[str, List[JSONType]]], JSONType
                ]
            ]
        ] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators or []
        self._description = description
        self._tags: Dict[str, str] = tags or {}
        self._tags["ddtrace.version"] = str(ddtrace.__version__)
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

    def run(self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None) -> ExperimentResult:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )

        project = self._llmobs_instance._dne_client.project_create_or_get(self._project_name)
        self._project_id = project.get("_id", "")

        experiment_id, experiment_run_name = self._llmobs_instance._dne_client.experiment_create(
            self.name,
            self._dataset._id,
            self._project_id,
            self._dataset._version,
            self._config,
            convert_tags_dict_to_list(self._tags),
            self._description,
        )
        self._id = experiment_id
        self._tags["experiment_id"] = str(experiment_id)
        self._run_name = experiment_run_name
        task_results = self._run_task(jobs, raise_errors, sample_size)
        evaluations = self._run_evaluators(task_results, raise_errors=raise_errors)
        summary_evals = self._run_summary_evaluators(task_results, evaluations, raise_errors)
        experiment_results = self._merge_results(task_results, evaluations, summary_evals)
        experiment_evals = self._generate_metrics_from_exp_results(experiment_results)
        self._llmobs_instance._dne_client.experiment_eval_post(
            self._id, experiment_evals, convert_tags_dict_to_list(self._tags)
        )

        return experiment_results

    @property
    def url(self) -> str:
        # FIXME: will not work for subdomain orgs
        return f"{_get_base_url()}/llm/experiments/{self._id}"

    def _process_record(self, idx_record: Tuple[int, DatasetRecord]) -> Optional[TaskResult]:
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
            span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, safe_json(record["expected_output"]))
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

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[TaskResult]:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            return []
        if sample_size is not None and sample_size < len(self._dataset):
            subset_records = [deepcopy(record) for record in self._dataset._records[:sample_size]]
            subset_name = "[Test subset of {} records] {}".format(sample_size, self._dataset.name)
            subset_dataset = Dataset(
                name=subset_name,
                project=self._dataset.project,
                dataset_id=self._dataset._id,
                records=subset_records,
                description=self._dataset.description,
                version=self._dataset._version,
                _dne_client=self._dataset._dne_client,
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

    def _run_evaluators(self, task_results: List[TaskResult], raise_errors: bool = False) -> List[EvaluationResult]:
        evaluations: List[EvaluationResult] = []
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
                        raise RuntimeError(f"Evaluator {evaluator.__name__} failed on row {idx}") from e
                evals_dict[evaluator.__name__] = {"value": eval_result, "error": eval_err}
            evaluation: EvaluationResult = {"idx": idx, "evaluations": evals_dict}
            evaluations.append(evaluation)
        return evaluations

    def _run_summary_evaluators(
        self, task_results: List[TaskResult], eval_results: List[EvaluationResult], raise_errors: bool = False
    ) -> List[EvaluationResult]:
        evaluations: List[EvaluationResult] = []
        inputs: List[DatasetRecordInputType] = []
        outputs: List[JSONType] = []
        expected_outputs: List[JSONType] = []
        evals_dict = {}

        # name of evaluator (not summary evaluator) -> list of eval results ordered by index of the list of task results
        # this is being computed so that the user can use the evaluation results in its original form
        eval_results_by_name: dict[str, List[JSONType]] = {}
        for idx, task_result in enumerate(task_results):
            outputs.append(task_result["output"])
            record: DatasetRecord = self._dataset[idx]
            inputs.append(record["input_data"])
            expected_outputs.append(record["expected_output"])

            eval_result_at_idx_by_name = eval_results[idx]["evaluations"]
            for name, eval_value in eval_result_at_idx_by_name.items():
                if name not in eval_results_by_name:
                    eval_results_by_name[name] = []

                eval_results_by_name[name].append(eval_value.get("value"))

        for idx, summary_evaluator in enumerate(self._summary_evaluators):
            eval_result: JSONType = None
            eval_err: JSONType = None

            try:
                eval_result = summary_evaluator(inputs, outputs, expected_outputs, eval_results_by_name)
            except Exception as e:
                exc_type, exc_value, exc_tb = sys.exc_info()
                exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
                exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                eval_err = {"message": str(exc_value), "type": exc_type_name, "stack": exc_stack}
                if raise_errors:
                    raise RuntimeError(f"Summary evaluator {summary_evaluator.__name__} failed") from e
            evals_dict[summary_evaluator.__name__] = {"value": eval_result, "error": eval_err}
            evaluation: EvaluationResult = {"idx": idx, "evaluations": evals_dict}
            evaluations.append(evaluation)

        return evaluations

    def _merge_results(
        self,
        task_results: List[TaskResult],
        evaluations: List[EvaluationResult],
        summary_evaluations: Optional[List[EvaluationResult]],
    ) -> ExperimentResult:
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

        result: ExperimentResult = {
            "summary_evaluations": summary_evals,
            "rows": experiment_results,
        }
        return result

    def _generate_metric_from_evaluation(
        self,
        eval_name: str,
        eval_value: JSONType,
        err: JSONType,
        span_id: str,
        trace_id: str,
        timestamp_ns: int,
        source: str = "custom",
    ) -> "LLMObsExperimentEvalMetricEvent":
        metric_type = None
        if eval_value is None:
            metric_type = "categorical"
        elif isinstance(eval_value, bool):
            metric_type = "boolean"
        elif isinstance(eval_value, (int, float)):
            metric_type = "score"
        else:
            metric_type = "categorical"
            eval_value = str(eval_value).lower()
        return {
            "metric_source": source,
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp_ms": int(timestamp_ns / 1e6),
            "metric_type": metric_type,
            "label": eval_name,
            f"{metric_type}_value": eval_value,  # type: ignore
            "error": err,
            "tags": convert_tags_dict_to_list(self._tags),
            "experiment_id": self._id,
        }

    def _generate_metrics_from_exp_results(
        self, experiment_result: ExperimentResult
    ) -> List["LLMObsExperimentEvalMetricEvent"]:
        eval_metrics = []
        latest_timestamp: int = 0
        for exp_result in experiment_result["rows"]:
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
                    eval_name, eval_value, eval_data.get("error"), span_id, trace_id, timestamp_ns
                )
                eval_metrics.append(eval_metric)

        for name, summary_eval_data in experiment_result.get("summary_evaluations", {}).items():
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


def _get_base_url() -> str:
    subdomain = ""
    if config._dd_site in DD_SITES_NEEDING_APP_SUBDOMAIN:
        subdomain = "app."

    return f"https://{subdomain}{config._dd_site}"
