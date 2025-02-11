import csv
import concurrent.futures
from enum import Enum
from functools import wraps
import hashlib
import inspect
import json
import os
import time
import traceback
from typing import Any, Callable, Dict, Iterator, List, Optional, Union
from urllib.parse import quote
import uuid

from ._utils import HTTPResponse
from ._utils import http_request

from .decorators import agent
from ._llmobs import LLMObs  

from ddtrace.context import Context

import ddtrace
from ddtrace import patch_all

# patch_all() # TODO: remove this comment if it messes with dist tracing, right now it's needed because it overrides integrations_enabled

DD_SITE = os.getenv("DD_SITE", "datadoghq.com")
if DD_SITE == "datadoghq.com":
    BASE_URL = f"https://api.{DD_SITE}"
else:
    BASE_URL = f"https://{DD_SITE}"

LLMObs.enable(
    ml_app="experiment-jonathan",
    integrations_enabled=True,
    agentless_enabled=True,
    site=os.getenv("DD_SITE"),
    api_key=os.getenv("DD_API_KEY"),
)

IS_INITIALIZED = False
ENV_ML_APP = None
ENV_PROJECT_NAME = None
ENV_SITE = None
ENV_API_KEY = None
ENV_APPLICATION_KEY = None

def init(project_name: str,  api_key: str = None, application_key: str = None, ml_app: str = "experiments", site: str = "datadoghq.com") -> None:
    """Initialize an experiment environment.

    Args:
        project_name: Name of the project
        api_key: Datadog API key
        application_key: Datadog application key
        ml_app: Name of the ML app
        site: Datadog site
    """

    global IS_INITIALIZED
    if IS_INITIALIZED:
        raise ValueError("Experiment environment already initialized, please call init() only once")
    else:
        if api_key is None:
            api_key = os.getenv("DD_API_KEY")
            if api_key is None:
                raise ValueError("DD_API_KEY environment variable is not set, please set it or pass it as an argument to init(api_key=...)")
        if application_key is None:
            application_key = os.getenv("DD_APPLICATION_KEY")
            if application_key is None:
                raise ValueError("DD_APPLICATION_KEY environment variable is not set, please set it or pass it as an argument to init(application_key=...)")

        ENV_ML_APP = ml_app
        ENV_PROJECT_NAME = project_name
        ENV_SITE = site
        ENV_API_KEY = api_key
        ENV_APPLICATION_KEY = application_key
        IS_INITIALIZED = True


class Dataset:
    """A container for LLM experiment data that can be pushed to and retrieved from Datadog.

    This class manages collections of input/output pairs used for LLM experiments,
    with functionality to validate, push to Datadog, and retrieve from Datadog.

    Attributes:
        name (str): Name of the dataset
        description (str): Optional description of the dataset
    """

    def __init__(self, name: str, data: Optional[List[Dict[str, Union[str, Dict[str, Any]]]]] = None, description: str = "") -> None:
        """
        Args:
            name: Name of the dataset
            data: List of dictionaries where 'input' and 'expected_output' values can be
                 either strings or dictionaries of strings. If None, attempts to pull from Datadog.
            description: Optional description of the dataset
        """
        self.name = name
        self.description = description
        self.version = 0
        

        # If no data provided, attempt to pull from Datadog
        if data is None:
            print(
                f"No data provided, pulling dataset '{name}' from Datadog..."
            )
            pulled_dataset = self.pull(name)
            self._data = pulled_dataset._data
            self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
            self._version = pulled_dataset._datadog_dataset_version
        else:
            self._validate_data(data)
            self._data = data
            self._datadog_dataset_id = None
            self._version = 0

    def __iter__(self) -> Iterator[Dict[str, Union[str, Dict[str, Any]]]]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: int) -> Dict[str, Union[str, Dict[str, Any]]]:
        """Get a dataset record.
        
        Args:
            index: Index of the record to retrieve
            
        Returns:
            Dict containing the record.
        """
        record = self._data[index].copy()
        return record

    def _validate_data(self, data: List[Dict[str, Union[str, Dict[str, Any]]]]) -> None:
        """Validate the format and structure of dataset records.

        Args:
            data: List of dataset records to validate

        Raises:
            ValueError: If data is empty, contains non-dictionary rows,
                       has inconsistent keys, or exceeds 50,000 rows
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
        dataset_version = datasets[0]["attributes"]["current_version"]
        

        # Get dataset records
        url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
        resp = exp_http_request("GET", url)
        records_data = resp.json()

        if not records_data.get("data", []):
            raise ValueError(f"Dataset '{name}' does not contain any records.")

        # Transform records into the expected format
        class_records = []
        for record in records_data.get("data", []):
            attrs = record.get("attributes", {})
            input_data = attrs.get("input")
            expected_output = attrs.get("expected_output")
                
            class_records.append({
                "record_id": record.get("id"),
                "input": input_data,
                "expected_output": expected_output,
                **attrs.get("metadata", {}),
            })

        # Create new dataset instance
        dataset = cls(name, class_records)
        dataset._datadog_dataset_id = dataset_id
        dataset._datadog_dataset_version = dataset_version
        return dataset

    def push(self, chunk_size: int = 300) -> None:
        """Push the dataset to Datadog and refresh with pulled data.

        Args:
            chunk_size: Number of records to upload in each chunk. Defaults to 300.
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
            self._datadog_dataset_version = 0
        else:
            # Dataset exists, raise error
            raise ValueError(
                f"Dataset '{self.name}' already exists. Dataset versioning will be supported in a future release. "
                "Please use a different name for your dataset."
            )

        # Split records into chunks and upload
        total_records = len(self._data)
        chunks = [self._data[i:i + chunk_size] for i in range(0, total_records, chunk_size)]
        total_chunks = len(chunks)

        # Only show progress bar for large datasets
        show_progress = total_records > chunk_size
        if show_progress:
            _print_progress_bar(0, total_chunks, prefix='Uploading:', suffix='Complete')

        for i, chunk in enumerate(chunks):
            records_payload = {"data": {"type": "datasets", "attributes": {"records": chunk}}}
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            resp = exp_http_request("POST", url, body=json.dumps(records_payload).encode("utf-8"))
            
            if show_progress:
                _print_progress_bar(i + 1, total_chunks, prefix='Uploading:', suffix='Complete')

        # Pull the dataset to get all record IDs and metadata
        pulled_dataset = self.pull(self.name)
        self._data = pulled_dataset._data
        self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
        self._datadog_dataset_version = pulled_dataset._datadog_dataset_version

        # Print url to the dataset in Datadog
        print(f"\nDataset '{self.name}' created: {BASE_URL}/llm/experiments/datasets/{dataset_id}\n")

    @classmethod
    def from_csv(
        cls,
        filepath: str,
        name: str,
        description: str = "",
        delimiter: str = ",",
        input_columns: List[str] = None,
        expected_output_columns: List[str] = None,
    ) -> "Dataset":
        """Create a Dataset from a CSV file.

        Args:
            filepath: Path to the CSV file
            name: Name of the dataset
            description: Optional description of the dataset
            delimiter: CSV delimiter character, defaults to comma
            input_columns: List of column names to use as input data
            expected_output_columns: List of column names to use as expected output data

        Returns:
            Dataset: A new Dataset instance containing the CSV data

        Raises:
            ValueError: If input_columns or expected_output_columns are not provided
            Exception: If there are issues reading the CSV file
        """
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

                if missing_input_columns:
                    raise ValueError(f"Input columns not found in CSV header: {missing_input_columns}")
                if missing_output_columns:
                    raise ValueError(f"Expected output columns not found in CSV header: {missing_output_columns}")

                # Get metadata columns (all columns not used for input or expected output)
                metadata_columns = [col for col in header_columns if col not in input_columns and col not in expected_output_columns]

                for row in rows:
                    # Handle input data
                    if len(input_columns) == 1:
                        input_data = row[input_columns[0]]
                    else:
                        input_data = {col: row[col] for col in input_columns}

                    # Handle expected output data
                    if len(expected_output_columns) == 1:
                        expected_output_data = row[expected_output_columns[0]]
                    else:
                        expected_output_data = {col: row[col] for col in expected_output_columns}

                    # Handle metadata (all remaining columns)
                    metadata = {col: row[col] for col in metadata_columns}

                    data.append({
                        'input': input_data,
                        'expected_output': expected_output_data,
                        **metadata,
                    })
        except FileNotFoundError as e:
            raise DatasetFileError(f"CSV file not found: {filepath}") from e
        except PermissionError as e:
            raise DatasetFileError(f"Permission denied when reading CSV file: {filepath}") from e
        except csv.Error as e:
            raise DatasetFileError(f"Error parsing CSV file: {e}") from e
        except Exception as e:
            raise DatasetFileError(f"Unexpected error reading CSV file: {e}") from e

        return cls(name=name, data=data, description=description)


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
            column_tuples = set()
            data_rows = []
            for record in self._data:
                flat_record = {}

                # Handle 'input' fields
                input_data = record.get('input', {})
                if isinstance(input_data, dict):        
                    for k, v in input_data.items():
                        flat_record[('input', k)] = v
                        column_tuples.add(('input', k))
                else:
                    flat_record[('input', '')] = input_data
                    column_tuples.add(('input', ''))

                # Handle 'expected_output' fields
                expected_output = record.get('expected_output', {})
                if isinstance(expected_output, dict):
                    for k, v in expected_output.items():
                        flat_record[('expected_output', k)] = v
                        column_tuples.add(('expected_output', k))
                else:
                    flat_record[('expected_output', '')] = expected_output
                    column_tuples.add(('expected_output', ''))

                # Handle any other top-level fields
                for k, v in record.items():
                    if k not in ['input', 'expected_output']:
                        flat_record[('metadata', k)] = v
                        column_tuples.add(('metadata', k))
                data_rows.append(flat_record)

            # Convert column_tuples to a sorted list to maintain consistent column order
            column_tuples = sorted(list(column_tuples))

            # Build the DataFrame
            records_list = []
            for flat_record in data_rows:
                row = [flat_record.get(col, None) for col in column_tuples]
                records_list.append(row)

            df = pd.DataFrame(records_list, columns=pd.MultiIndex.from_tuples(column_tuples))

            return df

        data = []
        for record in self._data:
            new_record = {}
            input_data = record.get('input', {})
            new_record['input'] = input_data
            expected_output = record.get('expected_output', {})
            new_record['expected_output'] = expected_output
            # Copy other fields
            for k, v in record.items():
                if k not in ['input', 'expected_output']:
                    new_record[k] = v
            data.append(new_record)
        return pd.DataFrame(data)

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
        description (str): Description of the experiment
        metadata (Dict[str, Any]): Additional metadata for the experiment
        config (Optional[Dict[str, Any]]): Configuration for the task
        has_run (bool): Whether the experiment has been executed
        has_evaluated (bool): Whether the evaluations have been performed
        outputs (List[Dict]): Outputs after running the task
        evaluations (List[Dict]): Evaluation results after running evaluators
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

        # Make sure the task is decorated with @task
        if not hasattr(self.task, "_is_task"):
            raise TypeError("Task function must be decorated with @task decorator.")

        # Make sure every evaluator is decorated with @evaluator
        for evaluator_func in self.evaluators:
            if not hasattr(evaluator_func, "_is_evaluator"):
                raise TypeError(
                    f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator."
                )

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
        Internal helper to retrieve or create a project in Datadog, returning the project_id.
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
            return response_data["data"]["id"]
        else:
            return projects[0]["id"]

    def _create_experiment_in_datadog(self) -> str:
        """
        Internal helper to create an experiment in Datadog, returning the new experiment_id.
        Raises ValueError if the dataset hasn't been pushed (no _datadog_dataset_id).
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
                    "dataset_version": self.dataset._datadog_dataset_version,
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
        jobs: int = 10,
        raise_errors: bool = True,
    ) -> "ExperimentResults":
        """
        Execute the task and evaluations, returning the results.
        Here, we guarantee an experiment is created first,
        so run_task() can tag traces with the real experiment ID.
        """
        print("Running experiment...")
        # 1) Make sure the dataset is pushed
        if not self.dataset._datadog_dataset_id:
            raise ValueError(
                "Dataset must be pushed to Datadog before running the experiment."
            )

        # 2) Create project + experiment if this hasn't been done yet
        if not self._datadog_experiment_id:
            project_id = self._get_or_create_project()  # your existing helper
            self._datadog_project_id = project_id
            
            experiment_id = self._create_experiment_in_datadog()  # your existing helper
            self._datadog_experiment_id = experiment_id

        # 3) Now run the task and evaluations
        self.run_task(_jobs=jobs, raise_errors=raise_errors)
        experiment_results = self.run_evaluations(raise_errors=raise_errors)
        return experiment_results

    def run_task(self, _jobs: int = 10, raise_errors: bool = True) -> None:
        """
        Execute the task function on the dataset concurrently using ThreadPoolExecutor.map,
        updating progress via _print_progress_bar and processing more rows in parallel.
        """
        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "True"
        total_rows = len(self.dataset)

        def process_row(idx_row):
            idx, row = idx_row
            start_time = time.time()
            with LLMObs._experiment(name=self.task.__name__) as span:
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

                     # Periodic flush for concurrency
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

                    # Periodic flush for concurrency
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
                        }
                    }

        outputs_buffer = []
        completed = 0
        error_count = 0


        # Using ThreadPoolExecutor.map to process rows concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=_jobs) as executor:
            # executor.map returns results in order, so we iterate and update our progress
            for result in executor.map(process_row, list(enumerate(self.dataset))):
                outputs_buffer.append(result)
                completed += 1
                _print_progress_bar(completed, total_rows, prefix="Processing:", suffix="Complete")

        # Check for errors and raise if required
        error_count = 0
        for idx, output_data in enumerate(outputs_buffer):
            if output_data["error"]["message"]:
                error_count += 1
                if raise_errors:
                    raise ExperimentTaskError(
                        output_data["error"]["message"],
                        idx,
                        output_data["error"]["type"]
                    )
        self.outputs = outputs_buffer
        self.has_run = True

        LLMObs.flush()

        error_rate = (error_count / total_rows) * 100
        os.environ["DD_EXPERIMENTS_RUNNER_ENABLED"] = "False"
        os.environ["DD_LLMOBS_ENABLED"] = "False"
        print(f"Task completed with {error_count} errors ({error_rate:.2f}% error rate)")
        if error_count > 0:
            print(
                "If you'd like to halt execution on errors and see the full traceback, "
                "set `raise_errors=True` when running the experiment."
            )

    def run_evaluations(
        self,
        evaluators: Optional[List[Callable]] = None,
        raise_errors: bool = False
    ) -> "ExperimentResults":
        """Run evaluators on the outputs and return ExperimentResults.
        
        Args:
            evaluators (Optional[List[Callable]]): List of evaluators to use. If None, uses the experiment's evaluators.
            raise_errors (bool): If True, raises exceptions encountered during evaluation.
        
        Returns:
            ExperimentResults: A new ExperimentResults instance with the evaluation results.
        
        Raises:
            ValueError: If task has not been run yet
        """
        if not self.has_run:
            raise ValueError("Task has not been run yet. Please call run_task() before run_evaluations().")

        # Use provided evaluators or fall back to experiment's evaluators
        evaluators_to_use = evaluators if evaluators is not None else self.evaluators

        # Validate that all evaluators have the @evaluator decorator
        for evaluator_func in evaluators_to_use:
            if not hasattr(evaluator_func, '_is_evaluator'):
                raise TypeError(f"Evaluator '{evaluator_func.__name__}' must be decorated with @evaluator decorator.")

        evaluations = []
        total_rows = len(self.outputs)
        completed = 0
        error_count = 0

        _print_progress_bar(0, total_rows, prefix='Evaluating:', suffix='Complete')

        for idx, output_data in enumerate(self.outputs):
            output = output_data["output"]
            dataset_row = self.dataset[idx]
            input_data = dataset_row.get('input', {})
            expected_output = dataset_row.get('expected_output', {})
            
            evaluations_dict = {}

            # Run all evaluators for this output
            for evaluator in evaluators_to_use:
                try:
                    evaluation_result = evaluator(input_data, output, expected_output)
                    evaluations_dict[evaluator.__name__] = {
                        "value": evaluation_result,
                        "error": None
                    }
                except Exception as e:
                    error_count += 1
                    evaluations_dict[evaluator.__name__] = {
                        "value": None,
                        "error": {
                            "message": str(e),
                            "type": type(e).__name__,
                            "stack": traceback.format_exc(),
                        }
                    }
                    if raise_errors:
                        raise e

            # Add single evaluation entry for this output
            evaluations.append({
                "idx": idx,
                "evaluations": evaluations_dict
            })

            completed += 1
            _print_progress_bar(completed, total_rows, prefix='Evaluating:', suffix='Complete')

        if len(evaluators_to_use) > 0:
            error_rate = (error_count / (total_rows * len(evaluators_to_use))) * 100
        else:
            error_rate = 0

        print(f"Evaluation completed with {error_count} errors ({error_rate:.2f}% error rate)")

        if error_count > 0:
            print("If you'd like to halt execution on errors and see the full traceback, set `raise_errors=True` when running the experiment.")

        self.has_evaluated = True
        return ExperimentResults(self.dataset, self, self.outputs, evaluations)

 

class ExperimentResults:
    """Contains and manages the results of an experiment run.

    Stores the outputs, evaluations, and metadata for each record processed
    in an experiment, with functionality to analyze and push results to Datadog.

    Attributes:
        dataset (Dataset): The dataset used in the experiment
        experiment (Experiment): The experiment that generated these results
        outputs (List[Dict]): Outputs after running the task
        evaluations (List[Dict]): Evaluation results after running evaluators
    """

    def __init__(self, dataset: Dataset, experiment: Experiment, outputs: List[Dict], evaluations: List[Dict]) -> None:
        self.dataset = dataset
        self.experiment = experiment
        self.outputs = outputs  # List of outputs from run_task
        self.evaluations = evaluations  # List of evaluations from run_evaluations
        self.merged_results = self._merge_results()  # Merged outputs and evaluations

    def _merge_results(self) -> List[Dict[str, Any]]:
        """Merge outputs and evaluations into a single list of results."""
        merged_results = []
        for idx in range(len(self.outputs)):
            output_data = self.outputs[idx]
            evaluation_data = self.evaluations[idx]
            dataset_record = self.dataset._data[idx]

            # Get base metadata and add tags to it
            metadata = output_data.get('metadata', {})
            metadata['tags'] = self.experiment.tags

            merged_result = {
                "idx": idx,
                "record_id": dataset_record.get('record_id'),
                "input": dataset_record.get('input', {}),
                "expected_output": dataset_record.get('expected_output', {}),
                "output": output_data.get('output'),
                "evaluations": evaluation_data.get('evaluations', {}),
                "metadata": metadata,
                "error": output_data.get('error'),
            }
            merged_results.append(merged_result)
        return merged_results

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.merged_results)

    def __len__(self) -> int:
        return len(self.merged_results)

    def __getitem__(self, index: int) -> Any:
        """Get a result record.
        
        Args:
            index: Index of the record to retrieve
            
        Returns:
            Dict containing the record.
        """
        result = self.merged_results[index].copy()
        return result

    def as_dataframe(self, multiindex: bool = True) -> "pd.DataFrame":
        """Convert the experiment results to a pandas DataFrame.

        Args:
            multiindex (bool): If True, expand input/output/expected_output dictionaries into MultiIndex columns.
                            If False, keep the nested dictionaries as they are.

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

        # Convert merged_results to DataFrame directly
        df = pd.DataFrame(self.merged_results)
        
        if not multiindex:
            return df
        
        # Process input, output, and expected_output with MultiIndex
        special_fields = ['input', 'output', 'expected_output']
        result_dfs = []
        
        # Handle special fields (input, output, expected_output)
        for field in special_fields:
            if field not in df.columns:
                continue
                
            # Get the first non-null value to check type
            first_value = next((v for v in df[field] if v is not None), None)
            
            if isinstance(first_value, dict):
                # For dictionary values, expand into columns
                field_df = pd.json_normalize(df[field].values)
            else:
                # For simple values, use 'value' as the subcolumn
                field_df = pd.DataFrame({'value': df[field].values})
            
            # Create MultiIndex columns for this field
            field_df.columns = pd.MultiIndex.from_tuples([(field, col) for col in field_df.columns])
            result_dfs.append(field_df)
        
        # Add all other columns as-is
        other_cols = [col for col in df.columns if col not in special_fields]
        if other_cols:
            other_df = df[other_cols]
            result_dfs.append(other_df)
        
        # Combine all DataFrames
        final_df = pd.concat(result_dfs, axis=1)
        
        # Replace NaN with None
        final_df = final_df.where(pd.notna(final_df), None)
        
        return final_df

    def push(self, chunk_size: int = 300) -> None:
        """
        Push the experiment results to Datadog, without re-creating the project/experiment.
        Assumes self.experiment._datadog_experiment_id and self.experiment._datadog_project_id
        have already been set in Experiment.run().
        """
        # Ensure the dataset is hosted in Datadog
        if not self.experiment.dataset._datadog_dataset_id:
            raise ValueError(
                "Dataset has not been pushed to Datadog. "
                "Please call dataset.push() before pushing experiment results."
            )

        # Ensure the experiment was already created (via run())
        if not self.experiment._datadog_experiment_id:
            raise ValueError(
                "Experiment has not been created in Datadog. "
                "Please call experiment.run() before pushing results."
            )

        # Grab IDs from the already-created experiment
        experiment_id = self.experiment._datadog_experiment_id
        project_id = self.experiment._datadog_project_id
        experiment_name = self.experiment.name

        # Now proceed with chunked uploading of your results — no project or experiment creation here.

        total_results = len(self.merged_results)
        # Optional progress bar
        show_progress = total_results > chunk_size

        # Just an example of how you'd do chunked uploads:
        chunks = [
            self.merged_results[i : i + chunk_size]
            for i in range(0, total_results, chunk_size)
        ]
        total_chunks = len(chunks)

        if show_progress:
            print(f"\nUploading {total_results} results in {total_chunks} chunks...")
            _print_progress_bar(0, total_chunks, prefix='Uploading:', suffix='Complete')

        for chunk_idx, chunk in enumerate(chunks):
            spans = []
            metrics = []
            
            # Process each result in the chunk
            for result in chunk:
                idx = result['idx']
                merged_result = result
                output = merged_result.get('output')
                record_id = merged_result.get('record_id')
                input = merged_result.get('input', {})
                evaluations = merged_result.get('evaluations', {})
                expected_output = merged_result.get('expected_output', {})
                error = merged_result.get('error', {})
                metadata = merged_result.get('metadata', {})
                span_id = metadata.get('span_id')
                trace_id = metadata.get('trace_id')


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
                        # "join_on": {
                        #     "span": {
                        #         "trace_id": str(trace_id),
                        #         "span_id": str(span_id),
                        #     },
                        # }
                    }

                    metrics.append(metric)
                    

            chunk_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {"scope": "experiments", "metrics": metrics, "tags": self.experiment.tags + ["ddtrace.version:" + ddtrace.__version__, "experiment_id:" + experiment_id]}, 
                }
            }

            url = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
            exp_http_request("POST", url, body=json.dumps(chunk_payload).encode("utf-8"))

            if show_progress:
                _print_progress_bar(
                    chunk_idx + 1, total_chunks, prefix='Uploading:', suffix='Complete'
                )

        print(f"\nExperiment '{experiment_name}' results pushed to Datadog.\n")

    def export_to_jsonl(self, file_path):
        """
        Exports the experiment results to a JSONL file.

        Args:
            file_path (str): The path to the output JSONL file.
        """
        import json

        with open(file_path, 'w') as f:
            for result in self.merged_results:
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
    full_url = BASE_URL + url
    resp = http_request(method, full_url, headers=headers, body=body)
    if resp.status_code == 403:
        if not DD_SITE:
            raise ValueError("DD_SITE may be incorrect. Please check your DD_SITE environment variable.")
        else:
            raise ValueError("DD_API_KEY or DD_APPLICATION_KEY is incorrect.")
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
    def wrapper(input: Dict[str, Union[str, Dict[str, Any]]], config: Optional[Dict[str, Any]] = None) -> Any:
        # Call the original function with or without config
        if 'config' in inspect.signature(func).parameters:
            return func(input, config)
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
    def wrapper(input: Union[str, Dict[str, Any]] = None, output: Union[str, Dict[str, Any]] = None, expected_output: Union[str, Dict[str, Any]] = None) -> Any:
        return func(input, output, expected_output)
    # Enforce signature compliance
    sig = inspect.signature(func)
    params = sig.parameters
    required_params = ['input', 'output', 'expected_output']
    if not all(param in params for param in required_params):
        raise TypeError(f"Evaluator function must have parameters {required_params}.")
    wrapper._is_evaluator = True  # Set attribute to indicate decoration
    return wrapper


def _print_progress_bar(iteration, total, prefix='', suffix='', decimals=1, length=50, fill='█'):
    percent = f"{100 * (iteration / float(total)):.{decimals}f}"
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    # Use carriage return '\r' to overwrite the line
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end='\r', flush=True)
    if iteration == total:
        print()  # Move to the next line after completion

class DatasetFileError(Exception):
    """Exception raised when there are errors reading or processing dataset files."""
    pass


class ExperimentTaskError(Exception):
    """Exception raised when a task fails during experiment execution."""
    def __init__(self, message: str, row_idx: int, original_error: Exception = None):
        self.row_idx = row_idx
        self.original_error = original_error
        super().__init__(message)