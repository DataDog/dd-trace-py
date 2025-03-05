import concurrent.futures
from copy import deepcopy
import json
import os
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Iterator, TYPE_CHECKING

import ddtrace

from ._dataset import Dataset
from .utils._http import exp_http_request
from ._config import (
    get_project_name,
    _validate_init,
    _is_locally_initialized,
    DEFAULT_CONCURRENT_JOBS,
    DEFAULT_CHUNK_SIZE,
    get_base_url,
)
from .utils._ui import Color, ProgressReporter, _print_progress_bar
from .._llmobs import LLMObs

if TYPE_CHECKING:
    import pandas as pd


class Experiment:
    """
    Manages the execution and evaluation of tasks over a Dataset.

    This class ties together a dataset, a task function, and a set of evaluators,
    providing methods to run tasks concurrently, evaluate results, and persist them
    to Datadog for analysis.

    Attributes:
        name (str): Name of the experiment.
        task (Callable): Function that processes each dataset record (decorated with @task).
        dataset (Dataset): Dataset to run the experiment on.
        evaluators (List[Callable]): List of evaluation functions (decorated with @evaluator).
        tags (List[str]): Tags useful for categorizing or querying the experiment.
        description (str): Description of the experiment.
        metadata (Dict[str, Any]): Additional metadata about the experiment.
        config (Optional[Dict[str, Any]]): Configuration passed to the task, if it accepts it.
        has_run (bool): Indicates whether the experiment task has been executed.
        has_evaluated (bool): Indicates whether the experiment evaluations have been performed.
        outputs (List[Dict]): Stores outputs after running the task.
        evaluations (List[Dict]): Stores evaluation results after running evaluators.
    """

    def __init__(
        self,
        name: str,
        task: Callable,
        dataset: Dataset,
        evaluators: List[Callable],
        tags: Optional[List[str]] = None,
        description: str = "",
        metadata: Dict[str, Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize an Experiment that will run a task on a Dataset and evaluate it with evaluators.

        Args:
            name (str): Name of the experiment.
            task (Callable): Callable decorated with @task that processes each record.
            dataset (Dataset): The dataset over which the task will be run.
            evaluators (List[Callable]): List of callables decorated with @evaluator.
            tags (List[str], optional): Tags for categorization of the experiment. Defaults to None.
            description (str, optional): Description of the experiment. Defaults to "".
            metadata (Dict[str, Any], optional): Additional metadata about the experiment. Defaults to None.
            config (Dict[str, Any], optional): Configuration passed to the task, if it accepts 'config'. Defaults to None.

        Raises:
            TypeError: If either the task or any of the evaluators are not properly decorated.
        """
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators
        self.tags = tags if tags is not None else []
        self.project_name = get_project_name()
        self.description = description
        self.metadata = metadata if metadata is not None else {}
        self.config = config

        # Make sure the task is decorated with @task
        if not hasattr(self.task, "_is_task"):
            raise TypeError("Task function must be decorated with @task decorator.")

        # Make sure every evaluator is decorated with @evaluator
        for evaluator_func in self.evaluators:
            if not hasattr(evaluator_func, "_is_evaluator"):
                raise TypeError(f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator.")

        # Post-run attributes
        self.has_run = False
        self.has_evaluated = False
        self.outputs = []
        self.evaluations = []

        # We'll store the experiment's Datadog ID once it's created.
        self._datadog_experiment_id: Optional[str] = None
        self._datadog_project_id: Optional[str] = None

    def _get_or_create_project(self) -> str:
        """
        Retrieve or create a project in Datadog based on the global 'project_name'.

        Returns:
            str: The Datadog project ID for the current project name.

        Raises:
            ValueError: If any HTTP request fails or returns invalid data.
        """
        url = f"/api/unstable/llm-obs/v1/projects?filter[name]={self.project_name}"
        resp = exp_http_request("GET", url)
        response_data = resp.json()
        projects = response_data.get("data", [])

        if not projects:
            # Create new project
            project_payload = {
                "data": {
                    "type": "projects",
                    "attributes": {
                        "name": self.project_name,
                        "description": "",
                    },
                }
            }
            resp = exp_http_request(
                "POST",
                "/api/unstable/llm-obs/v1/projects",
                body=json.dumps(project_payload).encode("utf-8"),
            )
            response_data = resp.json()
            return response_data["data"]["id"]

        return projects[0]["id"]

    def _create_experiment_in_datadog(self) -> str:
        """
        Create a new experiment in Datadog, tied to the dataset in this Experiment.

        Returns:
            str: The new Datadog experiment ID.

        Raises:
            ValueError: If the dataset is not yet pushed to Datadog (missing _datadog_dataset_id).
        """
        if not self.dataset._datadog_dataset_id:
            raise ValueError(
                "Dataset must be pushed to Datadog (so it has an ID) before creating an experiment. "
                "Please call dataset.push() first."
            )

        project_id = self._get_or_create_project()

        experiment_payload = {
            "data": {
                "type": "experiments",
                "attributes": {
                    "name": self.name,
                    "description": self.description,
                    "dataset_id": self.dataset._datadog_dataset_id,
                    "project_id": project_id,
                    "dataset_version": self.dataset._version,
                    "metadata": {
                        "tags": self.tags,
                        **(self.metadata or {}),
                        "config": self.config,
                    },
                    "ensure_unique": True,
                },
            }
        }
        resp = exp_http_request(
            "POST",
            "/api/unstable/llm-obs/v1/experiments",
            body=json.dumps(experiment_payload).encode("utf-8"),
        )
        response_data = resp.json()
        experiment_id = response_data["data"]["id"]

        # The API may rename the experiment (e.g., adding a suffix), so update local name:
        self.name = response_data["data"]["attributes"]["name"]
        return experiment_id

    def run(
        self,
        jobs: int = DEFAULT_CONCURRENT_JOBS,
        raise_errors: bool = False,
        sample_size: Optional[int] = None,
    ) -> "ExperimentResults":
        """
        Execute the entire experiment by running the task on each record (optionally on a subset),
        annotating results, and then evaluating them with the configured evaluators.

        Args:
            jobs (int, optional): Number of concurrent threads to use for the task. Defaults to 10.
            raise_errors (bool, optional): If True, raises an exception upon the first error encountered
                instead of continuing through all records. Defaults to False.
            sample_size (int, optional): If provided, only runs on the first 'sample_size' records from the dataset.
                Useful for quick tests without processing the entire dataset.

        Returns:
            ExperimentResults: An object that contains and manages the results of the task execution and evaluations.

        Raises:
            ValueError: If the dataset is not pushed to Datadog (when not running locally).
            RuntimeError: If raise_errors is True and an error occurs while processing any record.
        """
        if not _is_locally_initialized():
            _validate_init()

        if sample_size is not None:
            print(f"\n{Color.BOLD}Running experiment (subset of {sample_size} rows){Color.RESET}")
        else:
            print(f"\n{Color.BOLD}Running experiment{Color.RESET}")
        print(f"  Project: {Color.CYAN}{self.project_name}{Color.RESET}")
        print(f"  Name: {Color.CYAN}{self.name}{Color.RESET}")
        print(f"  raise_errors={raise_errors}\n")

        # 1) Make sure the dataset is pushed if not running locally
        if not _is_locally_initialized():
            if not self.dataset._datadog_dataset_id:
                raise ValueError("Dataset must be pushed to Datadog before running the experiment.")

        # 2) Create/get the project, then create an experiment in Datadog.
        if not _is_locally_initialized():
            project_id = self._get_or_create_project()
            self._datadog_project_id = project_id
            experiment_id = self._create_experiment_in_datadog()
            self._datadog_experiment_id = experiment_id

        # If a sample_size is given, slice the dataset
        if sample_size is not None and sample_size < len(self.dataset._data):
            subset_data = [deepcopy(r) for r in self.dataset._data[:sample_size]]
            subset_dataset = Dataset(
                name=f"{self.dataset.name}_test_subset",
                data=subset_data,
                description=f"[Test subset of {sample_size}] {self.dataset.description}",
            )
        else:
            subset_dataset = self.dataset

        # -- Run the "task" portion on the subset:
        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "True"
        total_rows = len(subset_dataset)
        progress = ProgressReporter(total_rows, desc="Processing")
        outputs_buffer = []

        def process_row(idx_row):
            idx, row = idx_row
            start_time = time.time()
            with LLMObs._experiment(name=self.task.__name__, experiment_id=self._datadog_experiment_id) as span:
                span.context.set_baggage_item("is_experiment_task", True)
                span_context = LLMObs.export_span(span=span)
                span_id = span_context["span_id"]
                trace_id = span_context["trace_id"]

                input_data = row["input"]
                expected_output = row["expected_output"]

                try:
                    if getattr(self.task, "_accepts_config", False):
                        output = self.task(input_data, self.config)
                    else:
                        output = self.task(input_data)

                    LLMObs.annotate(
                        span,
                        input_data=input_data,
                        output_data=output,
                        tags={
                            "dataset_id": self.dataset._datadog_dataset_id,
                            "dataset_record_id": row.get("record_id"),
                            "experiment_id": self._datadog_experiment_id,
                        },
                    )
                    LLMObs._tag_expected_output(span, expected_output)

                    return {
                        "idx": idx,
                        "output": output,
                        "metadata": {
                            "timestamp": start_time,
                            "duration": time.time() - start_time,
                            "dataset_record_index": idx,
                            "project_name": self.project_name,
                            "experiment_name": self.name,
                            "dataset_name": self.dataset.name,
                            "span_id": span_id,
                            "trace_id": trace_id,
                        },
                        "error": {"message": None, "stack": None, "type": None},
                    }
                except Exception as e:
                    error_message = str(e)
                    span.error = 1
                    span.set_exc_info(type(e), e, e.__traceback__)

                    LLMObs.annotate(
                        span,
                        input_data=input_data,
                        tags={
                            "dataset_id": self.dataset._datadog_dataset_id,
                            "dataset_record_id": row.get("record_id"),
                            "experiment_id": self._datadog_experiment_id,
                        },
                    )
                    LLMObs._tag_expected_output(span, expected_output)

                    return {
                        "idx": idx,
                        "output": None,
                        "metadata": {
                            "timestamp": start_time,
                            "duration": time.time() - start_time,
                            "dataset_record_index": idx,
                            "project_name": self.project_name,
                            "experiment_name": self.name,
                            "dataset_name": self.dataset.name,
                            "span_id": span_id,
                            "trace_id": trace_id,
                        },
                        "error": {
                            "message": error_message,
                            "stack": traceback.format_exc(),
                            "type": type(e).__name__,
                        },
                    }

        _jobs = DEFAULT_CONCURRENT_JOBS if sample_size else jobs
        with concurrent.futures.ThreadPoolExecutor(max_workers=_jobs) as executor:
            for result in executor.map(process_row, enumerate(subset_dataset)):
                outputs_buffer.append(result)
                progress.update(error=result["error"]["message"] is not None)
                if raise_errors and result["error"]["message"]:
                    raise RuntimeError(
                        f"Error on record {result['idx']}: {result['error']['message']}\n"
                        f"Stack Trace:\n{result['error']['stack']}"
                    )

        LLMObs.flush()
        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "False"
        os.environ["DD_LLMOBS_ENABLED"] = "False"

        if not outputs_buffer:
            raise ValueError("No outputs were produced, cannot evaluate.")

        # -- Run evaluations on the subset outputs:
        print(f"\nEvaluating {len(outputs_buffer)} rows...\n")
        evaluators_to_use = self.evaluators
        progress_eval = ProgressReporter(len(outputs_buffer), desc="Evaluating")
        evaluations_partial = []

        for idx, output_data in enumerate(outputs_buffer):
            output = output_data["output"]
            dataset_row = subset_dataset[idx]
            input_data = dataset_row.get("input", {})
            expected_output = dataset_row.get("expected_output", {})

            evaluations_dict = {}
            for evaluator_func in evaluators_to_use:
                if not hasattr(evaluator_func, "_is_evaluator"):
                    raise TypeError(
                        f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator."
                    )
                try:
                    evaluation_result = evaluator_func(input_data, output, expected_output)
                    evaluations_dict[evaluator_func.__name__] = {"value": evaluation_result, "error": None}
                except Exception as e:
                    evaluations_dict[evaluator_func.__name__] = {
                        "value": None,
                        "error": {
                            "message": str(e),
                            "type": type(e).__name__,
                            "stack": traceback.format_exc(),
                        },
                    }
                    if raise_errors:
                        raise RuntimeError(f"Evaluator '{evaluator_func.__name__}' failed on row {idx}: {e}") from e

            evaluations_partial.append({"idx": idx, "evaluations": evaluations_dict})
            progress_eval.update(error=any(e["error"] is not None for e in evaluations_dict.values()))

        # If sample_size was not used, store these as the main experiment outputs/evals:
        if sample_size is None:
            self.outputs = outputs_buffer
            self.has_run = True

        # Build/attach an ExperimentResults
        experiment_results = ExperimentResults(
            dataset=subset_dataset,
            experiment=self,
            outputs=outputs_buffer,
            evaluations=evaluations_partial,
        )
        self.has_evaluated = True
        print(f"\n{Color.RESET} Run complete.\n")
        return experiment_results

    def run_task(self, jobs: int = DEFAULT_CONCURRENT_JOBS, raise_errors: bool = False) -> None:
        """
        Execute only the task function on the dataset concurrently, without running the evaluators.

        Args:
            jobs (int, optional): Number of concurrent threads to use. Defaults to 10.
            raise_errors (bool, optional): If True, raises an exception upon the first error. Defaults to False.

        Raises:
            ValueError: If Datadog environment is not initialized.
            RuntimeError: If raise_errors is True and an error occurs while processing a record.
        """
        if not _is_locally_initialized():
            _validate_init()
        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "True"
        total_rows = len(self.dataset)

        progress = ProgressReporter(total_rows, desc="Processing")
        outputs_buffer = []

        def process_row(idx_row):
            idx, row = idx_row
            start_time = time.time()
            with LLMObs._experiment(name=self.task.__name__, experiment_id=self._datadog_experiment_id) as span:
                span.context.set_baggage_item("is_experiment_task", True)
                span_context = LLMObs.export_span(span=span)
                span_id = span_context["span_id"]
                trace_id = span_context["trace_id"]

                input_data = row["input"]
                expected_output = row["expected_output"]

                try:
                    if getattr(self.task, "_accepts_config", False):
                        output = self.task(input_data, self.config)
                    else:
                        output = self.task(input_data)

                    LLMObs.annotate(
                        span,
                        input_data=input_data,
                        output_data=output,
                        tags={
                            "dataset_id": self.dataset._datadog_dataset_id,
                            "dataset_record_id": row["record_id"],
                            "experiment_id": self._datadog_experiment_id,
                        },
                    )
                    LLMObs._tag_expected_output(span, expected_output)

                    if idx % 30 == 0:
                        LLMObs.flush()

                    return {
                        "idx": idx,
                        "output": output,
                        "metadata": {
                            "timestamp": start_time,
                            "duration": time.time() - start_time,
                            "dataset_record_index": idx,
                            "project_name": self.project_name,
                            "experiment_name": self.name,
                            "dataset_name": self.dataset.name,
                            "span_id": span_id,
                            "trace_id": trace_id,
                        },
                        "error": {"message": None, "stack": None, "type": None},
                    }
                except Exception as e:
                    error_message = str(e)
                    span.error = 1
                    span.set_exc_info(type(e), e, e.__traceback__)

                    LLMObs.annotate(
                        span,
                        input_data=input_data,
                        tags={
                            "dataset_id": self.dataset._datadog_dataset_id,
                            "dataset_record_id": row["record_id"],
                            "experiment_id": self._datadog_experiment_id,
                        },
                    )
                    LLMObs._tag_expected_output(span, expected_output)

                    if idx % 30 == 0:
                        LLMObs.flush()

                    return {
                        "idx": idx,
                        "output": None,
                        "metadata": {
                            "timestamp": start_time,
                            "duration": time.time() - start_time,
                            "dataset_record_index": idx,
                            "project_name": self.project_name,
                            "experiment_name": self.name,
                            "dataset_name": self.dataset.name,
                            "span_id": span_id,
                            "trace_id": trace_id,
                        },
                        "error": {
                            "message": error_message,
                            "stack": traceback.format_exc(),
                            "type": type(e).__name__,
                        },
                    }

        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            for result in executor.map(process_row, list(enumerate(self.dataset))):
                outputs_buffer.append(result)
                progress.update(error=result["error"]["message"] is not None)

                # Raise the first error encountered if raise_errors is True
                if raise_errors and result["error"]["message"]:
                    raise RuntimeError(
                        f"Error on record {result['idx']}: {result['error']['message']}\n"
                        f"Stack trace:\n{result['error']['stack']}"
                    )

        self.outputs = outputs_buffer
        self.has_run = True
        LLMObs.flush()

        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "False"
        os.environ["DD_LLMOBS_ENABLED"] = "False"

    def run_evaluations(
        self, evaluators: Optional[List[Callable]] = None, raise_errors: bool = False
    ) -> "ExperimentResults":
        """
        Execute evaluators on the already-run experiment outputs, returning an ExperimentResults object.

        This method allows you to run just the evaluation step after the task has been run on the dataset.

        Args:
            evaluators (List[Callable], optional): If provided, override the experiment's evaluators
                for this run. Must be decorated with @evaluator. Defaults to None.
            raise_errors (bool, optional): If True, raises exceptions encountered during evaluation.

        Returns:
            ExperimentResults: A new ExperimentResults instance with the evaluation results.

        Raises:
            ValueError: If the task has not been run yet
        """
        if not _is_locally_initialized():
            _validate_init()

        if not self.has_run:
            raise ValueError("Task has not been run yet. Please call run_task() before run_evaluations().")

        # Use provided evaluators or fall back to experiment's evaluators
        evaluators_to_use = evaluators if evaluators is not None else self.evaluators

        # Validate that all evaluators have the @evaluator decorator
        for evaluator_func in evaluators_to_use:
            if not hasattr(evaluator_func, "_is_evaluator"):
                raise TypeError(f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator.")

        evaluations = []
        total_rows = len(self.outputs)

        progress = ProgressReporter(total_rows, desc="Evaluating")

        for idx, output_data in enumerate(self.outputs):
            output = output_data["output"]
            dataset_row = self.dataset[idx]
            input_data = dataset_row.get("input", {})
            expected_output = dataset_row.get("expected_output", {})

            evaluations_dict = {}

            # Run all evaluators for this output
            for evaluator in evaluators_to_use:
                try:
                    evaluation_result = evaluator(input_data, output, expected_output)
                    evaluations_dict[evaluator.__name__] = {"value": evaluation_result, "error": None}
                except Exception as e:
                    evaluations_dict[evaluator.__name__] = {
                        "value": None,
                        "error": {
                            "message": str(e),
                            "type": type(e).__name__,
                            "stack": traceback.format_exc(),
                        },
                    }
                    if raise_errors:
                        raise e

            # Add single evaluation entry for this output
            evaluations.append({"idx": idx, "evaluations": evaluations_dict})

            progress.update(error=any(e["error"] is not None for e in evaluations_dict.values()))

        self.has_evaluated = True
        experiment_results = ExperimentResults(self.dataset, self, self.outputs, evaluations)
        experiment_results._push_evals()
        return experiment_results

    def __repr__(self) -> str:
        """
        Return a comprehensive string representation of the Experiment,
        including its name, project, task, dataset, evaluators, tags, and run status.

        Returns:
            str: A rich, color-coded representation helpful in debugging or logging.
        """
        name = f"{Color.CYAN}{self.name}{Color.RESET}"
        project = f"{Color.CYAN}{self.project_name}{Color.RESET}"

        task_name = self.task.__name__
        task_doc = self.task.__doc__
        task_preview = f"{task_name}"
        if task_doc:
            # Get first line of docstring
            first_line = task_doc.splitlines()[0].strip()
            task_preview += f" ({first_line})"

        dataset_info = f"{self.dataset.name} ({len(self.dataset):,} records)"
        evaluator_names = [e.__name__ for e in self.evaluators]
        evaluator_info = f"{len(evaluator_names)} evaluator{'s' if len(evaluator_names) != 1 else ''}"

        config_preview = ""
        if self.config:
            items = list(self.config.items())[:3]
            preview = ", ".join(f"{k}: {repr(v)}" for k, v in items)
            if len(self.config) > 3:
                preview += f" + {len(self.config) - 3} more"
            config_preview = f"\n  Config: {preview}"

        tags_info = ""
        if self.tags:
            tags = " ".join(f"{Color.GREY}#{tag}{Color.RESET}" for tag in self.tags)
            tags_info = f"\n  Tags: {tags}"

        status_indicators = []

        if self.has_run:
            run_status = f"{Color.GREEN}✓ Run complete{Color.RESET}"
            if self.outputs:
                errors = sum(1 for o in self.outputs if o.get("error", {}).get("message"))
            if errors:
                error_rate = (errors / len(self.outputs)) * 100
                run_status += f" ({Color.RED}{errors:,} errors{Color.RESET}, {error_rate:.1f}%)"
        else:
            run_status = f"{Color.YELLOW}Not run{Color.RESET}"
        status_indicators.append(run_status)

        if self.has_evaluated:
            eval_status = f"{Color.GREEN}✓ Evaluated{Color.RESET}"
        elif self.has_run:
            eval_status = f"{Color.YELLOW}Not evaluated{Color.RESET}"
        else:
            eval_status = f"{Color.DIM}Pending run{Color.RESET}"
        status_indicators.append(eval_status)

        if self._datadog_experiment_id:
            dd_status = f"{Color.GREEN}✓ Synced{Color.RESET}"
            dd_url = f"\n  URL: {Color.BLUE}{get_base_url()}/llm/testing/experiments/{self._datadog_experiment_id}{Color.RESET}"
        else:
            dd_status = f"{Color.YELLOW}Local only{Color.RESET}"
            dd_url = ""
        status_indicators.append(dd_status)

        desc_info = ""
        if self.description:
            desc_preview = (self.description[:47] + "...") if len(self.description) > 50 else self.description
            desc_info = f"\n  Description: {desc_preview}"

        info = [
            f"Experiment(name={name})",
            f"  Project: {project}",
            f"  Task: {task_preview}",
            f"  Dataset: {dataset_info}",
            f"  Evaluators: {evaluator_info} ({', '.join(evaluator_names)})",
            f"  Status: {' | '.join(status_indicators)}",
        ]

        if desc_info:
            info.insert(2, desc_info)
        if config_preview:
            info.append(config_preview)
        if tags_info:
            info.append(tags_info)
        if dd_url:
            info.append(dd_url)

        return "\n".join(info)


class ExperimentResults:
    """
    Contains and manages the results (both outputs and evaluations) after an Experiment is run.

    This class merges the observed outputs and evaluation metrics for each record in the dataset,
    providing methods to convert these merged results into a DataFrame, export them, or push them to Datadog.

    Attributes:
        dataset (Dataset): The dataset used in the experiment.
        experiment (Experiment): The experiment that generated these results.
        outputs (List[Dict]): Outputs after running the task on the dataset.
        evaluations (List[Dict]): Evaluation results after running evaluators on the outputs.
        merged_results (List[Dict]): A combined list of outputs + evaluations for each record.
    """

    def __init__(self, dataset: Dataset, experiment: Experiment, outputs: List[Dict], evaluations: List[Dict]) -> None:
        """
        Initialize an ExperimentResults object, merging outputs and evaluations for each dataset record.

        Args:
            dataset (Dataset): The dataset used for the experiment.
            experiment (Experiment): The experiment instance that produced these results.
            outputs (List[Dict]): List of dictionaries containing output data for each record.
            evaluations (List[Dict]): List of dictionaries containing evaluation data for each record.
        """
        self.dataset = dataset
        self.experiment = experiment
        self.outputs = outputs
        self.evaluations = evaluations
        self.merged_results = self._merge_results()

    def _merge_results(self) -> List[Dict[str, Any]]:
        """
        Merge outputs and evaluations into a single list of dictionaries, one per dataset record.

        Returns:
            List[Dict[str, Any]]: The combined data, each containing input, output, evaluations, and metadata.
        """
        merged_results = []
        for idx in range(len(self.outputs)):
            output_data = self.outputs[idx]
            evaluation_data = self.evaluations[idx]
            dataset_record = self.dataset._data[idx]

            metadata = output_data.get("metadata", {})
            metadata["tags"] = self.experiment.tags

            merged_result = {
                "idx": idx,
                "record_id": dataset_record.get("record_id"),
                "input": dataset_record.get("input", {}),
                "expected_output": dataset_record.get("expected_output", {}),
                "output": output_data.get("output"),
                "evaluations": evaluation_data.get("evaluations", {}),
                "metadata": metadata,
                "error": output_data.get("error"),
            }
            merged_results.append(merged_result)
        return merged_results

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """
        Enable iteration over merged experiment results.

        Yields:
            Iterator[Dict[str, Any]]: An iterator of merged result dictionaries.
        """
        return iter(self.merged_results)

    def __len__(self) -> int:
        """
        Return the number of merged results (should be equal to the number of records in the dataset).

        Returns:
            int: Number of merged results.
        """
        return len(self.merged_results)

    def __getitem__(self, index: int) -> Any:
        """
        Retrieve a specific merged result by index.

        Args:
            index (int): Index of the merged result to retrieve.

        Returns:
            Dict[str, Any]: A dictionary containing input, output, evaluations, metadata, and error info.
        """
        result = self.merged_results[index].copy()
        return result

    def as_dataframe(self, multiindex: bool = True) -> "pd.DataFrame":
        """
        Convert the experiment results into a pandas DataFrame for analysis and visualization.

        Args:
            multiindex (bool, optional): If True, expand input, output, and expected_output dictionaries
                into separate columns using a MultiIndex. If False, keep the nested dictionaries as they are.

        Returns:
            pd.DataFrame: DataFrame representation of the experiment results.

        Raises:
            ImportError: If pandas is not installed.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required to convert experiment results to DataFrame. "
                "Please install it with `pip install pandas`"
            )

        df = pd.DataFrame(self.merged_results)

        if not multiindex:
            return df

        special_fields = ["input", "output", "expected_output"]
        result_dfs = []

        for field in special_fields:
            if field not in df.columns:
                continue

            # Get the first non-null value to check type
            first_value = next((v for v in df[field] if v is not None), None)

            if isinstance(first_value, dict):
                field_df = pd.json_normalize(df[field].values)
            else:
                field_df = pd.DataFrame({"value": df[field].values})

            field_df.columns = pd.MultiIndex.from_tuples([(field, col) for col in field_df.columns])
            result_dfs.append(field_df)

        other_cols = [col for col in df.columns if col not in special_fields]
        if other_cols:
            other_df = df[other_cols]
            result_dfs.append(other_df)

        final_df = pd.concat(result_dfs, axis=1)
        final_df = final_df.where(pd.notna(final_df), None)

        return final_df

    def _push_evals(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        """
        Push the experiment evaluations (metrics) to Datadog for further analysis.

        Args:
            chunk_size (int, optional): Number of records to push per HTTP request batch. Defaults to 300.

        Raises:
            ValueError: If the dataset or experiment is not synchronized with Datadog.
        """
        _validate_init()

        if not self.experiment.dataset._datadog_dataset_id:
            raise ValueError(
                "Dataset has not been pushed to Datadog. "
                "Please call dataset.push() before pushing experiment results."
            )

        if not self.experiment._datadog_experiment_id:
            raise ValueError(
                "Experiment has not been created in Datadog. Please call experiment.run() before pushing results."
            )

        experiment_id = self.experiment._datadog_experiment_id
        project_id = self.experiment._datadog_project_id
        experiment_name = self.experiment.name

        total_results = len(self.merged_results)
        show_progress = total_results > chunk_size

        chunks = [self.merged_results[i : i + chunk_size] for i in range(0, total_results, chunk_size)]
        total_chunks = len(chunks)

        if show_progress:
            print(f"\nUploading {total_results} results in {total_chunks} chunks...")
            _print_progress_bar(0, total_chunks, prefix="Uploading:", suffix="Complete")

        for chunk_idx, chunk in enumerate(chunks):
            spans: List[Dict[str, Any]] = []
            metrics: List[Dict[str, Any]] = []

            for result in chunk:
                idx = result["idx"]
                merged_result = result
                output = merged_result.get("output")
                record_id = merged_result.get("record_id")
                input_data = merged_result.get("input", {})
                evaluations = merged_result.get("evaluations", {})
                expected_output = merged_result.get("expected_output", {})
                error = merged_result.get("error", {})
                metadata = merged_result.get("metadata", {})
                span_id = metadata.get("span_id")
                trace_id = metadata.get("trace_id")

                # Add evaluation metrics
                for metric_payload_name, metric_payload_value in evaluations.items():
                    # Skip None values
                    if metric_payload_value is None:
                        print(f"Skipping None value for metric: {metric_payload_name}")
                        continue

                    timestamp_ms = int(metadata.get("timestamp", time.time()) * 1000)

                    if metric_payload_value["value"] == None:
                        metric_type = "categorical"
                        metric_value = None
                    # Check for bool first, since bool is a subclass of int
                    elif isinstance(metric_payload_value["value"], (bool, str)):
                        metric_type = "categorical"
                        metric_value = str(metric_payload_value["value"]).lower()
                    elif isinstance(metric_payload_value["value"], (int, float)):
                        metric_type = "score"
                        metric_value = metric_payload_value["value"]
                    else:
                        metric_type = "categorical"
                        metric_value = str(metric_payload_value["value"])

                    metric = {
                        "span_id": str(span_id),
                        "trace_id": str(trace_id),
                        "metric_type": metric_type,
                        "timestamp_ms": timestamp_ms,
                        "label": metric_payload_name,
                        "score_value" if metric_type == "score" else "categorical_value": metric_value,
                        "error": metric_payload_value["error"],
                    }

                    metrics.append(metric)

            chunk_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "scope": "experiments",
                        "metrics": metrics,
                        "tags": self.experiment.tags
                        + ["ddtrace.version:" + ddtrace.__version__, "experiment_id:" + experiment_id],
                    },
                }
            }

            url = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
            exp_http_request("POST", url, body=json.dumps(chunk_payload).encode("utf-8"))

            if show_progress:
                _print_progress_bar(chunk_idx + 1, total_chunks, prefix="Uploading:", suffix="Complete")

        print(f"\n{Color.GREEN}✓ Experiment '{experiment_name}' results pushed to Datadog{Color.RESET}")
        print(f"{Color.BLUE}  {get_base_url()}/llm/testing/experiments/{experiment_id}{Color.RESET}\n")

    def __repr__(self) -> str:
        """
        Return a detailed, color-coded string summary of the experiment results, including
        error counts, evaluator statistics, and Datadog links if available.

        Returns:
            str: A descriptive string representing the experiment results.
        """
        exp_name = f"{Color.CYAN}{self.experiment.name}{Color.RESET}"
        dataset_name = f"{Color.CYAN}{self.dataset.name}{Color.RESET}"

        total_records = len(self.merged_results)
        errors = sum(1 for r in self.merged_results if r.get("error", {}).get("message"))
        error_rate = (errors / total_records) * 100 if total_records > 0 else 0

        evaluator_names = set()
        eval_stats = {}

        for result in self.merged_results:
            evals = result.get("evaluations", {})
            for eval_name, eval_data in evals.items():
                evaluator_names.add(eval_name)

                if eval_name not in eval_stats:
                    eval_stats[eval_name] = {"count": 0, "errors": 0, "values": []}

                eval_stats[eval_name]["count"] += 1
                if eval_data.get("error"):
                    eval_stats[eval_name]["errors"] += 1

                value = eval_data.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    eval_stats[eval_name]["values"].append(value)

        eval_info = []
        for name in sorted(evaluator_names):
            stats = eval_stats[name]
            eval_error_rate = (stats["errors"] / stats["count"]) * 100 if stats["count"] > 0 else 0

            if stats["values"]:
                values = stats["values"]
                avg = sum(values) / len(values) if values else 0
                min_val = min(values) if values else 0
                max_val = max(values) if values else 0
                if eval_error_rate > 0:
                    eval_info.append(
                        f"    {name}: avg={avg:.2f} min={min_val:.2f} max={max_val:.2f} "
                        f"({Color.RED}{stats['errors']} errors, {eval_error_rate:.1f}%{Color.RESET})"
                    )
                else:
                    eval_info.append(f"    {name}: avg={avg:.2f} min={min_val:.2f} max={max_val:.2f}")
            else:
                if eval_error_rate > 0:
                    eval_info.append(
                        f"    {name}: {stats['count']} evaluations "
                        f"({Color.RED}{stats['errors']} errors, {eval_error_rate:.1f}%{Color.RESET})"
                    )
                else:
                    eval_info.append(f"    {name}: {stats['count']} evaluations")

        # Format execution time if available
        durations: List[float] = []
        for r in self.merged_results:
            duration = r.get("metadata", {}).get("duration", 0.0)
            if isinstance(duration, (int, float)):
                durations.append(float(duration))

        if durations:
            avg_time = sum(durations) / len(durations)
            total_time = sum(durations)
            time_info = f"\n  Time: {total_time:.1f}s total, {avg_time:.3f}s per record"
        else:
            time_info = ""

        # Format error information
        error_info = ""
        if errors > 0:
            error_info = f"\n  {Color.RED}Errors: {errors} ({error_rate:.1f}%){Color.RESET}"
            # Add sample of first error
            for result in self.merged_results:
                error_msg = result.get("error", {}).get("message")
                if error_msg:
                    preview = (error_msg[:60] + "...") if len(error_msg) > 60 else error_msg
                    error_info += f"\n    First error: {preview}"
                    break

        # Build the representation
        info = [
            f"ExperimentResults({exp_name})",
            f"  Dataset: {dataset_name} ({total_records:,} records)",
            f"  Task: {self.experiment.task.__name__}",
        ]

        if error_info:
            info.append(error_info)

        # Add time info if available
        if time_info:
            info.append(time_info)

        # Add evaluator section if we have evaluations
        if evaluator_names:
            info.append(f"  Evaluations:")
            info.extend(eval_info)

        if self.experiment._datadog_experiment_id:
            info.append(
                f"\n  {Color.BLUE}URL: {get_base_url()}/llm/testing/experiments/{self.experiment._datadog_experiment_id}{Color.RESET}"
            )

        return "\n".join(info)
