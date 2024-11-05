import concurrent.futures
from datetime import datetime
import json
import os
import sys
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from urllib.parse import quote
import uuid

from ._utils import HTTPResponse
from ._utils import http_request


BASE_URL = "https://api.datadoghq.com"


class Dataset:
    """A container for LLM experiment data that can be pushed to and retrieved from Datadog.

    This class manages collections of input/output pairs used for LLM experiments,
    with functionality to validate, push to Datadog, and retrieve from Datadog.

    Attributes:
        name (str): Name of the dataset
        description (str): Optional description of the dataset
    """

    def __init__(self, name: str, data: List[Dict[str, Any]], description: str = "") -> None:
        self.name = name
        self.description = description
        self._validate_data(data)
        self._data = data

        # Post-push attributes
        self._datadog_dataset_id = None

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self._data[index]

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
                if key in row and any(isinstance(value, dict) for value in row[key].values()):
                    raise ValueError(f"'{key}' must be a flat dictionary (no nested dictionaries).")

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
        # Get dataset ID
        encoded_name = quote(name)
        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
        resp = exp_http_request("GET", url)
        response_data = resp.json()
        datasets = response_data.get("data", [])

        if not datasets:
            raise ValueError(f"Dataset '{name}' not found")

        dataset_id = datasets[0]["id"]

        # Get dataset records
        url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
        resp = exp_http_request("GET", url)
        records_data = resp.json()

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
        dataset._datadog_dataset_id = dataset_id
        return dataset

    def push(self) -> Dict[str, str]:
        """Push the dataset to Datadog.

        Returns:
            Dict[str, str]: Dictionary containing dataset information including:
                - dataset_id: The ID of the created/updated dataset
                - dataset_name: The name of the dataset
                - record_count: Number of records uploaded
        """
        # Check if dataset exists
        encoded_name = quote(self.name)
        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
        resp = exp_http_request("GET", url)
        response_data = resp.json()
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
            resp = exp_http_request(
                "POST", "/api/unstable/llm-obs/v1/datasets", body=json.dumps(dataset_payload).encode("utf-8")
            )
            response_data = resp.json()
            dataset_id = response_data["data"]["id"]
            self._datadog_dataset_id = dataset_id
        else:
            # Dataset exists, raise error
            raise ValueError(
                f"Dataset '{self.name}' already exists. Dataset versioning will be supported in a future release. "
                "Please use a different name for your dataset."
            )

        # Add records to the dataset
        records_payload = {"data": {"type": "datasets", "attributes": {"records": self._data}}}
        url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
        resp = exp_http_request("POST", url, body=json.dumps(records_payload).encode("utf-8"))
        data = resp.json()
        return data


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
                raise ValueError(f"Invalid tag format: {tag}. Tags should be in the format 'key:value'.")

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
            future_to_idx = {executor.submit(process_row, (idx, row)): idx for idx, row in enumerate(self.dataset)}

            # Process as they complete while maintaining order
            completed = 0
            outputs_buffer = [None] * total_rows
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                outputs_buffer[idx] = future.result()
                completed += 1
            self.outputs = outputs_buffer

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
                evaluations = {evaluator.__name__: evaluator(row, output) for evaluator in self.evaluators}

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

        self.has_evaluated = True
        self.results = results
        return results

    def get_results(self) -> "ExperimentResults":
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

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.experiment_rows)

    def __len__(self) -> int:
        return len(self.experiment_rows)

    def __getitem__(self, index: int) -> Any:
        return self.experiment_rows[index]

    def push(self) -> None:
        """Push the experiment results to Datadog.

        Returns:
            Dict[str, str]: Dictionary containing experiment information including:
                - experiment_id: The ID of the created experiment
                - experiment_name: The name of the experiment
                - span_count: Number of spans uploaded
        """
        # Check if project exists
        url = f"/api/unstable/llm-obs/v1/projects?filter[name]={self.experiment.project_name}"
        resp = exp_http_request("GET", url)
        response_data = resp.json()
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
            resp = exp_http_request(
                "POST",
                "/api/unstable/llm-obs/v1/projects",
                body=json.dumps(project_payload).encode("utf-8"),
            )
            response_data = resp.json()
            project_id = response_data["data"]["id"]
        else:
            project_id = projects[0]["id"]

        # Check if experiment exists
        encoded_name = quote(self.experiment.name)
        url = f"/api/unstable/llm-obs/v1/experiments?filter[name]={encoded_name}"
        resp = exp_http_request("GET", url)
        response_data = resp.json()
        experiments = response_data.get("data", [])

        if not experiments:
            # Create new experiment
            experiment_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "name": self.experiment.name,
                        "description": self.experiment.description,
                        "dataset_id": self.experiment.dataset._datadog_dataset_id,
                        "project_id": project_id,
                        "metadata": {
                            "tags": self.experiment.tags,
                            **self.experiment.metadata,
                        },
                    },
                }
            }
            resp = exp_http_request(
                "POST", "/api/unstable/llm-obs/v1/experiments", body=json.dumps(experiment_payload).encode("utf-8")
            )
            response_data = resp.json()
            experiment_id = response_data["data"]["id"]
        else:
            # Experiment exists, create a new version
            version_suffix = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            new_experiment_name = f"{self.experiment.name}-{version_suffix}"
            experiment_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "name": new_experiment_name,
                        "description": self.experiment.description,
                        "dataset_id": self.experiment.dataset._datadog_dataset_id,
                        "project_id": project_id,
                        "metadata": {
                            "tags": self.experiment.tags,
                            **self.experiment.metadata,
                        },
                    },
                }
            }
            resp = exp_http_request(
                "POST", "/api/unstable/llm-obs/v1/experiments", body=json.dumps(experiment_payload).encode("utf-8")
            )
            response_data = resp.json()
            experiment_id = response_data["data"]["id"]
            self.experiment.name = new_experiment_name

        spans = []
        metrics = []
        for idx, result in enumerate(self.experiment_rows):
            span = {
                "span_id": _make_id(),
                "project_id": project_id,
                "experiment_id": experiment_id,
                "dataset_id": self.experiment.dataset._datadog_dataset_id,
                "dataset_record_id": _make_id(),
                "start_ns": int(result["metadata"]["timestamp"] * 1e9),
                "duration": float(result["metadata"]["duration"] * 1e9),
                "tags": self.experiment.tags,
                "status": "ok",
                "metrics": {},  # TODO: Fill in with actual metrics once we have tracing and llm spans
                "meta": {
                    "span": {"kind": "experiment"},
                    "input": self.experiment.dataset[idx]["input"],
                    "output": result["output"],
                    "expected_output": self.experiment.dataset[idx].get("expected_output", {}),
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
                    "score_value" if metric_type == "score" else "categorical_value": metric_value,
                }

                if metric_type == "score":
                    metric["score_value"] = metric_value
                else:
                    metric["categorical_value"] = metric_value

                metrics.append(metric)

        results_payload = {
            "data": {
                "type": "experiments",
                "attributes": {"spans": spans, "metrics": metrics},
            }
        }

        url = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
        exp_http_request("POST", url, body=json.dumps(results_payload).encode("utf-8"))


def _make_id() -> str:
    """Generate a unique identifier.

    Returns:
        str: A random UUID as a hexadecimal string
    """
    return uuid.uuid4().hex


def exp_http_request(method: str, url: str, body: Optional[bytes] = None) -> HTTPResponse:
    """Make an HTTP request to the Datadog experiments API."""
    missing_keys = []
    for key in ["DD_API_KEY", "DD_APPLICATION_KEY"]:
        if not os.getenv(key):
            missing_keys.append(key)

    if missing_keys:
        raise ValueError(
            f"Missing required Datadog API keys in environment variables: {', '.join(missing_keys)}. "
            "Please set these environment variables before pushing to Datadog."
        )

    headers = {
        "DD-API-KEY": os.getenv("DD_API_KEY"),
        "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
        "Content-Type": "application/json",
    }
    url = BASE_URL + url
    resp = HTTPResponse(http_request(method, url, headers=headers, body=body))
    if resp.status_code >= 400:
        raise ValueError(f"Failed to make request, got status code {resp.status_code}.")
