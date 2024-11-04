from datetime import datetime
from http.client import HTTPSConnection
import hashlib
import json
import os
from typing import Any, Callable, Dict, List, Union, Optional, Iterator
import sys
import time
from urllib.parse import quote
import concurrent.futures
import itertools
import uuid

# Constants
BASE_URL = "api.datadoghq.com"


class Dataset:
    """A container for LLM experiment data that can be pushed to and retrieved from Datadog.

    This class manages collections of input/output pairs used for LLM experiments,
    with functionality to validate, push to Datadog, and retrieve from Datadog.

    Attributes:
        name (str): Name of the dataset
        data (List[Dict[str, Any]]): List of records containing input/output pairs
        description (str): Optional description of the dataset
        datadog_dataset_id (str): ID assigned by Datadog after pushing (None if not pushed)
    """

    def __init__(
        self, name: str, data: List[Dict[str, Any]], description: str = ""
    ) -> None:
        self.name = name
        self._validate_data(data)
        self.data = data
        self.description = description

        # Post-push attributes
        self.datadog_dataset_id = None

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]

    def __repr__(self) -> str:
        header = f"Dataset: {self.name}\nDescription: {self.description}\nLength: {len(self)}\nDatadog ID: {self.datadog_dataset_id}\n"
        separator = f"+{'-' * 10}+{'-' * 38}+{'-' * 38}+"

        def format_dict(d: Dict[str, Any]) -> List[str]:
            def truncate(value: str) -> str:
                return (value[:17] + "...") if len(value) > 20 else value

            return [f"{key}: {truncate(str(value))}" for key, value in d.items()]

        def format_entries(entries):
            formatted_rows = []
            for i, entry in entries:
                input_lines = format_dict(entry["input"])
                expected_output_lines = format_dict(entry.get("expected_output", {}))

                # Determine the maximum number of lines in input and expected_output
                max_lines = max(len(input_lines), len(expected_output_lines))

                # Pad the lists to have the same number of lines
                input_lines += [""] * (max_lines - len(input_lines))
                expected_output_lines += [""] * (max_lines - len(expected_output_lines))

                for j in range(max_lines):
                    if j == 0:
                        index = f"| {i+1:<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} |"
                    else:
                        index = f"| {'':<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} |"
                    formatted_rows.append(index)
                formatted_rows.append(separator)
            return "\n".join(formatted_rows)

        if len(self.data) <= 4:
            entries = format_entries(enumerate(self.data))
        else:
            first_two = format_entries(enumerate(self.data[:2]))
            last_two = format_entries(
                enumerate(self.data[-2:], start=len(self.data) - 2)
            )
            entries = f"{first_two}\n| {'...':<8} | {'...':<38} | {'...':<38} |\n{separator}\n{last_two}"

        table = f"{separator}\n| {'Index':<8} | {'Input':<38} | {'Expected Output':<38} |\n{separator}\n{entries}"
        return f"{header}\n{table if entries else 'No entries available.'}\n\n"

    def _validate_data(self, data: List[Dict[str, Any]]) -> None:
        """Validate the format and structure of dataset records.

        Args:
            data: List of dataset records to validate

        Raises:
            ValueError: If data is empty, contains non-dictionary rows,
                       has inconsistent keys, contains nested dictionaries,
                       or exceeds 50,000 rows
        """
        if not data:
            raise ValueError("Data cannot be empty.")

        if len(data) > 50000:
            raise ValueError("Dataset cannot exceed 50,000 rows.")

        if not all(isinstance(row, dict) for row in data):
            raise ValueError("All rows must be dictionaries.")

        first_row_keys = set(data[0].keys())
        for row in data:
            if set(row.keys()) != first_row_keys:
                raise ValueError("All rows must have the same keys.")

            # Check that 'input' and 'expected_output' are flat dictionaries
            for key in ["input", "expected_output"]:
                if key in row and any(
                    isinstance(value, dict) for value in row[key].values()
                ):
                    raise ValueError(
                        f"'{key}' must be a flat dictionary (no nested dictionaries)."
                    )

    @classmethod
    def from_datadog(cls, name: str) -> "Dataset":
        """Create a dataset from a dataset hosted in Datadog.

        Args:
            name: Name of the dataset to retrieve from Datadog

        Returns:
            Dataset: A new Dataset instance populated with the records from Datadog

        Raises:
            ValueError: If the dataset is not found
            Exception: If there are HTTP errors during the request
        """
        _validate_api_keys()
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json",
        }

        try:
            # Get dataset ID
            encoded_name = quote(name)
            url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
            response_data = _make_request(
                conn, headers, "GET", url, context="Dataset lookup"
            )
            datasets = response_data.get("data", [])

            if not datasets:
                raise ValueError(f"Dataset '{name}' not found")

            dataset_id = datasets[0]["id"]

            # Get dataset records
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            records_data = _make_request(
                conn, headers, "GET", url, context="Records lookup"
            )

            # Transform records into the expected format
            class_records = []
            for record in records_data.get("data", []):
                attrs = record.get("attributes", {})
                class_records.append(
                    {
                        "input": attrs.get("input", {}),
                        "expected_output": attrs.get("expected_output", {}),
                        **attrs.get("metadata", {}),
                    }
                )

            # Create new dataset instance
            dataset = cls(name, class_records)
            dataset.datadog_dataset_id = dataset_id
            return dataset

        finally:
            conn.close()

    def push(self) -> Dict[str, str]:
        """Push the dataset to Datadog.

        Returns:
            Dict[str, str]: Dictionary containing dataset information including:
                - dataset_id: The ID of the created/updated dataset
                - dataset_name: The name of the dataset
                - record_count: Number of records uploaded
        """
        _validate_api_keys()
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json",
        }

        try:
            # Check if dataset exists
            encoded_name = quote(self.name)
            url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
            response_data = _make_request(
                conn, headers, "GET", url, context="Dataset lookup"
            )
            datasets = response_data.get("data", [])

            if not datasets:
                # Create new dataset
                dataset_payload = {
                    "data": {
                        "type": "datasets",
                        "attributes": {
                            "name": self.name,
                            "description": self.description,
                            "metadata": {"team": "ml-obs"},
                        },
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/datasets",
                    body=json.dumps(dataset_payload),
                    context="Dataset creation",
                )
                dataset_id = response_data["data"]["id"]
                self.datadog_dataset_id = dataset_id
            else:
                # Dataset exists, raise error
                raise ValueError(
                    f"Dataset '{self.name}' already exists. Dataset versioning will be supported in a future release. "
                    "Please use a different name for your dataset."
                )

            # Add records to the dataset
            records_payload = {
                "data": {"type": "datasets", "attributes": {"records": self.data}}
            }
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            _make_request(
                conn,
                headers,
                "POST",
                url,
                body=json.dumps(records_payload),
                context="Adding records",
            )

            print(f"✓ Successfully uploaded dataset '{self.name}'")
            print(f"  • Dataset ID: {dataset_id}")
            print(f"  • Records uploaded: {len(self.data)}")

            return self

        finally:
            conn.close()


class Experiment:
    """Manages the execution and evaluation of LLM tasks on a dataset.

    This class handles running tasks against datasets, applying evaluators,
    and collecting results for analysis.

    Attributes:
        name (str): Name of the experiment
        task (Callable): Function that processes each dataset record
        dataset (Dataset): Dataset to run the experiment on
        evaluators (List[Callable]): Functions that evaluate task outputs
        tags (List[str]): Tags for organizing experiments
        project_name (str): Name of the project this experiment belongs to
        has_run (bool): Whether the experiment has been executed
        has_evaluated (bool): Whether the evaluations have been performed
        outputs (List[Dict]): Outputs after running the task
        results (ExperimentResults): Results after running evaluations
    """

    def __init__(
        self,
        name: str,
        task: Callable,
        dataset: Dataset,
        evaluators: List[Callable],
        tags: List[str] = [],
        project_name: str = "-",
        description: str = "",
        metadata: Dict[str, Any] = {},
    ) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators
        self.tags = tags
        self.project_name = project_name
        self.description = description
        self.metadata = metadata
        # Post-run attributes
        self.has_run = False
        self.has_evaluated = False
        self.outputs = []
        self.results = None

    def __repr__(self) -> str:
        separator = f"+{'-' * 20}+{'-' * 50}+"

        def format_evaluator(evaluator: Callable) -> str:
            return f"{evaluator.__name__}"

        evaluator_lines = [format_evaluator(evaluator) for evaluator in self.evaluators]
        evaluators = (
            ", ".join(evaluator_lines) if evaluator_lines else "No evaluators available"
        )

        table = (
            f"{separator}\n"
            f"| {'Experiment':<18} | {self.name:<48} |\n"
            f"{separator}\n"
            f"| {'Task':<18} | {self.task.__name__:<48} |\n"
            f"| {'Dataset':<18} | {f'{self.dataset.name} (n={len(self.dataset)})':<48} |\n"
            f"| {'Evaluators':<18} | {evaluators:<48} |\n"
            f"{separator}"
        )
        return table

    def _validate_tasks(self) -> None:
        # TODO: Design and implement this
        pass

    def _validate_evaluators(self) -> None:
        # TODO: Design and implement this
        pass

    def _validate_tags(self) -> None:
        """Validate experiment tags format.

        Raises:
            ValueError: If any tag doesn't follow the 'key:value' format
        """
        for tag in self.tags:
            if not isinstance(tag, str) or ":" not in tag:
                raise ValueError(
                    f"Invalid tag format: {tag}. Tags should be in the format 'key:value'."
                )

    def run(self, _jobs: int = 10) -> None:
        """Execute the experiment tasks on the dataset without performing evaluations.

        Runs the task function on each dataset record in parallel and stores
        the outputs and metadata.

        Args:
            _jobs (int, optional): Number of parallel workers. Defaults to 10.
                Must be between 1 and 20.

        Raises:
            ValueError: If _jobs is not between 1 and 20
        """
        if not 1 <= _jobs <= 20:
            raise ValueError("Number of jobs must be between 1 and 20")

        self.outputs = []
        total_rows = len(self.dataset)

        def process_row(idx_row):
            idx, row = idx_row
            try:
                # Apply the task function to the row
                start_time = time.time()
                output = self.task(row)
                end_time = time.time()
                duration = end_time - start_time

                return {
                    "idx": idx,
                    "output": output,
                    "metadata": {
                        "timestamp": start_time,
                        "duration": duration,
                        "dataset_record_idx": idx,
                        "project_name": self.project_name,
                        "experiment_name": self.name,
                        "dataset_name": self.dataset.name,
                    },
                    "error": None,
                }
            except Exception as e:
                return {
                    "idx": idx,
                    "output": None,
                    "metadata": {
                        "timestamp": time.time(),
                        "duration": 0,
                        "dataset_record_idx": idx,
                        "project_name": self.project_name,
                        "experiment_name": self.name,
                        "dataset_name": self.dataset.name,
                    },
                    "error": str(e),
                }

        with concurrent.futures.ThreadPoolExecutor(max_workers=_jobs) as executor:
            future_to_idx = {
                executor.submit(process_row, (idx, row)): idx
                for idx, row in enumerate(self.dataset)
            }

            # Process as they complete while maintaining order
            completed = 0
            outputs_buffer = [None] * total_rows
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                outputs_buffer[idx] = future.result()
                completed += 1

                # Update progress
                progress = int(50 * completed / total_rows)
                bar = f"{'=' * progress}{' ' * (50 - progress)}"
                percent = int(100 * completed / total_rows)
                sys.stdout.write(
                    f"\rRunning {self.name}: [{bar}] {percent}% ({completed}/{total_rows})"
                )
                sys.stdout.flush()

            self.outputs = outputs_buffer

        sys.stdout.write("\n")

        self.has_run = True

        return self.eval()

    def eval(self, _jobs: int = 10) -> "ExperimentResults":
        """Evaluate the outputs using the provided evaluators.

        Runs the evaluators on each output in parallel and collects evaluations.

        Args:
            _jobs (int, optional): Number of parallel workers. Defaults to 10.
                Must be between 1 and 20.

        Returns:
            ExperimentResults: Object containing the experiment results

        Raises:
            ValueError: If _jobs is not between 1 and 20
            ValueError: If the experiment has not been run yet
        """
        if not 1 <= _jobs <= 20:
            raise ValueError("Number of jobs must be between 1 and 20")

        if not self.has_run:
            raise ValueError("Experiment has not been run yet. Please call run() before eval().")

        results = ExperimentResults(self.dataset, self)
        total_rows = len(self.outputs)

        def evaluate_output(idx_output):
            idx, output_data = idx_output
            try:
                idx_in_dataset = output_data["metadata"]["dataset_record_idx"]
                row = self.dataset[idx_in_dataset]
                output = output_data["output"]
                evaluations = {
                    evaluator.__name__: evaluator(row, output)
                    for evaluator in self.evaluators
                }

                result = {
                    "output": output,
                    "evaluations": evaluations,
                    "metadata": output_data["metadata"],
                    "tags": self.tags,
                    "error": output_data["error"],
                }

                return {"idx": idx, "result": result}
            except Exception as e:
                return {
                    "idx": idx,
                    "result": {
                        "output": output_data["output"],
                        "evaluations": {},
                        "metadata": output_data["metadata"],
                        "tags": self.tags,
                        "error": str(e),
                    },
                }

        with concurrent.futures.ThreadPoolExecutor(max_workers=_jobs) as executor:
            future_to_idx = {
                executor.submit(evaluate_output, (idx, output_data)): idx
                for idx, output_data in enumerate(self.outputs)
            }

            # Process as they complete while maintaining order
            completed = 0
            results_buffer = [None] * total_rows
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                results_buffer[idx] = future.result()["result"]
                completed += 1

            results.experiment_rows = results_buffer

        sys.stdout.write("\n")

        self.has_evaluated = True
        self.results = results
        return results

    def get_results(self) -> 'ExperimentResults':
        if not self.has_evaluated:
            raise ValueError("Evaluations have not been performed yet. Please call eval() after run().")
        return self.results


class ExperimentResults:
    """Contains and manages the results of an experiment run.

    Stores the outputs, evaluations, and metadata for each record processed
    in an experiment, with functionality to analyze and push results to Datadog.

    Attributes:
        dataset (Dataset): The dataset used in the experiment
        experiment (Experiment): The experiment that generated these results
        experiment_rows (List[Dict]): Results for each processed record
    """

    def __init__(self, dataset: Dataset, experiment: Experiment) -> None:
        self.dataset = dataset
        self.experiment = experiment
        self.experiment_rows = []

    def __repr__(self) -> str:
        separator = f"+{'-' * 10}+{'-' * 38}+{'-' * 38}+{'-' * 38}+{'-' * 38}+"

        def format_dict(d: Union[Dict[str, Any], List[Any]]) -> List[str]:
            if isinstance(d, dict):

                def truncate(value: str) -> str:
                    return (value[:17] + "...") if len(value) > 20 else value

                return [f"{key}: {truncate(str(value))}" for key, value in d.items()]
            elif isinstance(d, list):
                return [str(item) for item in d]
            else:
                return [str(d)]

        def format_entries(entries):
            formatted_rows = []
            for i, entry in enumerate(entries):
                dataset_idx = entry["metadata"]["dataset_record_idx"]
                dataset_entry = self.dataset[dataset_idx]
                input_lines = format_dict(dataset_entry["input"])
                expected_output_lines = format_dict(
                    dataset_entry.get("expected_output", {})
                )
                output_lines = format_dict(entry["output"])
                evaluations_lines = format_dict(entry.get("evaluations", []))

                # Determine the maximum number of lines across all fields
                max_lines = max(
                    len(input_lines),
                    len(expected_output_lines),
                    len(output_lines),
                    len(evaluations_lines),
                )

                # Pad the lists to have the same number of lines
                input_lines += [""] * (max_lines - len(input_lines))
                expected_output_lines += [""] * (max_lines - len(expected_output_lines))
                output_lines += [""] * (max_lines - len(output_lines))
                evaluations_lines += [""] * (max_lines - len(evaluations_lines))

                for j in range(max_lines):
                    if j == 0:
                        index = f"| {dataset_idx:<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} | {output_lines[j]:<38} | {evaluations_lines[j]:<38} |"
                    else:
                        index = f"|{'':<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} | {output_lines[j]:<38} | {evaluations_lines[j]:<38} |"
                    formatted_rows.append(index)
                formatted_rows.append(separator)
            return "\n".join(formatted_rows)

        if len(self.experiment_rows) <= 4:
            entries = format_entries(self.experiment_rows)
        else:
            first_two = format_entries(self.experiment_rows[:2])
            last_two = format_entries(self.experiment_rows[-2:])
            entries = f"{first_two}\n| {'...':<8} | {'...':<38} | {'...':<38} | {'...':<38} | {'...':<38} |\n{separator}\n{last_two}"

        table = (
            f"{separator}\n"
            f"| {'Index':<8} | {'Input':<38} | {'Expected Output':<38} | {'Output':<38} | {'Evaluations':<38} |\n"
            f"{separator}\n"
            f"{entries}"
        )
        return f"Experiment Results:\n{table if entries else 'No results available.'}\n\n"

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.experiment_rows)

    def __len__(self) -> int:
        return len(self.experiment_rows)

    def __getitem__(self, index: int) -> Any:
        return self.experiment_rows[index]

    def push(self) -> Dict[str, str]:
        """Push the experiment results to Datadog.

        Returns:
            Dict[str, str]: Dictionary containing experiment information including:
                - experiment_id: The ID of the created experiment
                - experiment_name: The name of the experiment
                - span_count: Number of spans uploaded
        """
        _validate_api_keys()

        # Initialize connection and headers
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json",
        }

        try:
            # Check if project exists
            url = f"/api/unstable/llm-obs/v1/projects?filter[name]={self.experiment.project_name}"
            response_data = _make_request(
                conn, headers, "GET", url, context="Project lookup"
            )
            projects = response_data.get("data", [])

            if not projects:
                # Create new project
                project_payload = {
                    "data": {
                        "type": "projects",
                        "attributes": {
                            "name": self.experiment.project_name,
                            "description": "",
                            "metadata": {"team": "ml-obs"},
                        },
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/projects",
                    body=json.dumps(project_payload),
                    context="Project creation",
                )
                project_id = response_data["data"]["id"]
            else:
                project_id = projects[0]["id"]

            # Check if experiment exists
            encoded_name = quote(self.experiment.name)
            url = f"/api/unstable/llm-obs/v1/experiments?filter[name]={encoded_name}"
            response_data = _make_request(
                conn, headers, "GET", url, context="Experiment lookup"
            )
            experiments = response_data.get("data", [])

            if not experiments:
                # Create new experiment
                experiment_payload = {
                    "data": {
                        "type": "experiments",
                        "attributes": {
                            "name": self.experiment.name,
                            "description": self.experiment.description,
                            "dataset_id": self.experiment.dataset.datadog_dataset_id,
                            "project_id": project_id,
                            "metadata": {
                                "tags": self.experiment.tags,
                                **self.experiment.metadata,
                            },
                        },
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/experiments",
                    body=json.dumps(experiment_payload),
                    context="Experiment creation",
                )
                experiment_id = response_data["data"]["id"]
            else:
                # Experiment exists, create a new version
                version_suffix = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
                new_experiment_name = f"{self.experiment.name}-{version_suffix}"
                print(
                    f"Experiment '{self.experiment.name}' found. Creating new version '{new_experiment_name}'."
                )
                experiment_payload = {
                    "data": {
                        "type": "experiments",
                        "attributes": {
                            "name": new_experiment_name,
                            "description": self.experiment.description,
                            "dataset_id": self.experiment.dataset.datadog_dataset_id,
                            "project_id": project_id,
                            "metadata": {
                                "tags": self.experiment.tags,
                                **self.experiment.metadata,
                            },
                        },
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/experiments",
                    body=json.dumps(experiment_payload),
                    context="Experiment version creation",
                )
                experiment_id = response_data["data"]["id"]
                self.experiment.name = new_experiment_name

            spans = []
            metrics = []

            for idx, result in enumerate(self.experiment_rows):

                span = {
                    "span_id": _make_id(),
                    "project_id": project_id,
                    "experiment_id": experiment_id,
                    "dataset_id": self.experiment.dataset.datadog_dataset_id,
                    "dataset_record_id": _make_id(),
                    "start_ns": int(result["metadata"]["timestamp"] * 1e9),
                    "duration": float(result["metadata"]["duration"] * 1e9),
                    "tags": self.experiment.tags,
                    "status": "ok",
                    "metrics": { # TODO: Fill in with actual metrics once we have tracing and llm spans
                    },
                    "meta": {
                        "span": {"kind": "experiment"},
                        "input": self.experiment.dataset[idx]["input"],
                        "output": result["output"],
                        "expected_output": self.experiment.dataset[idx].get(
                            "expected_output", {}
                        ),
                        "error": {
                            "message": result["error"],
                            "stack": None,
                            "type": None,
                        },
                    },
                }
                spans.append(span)

                # Add evaluation metrics
                for metric_name, metric_value in result["evaluations"].items():
                    timestamp_ms = int(result["metadata"]["timestamp"] * 1000)
                    
                    # Check for bool first, since bool is a subclass of int
                    if isinstance(metric_value, bool):
                        metric_type = "categorical"
                        metric_value = str(metric_value).lower() 
                    elif isinstance(metric_value, (int, float)):
                        metric_type = "score"
                    else:
                        metric_type = "categorical"
                        metric_value = str(metric_value)

                    metric = {
                        "span_id": span["span_id"],
                        "metric_type": metric_type,
                        "timestamp_ms": timestamp_ms,
                        "label": metric_name,
                    }

                    if metric_type == "score":
                        metric["score_value"] = metric_value
                    else:
                        metric["categorical_value"] = metric_value

                    metrics.append(metric)

            print(metrics)
            results_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {"spans": spans, "metrics": metrics},
                }
            }

            

            url = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
            _make_request(
                conn,
                headers,
                "POST",
                url,
                body=json.dumps(results_payload),
                context="Publishing results",
            )

            print(
                f"✓ Successfully uploaded experiment results for '{self.experiment.name}'"
            )
            print(f"  • Experiment ID: {experiment_id}")
            print(f"  • Spans uploaded: {len(spans)}")
            print(f"  • Metrics uploaded: {len(metrics)}")

            return self

        finally:
            conn.close()


def _make_request(
    conn: HTTPSConnection,
    headers: Dict[str, Any],
    method: str,
    url: str,
    body: Optional[Any] = None,
    context: str = "",
) -> Dict[str, Any]:
    """Make an HTTP request to the Datadog API.

    Raises:
        DatadogAPIError: If the request fails or returns an error status
        DatadogResponseError: If the response contains invalid JSON
    """
    if method == "GET":
        conn.request(method, url, headers=headers)
    else:
        if body is not None and isinstance(body, str):
            body = body.encode("utf-8")
        conn.request(method, url, body=body, headers=headers)

    response = conn.getresponse()
    response_body = response.read()
    response_text = response_body.decode('utf-8')

    if response.status >= 400:
        error_message = f"HTTP {response.status} Error during {context}: {response.reason}\nResponse: {response_text}"
        raise DatadogAPIError(error_message, status_code=response.status, response=response_text)

    if not response_body:
        return {}

    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        error_message = f"Invalid JSON response during {context}. Status: {response.status}"
        raise DatadogResponseError(error_message, raw_response=response_text)


def _make_id() -> str:
    """Generate a unique identifier.

    Returns:
        str: A random UUID as a hexadecimal string
    """
    return uuid.uuid4().hex


class DatadogAPIError(Exception):
    """Raised when there is an error interacting with the Datadog API."""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[str] = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)

class DatadogResponseError(Exception):
    """Raised when there is an error parsing the response from Datadog."""
    def __init__(self, message: str, raw_response: Optional[str] = None):
        self.raw_response = raw_response
        super().__init__(message)


def _validate_api_keys() -> None:
    """Validate that required Datadog API keys are set in environment variables.

    Raises:
        ValueError: If any required API keys are missing from environment variables
    """
    missing_keys = []
    for key in ["DD_API_KEY", "DD_APPLICATION_KEY"]:
        if not os.getenv(key):
            missing_keys.append(key)

    if missing_keys:
        raise ValueError(
            f"Missing required Datadog API keys in environment variables: {', '.join(missing_keys)}. "
            "Please set these environment variables before pushing to Datadog."
        )



def parametrize(**param_dict: Dict[str, Union[Any, List[Any]]]) -> Callable:
    """Decorator that creates multiple versions by combining all parameter values.
    
    Args:
        **param_dict: Dictionary of parameter names and their possible values.
                     Values can be single items or lists of possible values.

    Returns:
        List[Any]: List of results from calling the decorated function with each parameter combination
    """
    def decorator(func):
        # Convert single values to lists
        processed_params = {
            name: [val] if not isinstance(val, (list, tuple)) else val
            for name, val in param_dict.items()
        }

        # Generate all combinations of parameters
        param_names = list(processed_params.keys())
        param_values = [processed_params[name] for name in param_names]
        param_combinations = [
            dict(zip(param_names, combo)) 
            for combo in itertools.product(*param_values)
        ]

        # Return list of results from calling function with each combination
        return [func(**params) for params in param_combinations]

    return decorator

class Prompt:
    """A class for rendering templated prompts with variables.

    Supports both simple string templates and structured chat-like templates.

    Attributes:
        template (Union[str, List[Dict[str, str]]]): Either a template string or a list of message dictionaries
        variables (dict): Default variables to use when rendering the template
    """

    def __init__(self, template, variables=None):
        """Initialize a new Prompt.

        Args:
            template (Union[str, List[Dict[str, str]]]): Either a template string or a list of message dictionaries
            variables (dict, optional): Default variables to use when rendering the template. Defaults to {}.
        """
        self.template = template
        self.variables = variables or {}

    def render(self, **kwargs):
        """Render the template with provided variables.

        Args:
            **kwargs: Additional variables to use when rendering the template.
                     These override any default variables with the same name.

        Returns:
            Union[str, List[Dict[str, str]]]: The rendered template with all variables substituted
        """
        merged_vars = {**self.variables, **kwargs}

        if isinstance(self.template, str):
            return self.template.format(**merged_vars)
        elif isinstance(self.template, (list, tuple)):
            return [
                {
                    k: v.format(**merged_vars) if isinstance(v, str) else v
                    for k, v in message.items()
                }
                for message in self.template
            ]
        else:
            raise ValueError("Template must be either a string or a list of message dictionaries")
        
        
        
    def __repr__(self):
        hash = hashlib.md5(str(self.template).encode()).hexdigest()[:8]
        return f"Prompt(hash={hash})"