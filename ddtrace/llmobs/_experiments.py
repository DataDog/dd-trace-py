# TODO: Add error handling

import concurrent.futures
from datetime import datetime
import json
import os
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
import inspect
from functools import wraps
from urllib.parse import quote
import uuid
import csv
from enum import Enum
import itertools
import hashlib
import threading

from ._utils import HTTPResponse
from ._utils import http_request


DD_SITE = os.getenv("DD_SITE", "datadoghq.com")
BASE_URL = f"https://api.{DD_SITE}"


class FileType(Enum):
    CSV = 'csv'
    PARQUET = 'parquet'
    JSONL = 'jsonl'


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

            # Validate that 'input' exists and is a dictionary
            if 'input' not in row:
                raise ValueError("Each row must contain an 'input' field")
            if not isinstance(row['input'], dict):
                raise ValueError("The 'input' field must be a dictionary")

            # If expected_output exists, validate it's a dictionary
            if 'expected_output' in row and not isinstance(row['expected_output'], dict):
                raise ValueError("The 'expected_output' field must be a dictionary")

            # Check that 'input' and 'expected_output' are flat dictionaries
            for key in ["input", "expected_output"]:
                if key in row and any(isinstance(value, dict) for value in row[key].values()):
                    raise ValueError(f"'{key}' must be a flat dictionary (no nested dictionaries).")

    @classmethod
    def pull(cls, name: str) -> "Dataset":
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

    def push(self) -> None:
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

    @classmethod
    def from_csv(
        cls,
        filepath: str,
        name: str,
        description: str = "",
        delimiter: str = ",",
        input_columns: List[str] = None,
        expected_output_columns: List[str] = None,
        metadata_columns: List[str] = None,
    ) -> "Dataset":
        if input_columns is None or expected_output_columns is None:
            raise ValueError("`input_columns` and `expected_output_columns` must be provided.")

        data = []
        try:
            with open(filepath, mode='r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                rows = list(reader)
                if not rows:
                    raise ValueError("CSV file is empty.")

                # Ensure that the specified columns are present
                header_columns = reader.fieldnames
                missing_input_columns = [col for col in input_columns if col not in header_columns]
                missing_output_columns = [col for col in expected_output_columns if col not in header_columns]
                missing_metadata_columns = []
                if metadata_columns:
                    missing_metadata_columns = [col for col in metadata_columns if col not in header_columns]

                if missing_input_columns:
                    raise ValueError(f"Input columns not found in CSV header: {missing_input_columns}")
                if missing_output_columns:
                    raise ValueError(f"Expected output columns not found in CSV header: {missing_output_columns}")
                if missing_metadata_columns:
                    raise ValueError(f"Metadata columns not found in CSV header: {missing_metadata_columns}")

                for row in rows:
                    input_data = {col: row[col] for col in input_columns}
                    expected_output_data = {col: row[col] for col in expected_output_columns}
                    metadata = {}
                    if metadata_columns:
                        metadata = {col: row[col] for col in metadata_columns}

                    data.append({
                        'input': input_data,
                        'expected_output': expected_output_data,
                        **metadata,
                    })
        except Exception as e:
            raise Exception(f"Failed to read CSV file: {e}")

        return cls(name=name, data=data, description=description)

    @classmethod
    def from_jsonl(cls, filepath: str, name: str, description: str = "", input_columns: List[str] = None, expected_output_columns: List[str] = None, metadata_columns: List[str] = None) -> "Dataset":
        if input_columns is None or expected_output_columns is None:
            raise ValueError("`input_columns` and `expected_output_columns` must be provided.")

        data = []
        try:
            with open(filepath, mode='r', encoding='utf-8') as jsonlfile:
                for line in jsonlfile:
                    row = json.loads(line.strip())

                    input_data = {col: row.get(col) for col in input_columns}
                    expected_output_data = {col: row.get(col) for col in expected_output_columns}
                    metadata = {}
                    if metadata_columns:
                        metadata = {col: row.get(col) for col in metadata_columns}

                    data.append({
                        'input': input_data,
                        'expected_output': expected_output_data,
                        **metadata,
                    })

                if not data:
                    raise ValueError("JSONL file is empty.")

        except Exception as e:
            raise Exception(f"Failed to read JSONL file: {e}")

        return cls(name=name, data=data, description=description)

    @classmethod
    def from_parquet(cls, filepath: str, name: str, description: str = "", input_columns: List[str] = None, expected_output_columns: List[str] = None, metadata_columns: List[str] = None) -> "Dataset":
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required to read parquet files. "
                "Please install pandas with: pip install pandas"
            )
        
        if input_columns is None or expected_output_columns is None:
            raise ValueError("`input_columns` and `expected_output_columns` must be provided.")

        data = []
        try:
            df = pd.read_parquet(filepath)
            if df.empty:
                raise ValueError("Parquet file is empty.")

            # Ensure that the specified columns are present
            missing_input_columns = [col for col in input_columns if col not in df.columns]
            missing_output_columns = [col for col in expected_output_columns if col not in df.columns]
            missing_metadata_columns = []
            if metadata_columns:
                missing_metadata_columns = [col for col in metadata_columns if col not in df.columns]

            if missing_input_columns:
                raise ValueError(f"Input columns not found in DataFrame: {missing_input_columns}")
            if missing_output_columns:
                raise ValueError(f"Expected output columns not found in DataFrame: {missing_output_columns}")
            if missing_metadata_columns:
                raise ValueError(f"Metadata columns not found in DataFrame: {missing_metadata_columns}")

            for idx, row in df.iterrows():
                input_data = {col: row[col] for col in input_columns}
                expected_output_data = {col: row[col] for col in expected_output_columns}
                metadata = {}
                if metadata_columns:
                    metadata = {col: row[col] for col in metadata_columns}

                data.append({
                    'input': input_data,
                    'expected_output': expected_output_data,
                    **metadata,
                })

        except Exception as e:
            raise Exception(f"Failed to read Parquet file: {e}")

        return cls(name=name, data=data, description=description)

    @classmethod
    def import_file(cls, path: str, filetype: FileType, name: str, description: str = "", input_columns: List[str] = None, expected_output_columns: List[str] = None, metadata_columns: List[str] = None, delimiter: str = ",") -> "Dataset":
        if filetype == FileType.CSV:
            return cls.from_csv(
                filepath=path,
                name=name,
                description=description,
                delimiter=delimiter,
                input_columns=input_columns,
                expected_output_columns=expected_output_columns,
                metadata_columns=metadata_columns,
            )
        elif filetype == FileType.JSONL:
            return cls.from_jsonl(
                filepath=path,
                name=name,
                description=description,
                input_columns=input_columns,
                expected_output_columns=expected_output_columns,
                metadata_columns=metadata_columns,
            )
        elif filetype == FileType.PARQUET:
            return cls.from_parquet(
                filepath=path,
                name=name,
                description=description,
                input_columns=input_columns,
                expected_output_columns=expected_output_columns,
                metadata_columns=metadata_columns,
            )
        else:
            raise ValueError(f"Unsupported file type: {filetype}")

    def as_dataframe(self, multiindex: bool = True) -> "pd.DataFrame":
        """Convert the dataset to a pandas DataFrame.

        Args:
            multiindex (bool): If True, expand 'input' and 'expected_output' dictionaries into columns with MultiIndex.
                            If False, keep 'input' and 'expected_output' as columns containing dictionaries.

        Returns:
            pd.DataFrame: DataFrame representation of the dataset.

        Raises:
            ImportError: If pandas is not installed.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required to convert dataset to DataFrame. "
                "Please install it with `pip install pandas`"
            )

        if multiindex:
            # Create a list of flattened dictionaries
            flattened_data = []
            for record in self._data:
                flat_record = {}
                # Handle 'input' fields
                for k, v in record.get('input', {}).items():
                    flat_record[('input', k)] = v
                # Handle 'expected_output' fields
                for k, v in record.get('expected_output', {}).items():
                    flat_record[('expected_output', k)] = v
                # Handle any other top-level fields
                for k, v in record.items():
                    if k not in ['input', 'expected_output']:
                        flat_record[('metadata', k)] = v
                flattened_data.append(flat_record)

            df = pd.DataFrame(flattened_data)
            # Set columns as MultiIndex
            df.columns = pd.MultiIndex.from_tuples(df.columns)
            return df
        else:
            # Keep 'input' and 'expected_output' as dicts in the DataFrame
            return pd.DataFrame(self._data)

    def export_to_jsonl(self, file_path):
        """
        Exports the dataset to a JSONL file.

        Args:
            file_path (str): The path to the output JSONL file.
        """
        import json

        with open(file_path, 'w') as f:
            for record in self._data:
                json_line = json.dumps(record)
                f.write(json_line + '\n')


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
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators
        self.tags = tags
        self.project_name = project_name
        self.description = description
        self.metadata = metadata
        self.config = config

        # Enforce that the task function has the @task decorator
        if not hasattr(self.task, '_is_task'):
            raise TypeError("Task function must be decorated with @task decorator.")

        # Enforce that all evaluators have the @evaluator decorator
        for evaluator_func in self.evaluators:
            if not hasattr(evaluator_func, '_is_evaluator'):
                raise TypeError(f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator.")

        # Post-run attributes
        self.has_run = False
        self.has_evaluated = False
        self.outputs = []
        self.results = None

    def run_task(self, _jobs: int = 10) -> None:
        """Execute the task function on the dataset and store the outputs."""
        if not 1 <= _jobs <= 20:
            raise ValueError("Number of jobs must be between 1 and 20")
        self.outputs = []
        total_rows = len(self.dataset)
        completed = 0

        def process_row(idx_row):
            idx, row = idx_row
            try:
                # Extract the input data
                input_data = row['input']
                # Apply the task function to the input data with config
                start_time = time.time()
                if getattr(self.task, '_accepts_config', False):
                    output = self.task(input_data, self.config)
                else:
                    output = self.task(input_data)
                end_time = time.time()
                duration = end_time - start_time

                # **Ensure output is a dictionary**
                if not isinstance(output, dict):
                    output = {'value': output}

                # Prepare output data
                output_data = {
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
                return output_data

            except Exception as e:
                output_data = {
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
                return output_data

        # Initialize the progress bar
        _print_progress_bar(0, total_rows, prefix='Processing:', suffix='Complete')

        with concurrent.futures.ThreadPoolExecutor(max_workers=_jobs) as executor:
            future_to_idx = {executor.submit(process_row, (idx, row)): idx for idx, row in enumerate(self.dataset)}

            outputs_buffer = [None] * total_rows
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                output_data = future.result()
                outputs_buffer[idx] = output_data
                completed += 1
                _print_progress_bar(completed, total_rows, prefix='Processing:', suffix='Complete')

        self.outputs = outputs_buffer
        self.has_run = True

    def run_evaluations(self) -> None:
        """Run evaluators on the outputs and store the results."""
        if not self.has_run:
            raise ValueError("Task has not been run yet. Please call run_task() before run_evaluations().")

        self.results = ExperimentResults(self.dataset, self)
        results_buffer = []
        total_rows = len(self.outputs)
        completed = 0

        # Initialize the progress bar
        _print_progress_bar(0, total_rows, prefix='Evaluating:', suffix='Complete')

        for idx, output_data in enumerate(self.outputs):
            try:
                # Retrieve output from output_data
                output = output_data["output"]
                # Get the corresponding dataset row
                dataset_row = self.dataset[idx]
                input_data = dataset_row.get('input', {})
                expected_output = dataset_row.get('expected_output', {})

                # Perform evaluation
                evaluations = {}
                for evaluator in self.evaluators:
                    evaluation_result = evaluator(expected_output, output, input_data)
                    evaluations[evaluator.__name__] = evaluation_result

                # Prepare result data
                result = {
                    "output": output,
                    "evaluations": evaluations,
                    "metadata": output_data["metadata"],
                    "tags": self.tags,
                    "error": None #TODO: Add error handling
                }
            except Exception as e:
                result = {
                    "output": output_data.get('output'),
                    "evaluations": {},
                    "metadata": output_data["metadata"],
                    "tags": self.tags,
                    "error": str(e),
                }

            results_buffer.append(result)
            completed += 1
            _print_progress_bar(completed, total_rows, prefix='Evaluating:', suffix='Complete')

        self.has_evaluated = True
        self.results.experiment_rows = results_buffer

    def run(self, _jobs: int = 10) -> "ExperimentResults":
        """Execute the task and evaluations, returning the results."""
        self.run_task(_jobs=_jobs)
        self.run_evaluations()
        print()  # Move to the next line after completion
        return self.results

    def get_results(self) -> "ExperimentResults":
        if not self.has_evaluated:
            raise ValueError("Evaluations have not been performed yet. Please call run() or run_evaluations().")
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

    def push(self, overwrite: bool = False) -> None:
        """Push the experiment results to Datadog.

        Raises:
            ValueError: If the dataset hasn't been pushed to Datadog first
        """
        if not self.experiment.dataset._datadog_dataset_id:
            raise ValueError(
                "Dataset has not been pushed to Datadog. "
                "Please call dataset.push() before pushing experiment results."
            )

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
                            "config": self.experiment.config,
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
                            "config": self.experiment.config,
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

    def as_dataframe(self, multiindex: bool = True) -> "pd.DataFrame":
        """Convert the experiment results to a pandas DataFrame, including the experiment config.

        Args:
            multiindex (bool): If True, expand nested dictionaries into MultiIndex columns.
                               If False, keep the nested dictionaries as they are.

        Returns:
            pd.DataFrame: A DataFrame representation of the experiment results.

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

        # Collect data
        data = []
        for result in self.experiment_rows:
            record = {}
            # Get index of the dataset record
            idx = result['metadata'].get('dataset_record_idx')
            dataset_record = self.dataset[idx]

            if multiindex:

                # Flatten 'input' and 'expected_output' from the dataset
                for k, v in dataset_record.get('input', {}).items():
                    record[('input', k)] = v
                for k, v in dataset_record.get('expected_output', {}).items():
                    record[('expected_output', k)] = v

                # Flatten 'output' from the result
                output = result.get('output', {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        record[('output', k)] = v
                else:
                    record[('output', 'value')] = output

                # Flatten 'evaluations' from the result
                evaluations = result.get('evaluations', {})
                for evaluator_name, evaluation in evaluations.items():
                    if isinstance(evaluation, dict):
                        for k, v in evaluation.items():
                            record[('evaluations', evaluator_name, k)] = v
                    else:
                        record[('evaluations', evaluator_name)] = evaluation

                 # Flatten 'config' from the experiment, if it exists
                if self.experiment.config:
                    for k, v in self.experiment.config.items():
                        record[('config', k)] = v

                # Flatten 'metadata' from the result
                for k, v in result.get('metadata', {}).items():
                    # Skip project_name, experiment_name, and dataset_name
                    if k not in ['project_name', 'experiment_name', 'dataset_name']:
                        record[('metadata', k)] = v


                # Include 'error' if any
                error = result.get('error')
                if error:
                    record[('error', 'message')] = error

            else:
                # Include config as a dictionary, if it exists
                if self.experiment.config:
                    record['config'] = self.experiment.config

                # Keep nested dictionaries
                record['input'] = dataset_record.get('input', {})
                record['expected_output'] = dataset_record.get('expected_output', {})
                record['output'] = result.get('output', {})
                record['evaluations'] = result.get('evaluations', {})
                record['metadata'] = result.get('metadata', {})
                record['tags'] = result.get('tags', [])
                record['error'] = result.get('error')

            data.append(record)

        df = pd.DataFrame(data)
        if multiindex:
            # Set columns as MultiIndex
            df.columns = pd.MultiIndex.from_tuples(df.columns)
        return df

    def export_to_jsonl(self, file_path):
        """
        Exports the experiment results to a JSONL file.

        Args:
            file_path (str): The path to the output JSONL file.
        """
        import json

        with open(file_path, 'w') as f:
            for result in self.experiment_rows:
                json_line = json.dumps(result)
                f.write(json_line + '\n')


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
    resp = http_request(method, url, headers=headers, body=body)
    if resp.status_code >= 400:
        try:
            error_details = resp.json()
            error_message = error_details.get('errors', [{}])[0].get('detail', resp.text())
        except Exception:
            error_message = resp.text()
        raise ValueError(f"Request failed with status code {resp.status_code}: {error_message}")
    return resp


def task(func):
    if func.__name__ == "task":
        raise ValueError("Function name 'task' is reserved. Please use a different name for your task function.")
        
    @wraps(func)
    def wrapper(input: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Any:
        # Call the original function with or without config
        if 'config' in inspect.signature(func).parameters:
            return func(input, config)
        else:
            return func(input)
    # Enforce signature compliance
    sig = inspect.signature(func)
    params = sig.parameters
    if 'input' not in params:
        raise TypeError("Task function must have an 'input' parameter.")
    # Set attribute to indicate whether the function accepts config
    wrapper._accepts_config = 'config' in params
    wrapper._is_task = True  # Set attribute to indicate decoration
    return wrapper


def evaluator(func):
    @wraps(func)
    def wrapper(expected_output: Dict[str, Any], output: Any, input: Dict[str, Any] = None) -> Any:
            return func(expected_output, output, input)
    # Enforce signature compliance
    sig = inspect.signature(func)
    params = sig.parameters
    required_params = ['expected_output', 'output', 'input']
    if not all(param in params for param in required_params):
        raise TypeError(f"Evaluator function must have parameters {required_params}.")
    wrapper._is_evaluator = True  # Set attribute to indicate decoration
    return wrapper


class ExperimentGrid:
    """Class to run a grid of experiments over multiple parameter combinations.

    Attributes:
        name (str): Name of the experiment grid.
        task (Callable): The task function to execute.
        dataset (Dataset): The dataset to use.
        evaluators (List[Callable]): List of evaluator functions.
        config (Dict[str, List[Any]]): Parameter grid to run over.
        tags (List[str]): List of tags.
        project_name (str): Name of the project.
        description (str): Description of the experiment grid.
        metadata (Dict[str, Any]): Metadata dictionary.
        experiments (List[Experiment]): List of experiments created.
        results (List[ExperimentResults]): List of corresponding results.
    """

    def __init__(
        self,
        name: str,
        task: Callable,
        dataset: Dataset,
        evaluators: List[Callable],
        config: Dict[str, List[Any]],
        tags: List[str] = [],
        project_name: str = "-",
        description: str = "",
        metadata: Dict[str, Any] = {},
    ) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators
        self.config = config
        self.tags = tags
        self.project_name = project_name
        self.description = description
        self.metadata = metadata
        self.experiments = []
        self.results = []

        # Generate all parameter combinations and create experiments
        self._generate_experiments()

    def _generate_experiments(self):
        keys, values = zip(*self.config.items())
        param_combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]

        for params in param_combinations:
            # Create config for the experiment
            config = params.copy()

            # Compute hash of the config
            config_str = json.dumps(config, sort_keys=True)
            config_hash = hashlib.md5(config_str.encode('utf-8')).hexdigest()
            config_hash_tag = f"config_hash:{config_hash}"

            # Generate a unique name for each experiment
            experiment_name = f"{self.name}_" + "_".join(f"{k}_{v}" for k, v in params.items())

            # Create tags for parameters
            param_tags = [f"{k}:{v}" for k, v in params.items()] + [config_hash_tag]

            # Create a new experiment instance with updated config and name
            experiment = Experiment(
                name=experiment_name,
                task=self.task,
                dataset=self.dataset,
                evaluators=self.evaluators,
                tags=self.tags + param_tags,
                project_name=self.project_name,
                description=self.description,
                metadata={**self.metadata, "config": config},
                config=config,
            )

            # Add the experiment to the list without running it
            self.experiments.append(experiment)

    def __len__(self):
        return len(self.experiments)

    def __getitem__(self, index):
        return self.experiments[index]

    # Update the run method to use the pre-generated experiments
    def run(self, _jobs: int = 10):
        """Run experiments for all combinations of parameters in the grid.

        Args:
            _jobs (int): Number of parallel workers for each experiment run.
        """
        for experiment in self.experiments:
            experiment.run(_jobs=_jobs)
            self.results.append(experiment.get_results())

        return self.results

    def get_all_results(self) -> List[ExperimentResults]:
        """Return all results from the experiment grid.

        Returns:
            List[ExperimentResults]: A list of results for each experiment.
        """
        return self.results


def _print_progress_bar(iteration, total, prefix='', suffix='', decimals=1, length=50, fill='█'):
    percent = f"{100 * (iteration / float(total)):.{decimals}f}"
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end='\r')
    if iteration == total:
        print()
