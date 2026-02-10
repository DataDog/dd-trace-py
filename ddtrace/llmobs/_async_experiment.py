"""Async experiment implementation for LLM Observability."""

from abc import ABC
from abc import abstractmethod
import asyncio
from copy import deepcopy
import sys
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import DatasetRecordInputType
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import EvaluatorType
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRowResult
from ddtrace.llmobs._experiment import ExperimentRun
from ddtrace.llmobs._experiment import JSONType
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs._experiment import SummaryEvaluatorType
from ddtrace.llmobs._experiment import _create_experiment
from ddtrace.llmobs._experiment import _ExperimentRunInfo
from ddtrace.llmobs._experiment import _get_or_create_project
from ddtrace.llmobs._experiment import _is_class_evaluator
from ddtrace.llmobs._experiment import _is_class_summary_evaluator
from ddtrace.llmobs._experiment import _validate_evaluator_name
from ddtrace.llmobs._utils import convert_tags_dict_to_list
from ddtrace.version import __version__


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentEvalMetricEvent


logger = get_logger(__name__)


# Async task type
AsyncTaskType = Callable[[DatasetRecordInputType, Optional[ConfigType]], Awaitable[JSONType]]


# Async-only evaluator types
AsyncEvaluatorType = Union[
    Callable[[DatasetRecordInputType, JSONType, JSONType], Awaitable[Union[JSONType, EvaluatorResult]]],
    "BaseAsyncEvaluator",
]

AsyncSummaryEvaluatorType = Union[
    Callable[..., Awaitable[JSONType]],
    "BaseAsyncSummaryEvaluator",
]


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


def _is_async_class_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is an async class-based evaluator."""
    return isinstance(evaluator, BaseAsyncEvaluator)


def _is_async_class_summary_evaluator(evaluator: Any) -> bool:
    """Check if an evaluator is an async class-based summary evaluator."""
    return isinstance(evaluator, BaseAsyncSummaryEvaluator)


def _format_error(e: Exception) -> dict[str, JSONType]:
    """Format an exception into an error dictionary."""
    exc_type, exc_value, exc_tb = sys.exc_info()
    exc_type_name = type(e).__name__ if exc_type is not None else "Unknown Exception"
    exc_stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    return {
        "message": str(exc_value),
        "type": exc_type_name,
        "stack": exc_stack,
    }


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


class AsyncExperiment:
    """Async experiment for running tasks and evaluators concurrently."""

    def __init__(
        self,
        name: str,
        task: AsyncTaskType,
        dataset: Dataset,
        evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]],
        project_name: str,
        _llmobs_instance: "LLMObs",
        description: str = "",
        tags: Optional[dict[str, str]] = None,
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
        # Below values are set at experiment run time via API calls
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None
        self._run_name: Optional[str] = None

    async def run(
        self,
        jobs: int = 10,
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
                "and create the experiment via `LLMObs.async_experiment(...)` before running the experiment."
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
            subset_records = [deepcopy(record) for record in self._dataset._records[:sample_size]]
            subset_name = "[Test subset of {} records] {}".format(sample_size, self._dataset.name)
            subset_dataset = Dataset(
                name=subset_name,
                project=self._dataset.project,
                dataset_id=self._dataset._id,
                records=subset_records,
                description=self._dataset.description,
                latest_version=self._dataset._latest_version,
                version=self._dataset._version,
                _dne_client=self._dataset._dne_client,
            )
        else:
            subset_dataset = self._dataset

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(jobs)

        # Run all iterations concurrently
        async def run_iteration(iteration: int) -> ExperimentRun:
            run_info = _ExperimentRunInfo(iteration)
            iteration_tags = {
                **self._tags,
                "run_id": str(run_info._id),
                "run_iteration": str(run_info._run_iteration),
            }

            coros = [
                self._process_record(record, idx, run_info, iteration_tags, semaphore, raise_errors)
                for idx, record in enumerate(subset_dataset)
            ]
            results = await asyncio.gather(*coros, return_exceptions=not raise_errors)

            row_results: list[ExperimentRowResult] = []
            all_metrics: list["LLMObsExperimentEvalMetricEvent"] = []
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

        # Launch all iterations concurrently
        all_runs = list(await asyncio.gather(*[run_iteration(i) for i in range(self._runs)]))

        # Flush spans in case of serverless
        self._llmobs_instance.flush()

        return ExperimentResult(
            summary_evaluations=all_runs[0].summary_evaluations if all_runs else {},
            rows=all_runs[0].rows if all_runs else [],
            runs=all_runs,
        )

    async def _process_record(
        self,
        record: DatasetRecord,
        idx: int,
        run: _ExperimentRunInfo,
        iteration_tags: dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> tuple[ExperimentRowResult, list["LLMObsExperimentEvalMetricEvent"]]:
        """Process a single record: run task, run evaluators, build metrics."""
        metrics: list["LLMObsExperimentEvalMetricEvent"] = []

        # Run task (with semaphore)
        task_output, span_id, trace_id, timestamp, task_error = await self._run_task_for_record(
            record, idx, run, iteration_tags, semaphore, raise_errors
        )

        # Run evaluators (each with semaphore)
        evaluations: dict[str, dict[str, JSONType]] = {}
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
        iteration_tags: dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> tuple[JSONType, str, str, int, dict[str, Optional[str]]]:
        """Run the task for a single record."""
        from ddtrace.constants import ERROR_MSG
        from ddtrace.constants import ERROR_STACK
        from ddtrace.constants import ERROR_TYPE
        from ddtrace.llmobs._constants import EXPERIMENT_EXPECTED_OUTPUT
        from ddtrace.llmobs._constants import EXPERIMENT_RECORD_METADATA

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
                    output_data = await self._task(input_data, self._config)
                except Exception:
                    span.set_exc_info(*sys.exc_info())
                    if raise_errors:
                        exc_type, exc_value, exc_tb = sys.exc_info()
                        raise RuntimeError(f"Error on record {idx}: {exc_value}\n{exc_type}\n{exc_tb}") from exc_value

                self._llmobs_instance.annotate(span, input_data=input_data, output_data=output_data, tags=tags)

                span._set_ctx_item(EXPERIMENT_EXPECTED_OUTPUT, record["expected_output"])
                if "metadata" in record:
                    span._set_ctx_item(EXPERIMENT_RECORD_METADATA, record["metadata"])

                error_dict: dict[str, Optional[str]] = {
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
    ) -> dict[str, JSONType]:
        """Run a single evaluator, handling sync/async transparently."""
        async with semaphore:
            input_data = record["input_data"]
            expected_output = record["expected_output"]
            metadata = record.get("metadata", {})
            combined_metadata = {**metadata, "experiment_config": self._config}

            eval_result_value: JSONType = None
            eval_err: JSONType = None
            extra_return_values: dict[str, JSONType] = {}

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
                    eval_result = await asyncio.to_thread(evaluator, input_data, output, expected_output)  # type: ignore[arg-type]

                # Extract EvaluatorResult if applicable
                if isinstance(eval_result, EvaluatorResult):
                    if eval_result.reasoning:
                        extra_return_values["reasoning"] = eval_result.reasoning
                    if eval_result.assessment:
                        extra_return_values["assessment"] = eval_result.assessment
                    if eval_result.metadata:
                        extra_return_values["metadata"] = eval_result.metadata
                    if eval_result.tags:
                        extra_return_values["tags"] = eval_result.tags
                    eval_result_value = eval_result.value
                else:
                    eval_result_value = eval_result

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
        row_results: list[ExperimentRowResult],
        iteration_tags: dict[str, str],
        semaphore: asyncio.Semaphore,
        raise_errors: bool,
    ) -> tuple[dict[str, dict[str, JSONType]], list["LLMObsExperimentEvalMetricEvent"]]:
        """Run summary evaluators on aggregated results."""
        if not self._summary_evaluators:
            return {}, []

        # Build aggregated data
        inputs: list[DatasetRecordInputType] = []
        outputs: list[JSONType] = []
        expected_outputs: list[JSONType] = []
        metadata_list: list[dict[str, Any]] = []
        eval_results_by_name: dict[str, list[JSONType]] = {}

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
        summary_evals: dict[str, dict[str, JSONType]] = {}
        metrics: list["LLMObsExperimentEvalMetricEvent"] = []

        async def run_single_summary_evaluator(
            summary_evaluator: Union[SummaryEvaluatorType, AsyncSummaryEvaluatorType],
        ) -> tuple[str, dict[str, JSONType]]:
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
                        eval_result_value = await asyncio.to_thread(summary_evaluator.evaluate, context)  # type: ignore[union-attr, arg-type]
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
        self, record: DatasetRecord, idx: int, iteration_tags: dict[str, str]
    ) -> dict[str, JSONType]:
        """Build metadata for a row result."""
        tags: list[JSONType] = list(convert_tags_dict_to_list(iteration_tags))
        return {
            "tags": tags,
            "dataset_record_index": idx,
            "experiment_name": self.name,
            "dataset_name": self._dataset.name,
        }

    def _build_metric_event(
        self,
        evaluator_name: str,
        eval_result: dict[str, JSONType],
        span_id: str,
        trace_id: str,
        timestamp: int,
        metric_source: str,
        iteration_tags: dict[str, str],
    ) -> "LLMObsExperimentEvalMetricEvent":
        """Build a metric event from an evaluation result."""
        if self._id is None:
            raise ValueError("Experiment ID is not set")

        value = eval_result.get("value")

        # Infer metric type
        if value is None:
            metric_type = "categorical"
            typed_value: Any = value
        elif isinstance(value, bool):
            metric_type = "boolean"
            typed_value = value
        elif isinstance(value, (int, float)):
            metric_type = "score"
            typed_value = value
        elif isinstance(value, dict):
            metric_type = "json"
            typed_value = value
        else:
            metric_type = "categorical"
            typed_value = str(value).lower()

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
