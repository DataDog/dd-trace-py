import csv
import concurrent.futures
from enum import Enum
from functools import wraps
import inspect
import json
import os
import time
import traceback
from typing import Any, Callable, Dict, Iterator, List, Optional, Union
from urllib.parse import quote
import uuid

from .._utils import HTTPResponse
from .._utils import http_request
from ..utils import Prompt

from ..decorators import agent
from .._llmobs import LLMObs  

from ddtrace.context import Context

import ddtrace
from ddtrace import patch_all

# TODO: Clean up this, move logic to init
DD_SITE = os.getenv("DD_SITE", "datadoghq.com")
if DD_SITE == "datadoghq.com":
    BASE_URL = f"https://api.{DD_SITE}"
else:
    BASE_URL = f"https://{DD_SITE}"


IS_INITIALIZED = False
ENV_PROJECT_NAME = None
ENV_SITE = None
ENV_API_KEY = None
ENV_APPLICATION_KEY = None
ML_APP = "dne"
RUN_LOCALLY = False


def init(project_name: str, site: str = "datadoghq.com", api_key: str = None, application_key: str = None, run_locally: bool = False) -> None:
    """Initialize an experiment environment.

    Args:
        project_name: Name of the project
        site: Datadog site
        api_key: Datadog API key
        application_key: Datadog application key
        run_locally: If True, run locally and don't send any data to Datadog
    """

    global IS_INITIALIZED, ENV_PROJECT_NAME, ENV_SITE, ENV_API_KEY, ENV_APPLICATION_KEY, RUN_LOCALLY

    if run_locally:
        RUN_LOCALLY = True
       
        return

    if api_key is None:
        api_key = os.getenv("DD_API_KEY")
        if api_key is None:
            raise ValueError("DD_API_KEY environment variable is not set, please set it or pass it as an argument to init(...api_key=...)")
        
    if application_key is None:
        application_key = os.getenv("DD_APPLICATION_KEY")
        if application_key is None:
            raise ValueError("DD_APPLICATION_KEY environment variable is not set, please set it or pass it as an argument to init(...application_key=...)")


    if IS_INITIALIZED is False:
        LLMObs.enable(
            ml_app=ML_APP,
            integrations_enabled=True,
            agentless_enabled=True,
            site=site,
            api_key=api_key,
        )
        
    ENV_PROJECT_NAME = project_name
    ENV_SITE = site
    ENV_API_KEY = api_key
    ENV_APPLICATION_KEY = application_key
    IS_INITIALIZED = True

def _validate_init() -> None:
    if IS_INITIALIZED is False:
        raise ValueError("Environment not initialized, please call ddtrace.llmobs.experiments.init() at the top of your script before calling any other functions")
    
def _is_locally_initialized() -> bool:
    return RUN_LOCALLY

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
            print(pulled_dataset._datadog_dataset_id)
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
    def pull(cls, name: str, version: int = None) -> "Dataset":
        """Create a dataset from a dataset hosted in Datadog.

        Args:
            name: Name of the dataset to retrieve from Datadog
            version: Version of the dataset to retrieve from Datadog

        Returns:
            Dataset: A new Dataset instance populated with the records from Datadog

        Raises:
            ValueError: If the dataset is not found or if the specified version doesn't exist
            Exception: If there are HTTP errors during the request
        """
        _validate_init()
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

        print(f"Found dataset '{name}' (latest version: {dataset_version})")

        # If version specified, verify it exists
        if version is not None:
            versions_url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/versions"
            try:
                versions_resp = exp_http_request("GET", versions_url)
                versions_data = versions_resp.json()
                available_versions = [v["attributes"]["version_number"] for v in versions_data.get("data", [])]
                
                if not available_versions:
                    raise ValueError(f"No versions found for dataset '{name}'")
                
                if version not in available_versions:
                    versions_str = ", ".join(str(v) for v in sorted(available_versions))
                    raise ValueError(
                        f"Version {version} not found for dataset '{name}'. "
                        f"Available versions: {versions_str}"
                    )
                dataset_version = version
            except ValueError as e:
                if "404" in str(e):
                    raise ValueError(f"Dataset '{name}' not found") from e
                raise

        # Get dataset records
        url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
        if version is not None:
            url += f"?filter[version]={version}"
        
        try:
            resp = exp_http_request("GET", url)
            records_data = resp.json()
        except ValueError as e:
            if "404" in str(e):
                if version is not None:
                    versions_str = ", ".join(str(v) for v in sorted(available_versions))
                    raise ValueError(
                        f"Version {version} not found for dataset '{name}'. "
                        f"Available versions: {versions_str}"
                    ) from e
                raise ValueError(f"Dataset '{name}' not found") from e
            raise

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

    def push(self, overwrite: bool = False) -> None:
        """Push the dataset to Datadog and refresh with pulled data.

        Args:
            overwrite: If True, overwrite the dataset if it already exists. If False, create a new version. This is ignored if the dataset does not exist.
        """
        _validate_init()

        chunk_size: int = 300

        # First check if dataset exists
        encoded_name = quote(self.name)
        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
        resp = exp_http_request("GET", url)
        existing_dataset = resp.json().get("data", [])

        if existing_dataset and overwrite:
            # Use existing dataset ID
            dataset_id = existing_dataset[0]["id"]
            self._datadog_dataset_id = dataset_id
            self._datadog_dataset_version = existing_dataset[0]["attributes"]["current_version"]

            # Get existing records to map record IDs
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            resp = exp_http_request("GET", url)
            existing_records = resp.json().get("data", [])
            
            # Update records in chunks
            total_records = len(self._data)
            chunks = [self._data[i:i + chunk_size] for i in range(0, total_records, chunk_size)]
            total_chunks = len(chunks)

            show_progress = total_records > chunk_size
            if show_progress:
                _print_progress_bar(0, total_chunks, prefix='Updating:', suffix='Complete')

            for i, chunk in enumerate(chunks):
                for record, existing in zip(chunk, existing_records[i*chunk_size:(i+1)*chunk_size]):
                    record_id = existing["id"]
                    payload = {
                        "data": {
                            "type": "datasets",
                            "attributes": {
                                "input": record["input"],
                                "expected_output": record["expected_output"],
                                "metadata": {k: v for k, v in record.items() 
                                           if k not in ["input", "expected_output", "record_id"]}
                            }
                        }
                    }
                    url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records/{record_id}"
                    resp = exp_http_request("PATCH", url, body=json.dumps(payload).encode("utf-8"))

                if show_progress:
                    _print_progress_bar(i + 1, total_chunks, prefix='Updating:', suffix='Complete')

        else:
            # Create new dataset
            dataset_payload = {
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "name": self.name,
                        "description": self.description,
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

            time.sleep(1) # Sleep to allow for processing after ingestion.
            # Pull the dataset to get all record IDs and metadata
            pulled_dataset = self.pull(self.name)
            self._data = pulled_dataset._data
            self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
            self._datadog_dataset_version = pulled_dataset._datadog_dataset_version

            # Print url to the dataset in Datadog
            print(f"\nDataset '{self.name}' created: {BASE_URL}/llm/testing/datasets/{dataset_id}\n")

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

    def __repr__(self) -> str:
        """Rich string representation of the Dataset.
        
        Shows dataset name, size, structure, and Datadog sync status.
        """
        # Get basic dataset info
        name = f"{Color.CYAN}{self.name}{Color.RESET}"
        record_count = len(self._data)
        
        # Get sample structure from first record
        structure = []
        if self._data:
            sample = self._data[0]
            # Input structure
            input_data = sample.get('input', {})
            if isinstance(input_data, dict):
                input_desc = f"dict[{len(input_data)} keys]"
            else:
                input_desc = type(input_data).__name__
            structure.append(f"input: {input_desc}")
            
            # Expected output structure
            expected = sample.get('expected_output', {})
            if isinstance(expected, dict):
                output_desc = f"dict[{len(expected)} keys]"
            else:
                output_desc = type(expected).__name__
            structure.append(f"expected_output: {output_desc}")
            
            # Metadata fields
            metadata = {k: v for k, v in sample.items() 
                       if k not in ['input', 'expected_output', 'record_id']}
            if metadata:
                structure.append(f"metadata: {len(metadata)} fields")
        
        # Datadog sync status
        if self._datadog_dataset_id:
            dd_status = f"{Color.GREEN}✓ Synced{Color.RESET} (v{self._version})"
            dd_url = f"\n  URL: {Color.BLUE}{BASE_URL}/llm/testing/datasets/{self._datadog_dataset_id}{Color.RESET}"
        else:
            dd_status = f"{Color.YELLOW}Local only{Color.RESET}"
            dd_url = ""
        
        # Build the representation
        info = [
            f"Dataset(name={name})",
            f"  Records: {record_count:,}",
            f"  Structure: {', '.join(structure)}",
            f"  Datadog: {dd_status}"
        ]
        
        # Add description if present
        if self.description:
            desc_preview = (self.description[:47] + '...') if len(self.description) > 50 else self.description
            info.insert(1, f"  Description: {desc_preview}")
        
        # Add URL if dataset is synced
        if dd_url:
            info.append(dd_url)
        
        return '\n'.join(info)


class Experiment:
    """Manages the execution and evaluation of tasks on a dataset.

    This class handles running tasks against datasets, applying evaluators,
    and collecting results for analysis.

    Attributes:
        name (str): Name of the experiment
        task (Callable): Function that processes each dataset record
        dataset (Dataset): Dataset to run the experiment on
        prompt (Prompt): Prompt template for the experiment
        evaluators (List[Callable]): Functions that evaluate task outputs
        tags (List[str]): Tags for organizing experiments
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
        prompt: Prompt,
        evaluators: List[Callable],
        tags: List[str] = [],
        description: str = "",
        metadata: Dict[str, Any] = {},
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.prompt = prompt
        self.evaluators = evaluators
        self.tags = tags
        self.project_name = ENV_PROJECT_NAME
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
    raise_errors: bool = False,
    sample_size: Optional[int] = None,
) -> "ExperimentResults":
        """
        Execute the task and evaluations, returning the results.
        If sample_size is provided, run on just that many rows from the dataset (subset).
        Otherwise, run on the entire dataset.
        """
        if not _is_locally_initialized():
            _validate_init()

        # Handle logging/feedback:
        if sample_size is not None:
            print(f"\n{Color.BOLD}Running experiment (subset of {sample_size} rows){Color.RESET}")
        else:
            print(f"\n{Color.BOLD}Running experiment{Color.RESET}")
        print(f"  Project: {Color.CYAN}{self.project_name}{Color.RESET}")
        print(f"  Name: {Color.CYAN}{self.name}{Color.RESET}")
        print(f"  raise_errors={raise_errors}\n")

        # 1) Make sure the dataset is pushed
        if not _is_locally_initialized():
            if not self.dataset._datadog_dataset_id:
                raise ValueError("Dataset must be pushed to Datadog before running the experiment.")

        # 2) Create/get the project, then create an experiment in Datadog.
        if not _is_locally_initialized():
            project_id = self._get_or_create_project()
            self._datadog_project_id = project_id
            experiment_id = self._create_experiment_in_datadog()
            self._datadog_experiment_id = experiment_id

        # If a sample_size is given, slice the dataset to that many records
        # to create a temporary subset that lines up with the partial output.
        from copy import deepcopy
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
                        }
                    }

        _jobs = 5 if sample_size else jobs
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
                    evaluations_dict[evaluator_func.__name__] = {
                        "value": evaluation_result,
                        "error": None
                    }
                except Exception as e:
                    evaluations_dict[evaluator_func.__name__] = {
                        "value": None,
                        "error": {
                            "message": str(e),
                            "type": type(e).__name__,
                            "stack": traceback.format_exc(),
                        }
                    }
                    if raise_errors:
                        raise RuntimeError(
                            f"Evaluator '{evaluator_func.__name__}' failed on row {idx}: {e}"
                        ) from e

            evaluations_partial.append({
                "idx": idx,
                "evaluations": evaluations_dict
            })
            progress_eval.update(error=any(e["error"] is not None for e in evaluations_dict.values()))

        # If sample_size was not used, we can store these as the main experiment outputs/evals:
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


    def run_task(self, jobs: int = 10, raise_errors: bool = False) -> None:
        """
        Execute the task function on the dataset concurrently, terminating early if raise_errors is True
        and an exception occurs (on the first error).
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
                        prompt=self.prompt,
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
                        prompt=self.prompt,
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
                        }
                    }

        # Using ThreadPoolExecutor.map to process rows concurrently
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
        if not _is_locally_initialized():
            _validate_init()
        
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
        
        progress = ProgressReporter(total_rows, desc="Evaluating")

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
            
            progress.update(error=any(e["error"] is not None for e in evaluations_dict.values()))

        self.has_evaluated = True
        experiment_results = ExperimentResults(self.dataset, self, self.outputs, evaluations)
        experiment_results._push_evals()
        return experiment_results
    




    def __repr__(self) -> str:
        """Rich string representation of the Experiment.
        
        Shows experiment configuration, task details, evaluators, and run status
        with color-coded indicators and formatted statistics.
        """
        # Basic experiment info with color
        name = f"{Color.CYAN}{self.name}{Color.RESET}"
        project = f"{Color.CYAN}{self.project_name}{Color.RESET}"
        
        # Task info
        task_name = self.task.__name__
        task_doc = self.task.__doc__
        task_preview = f"{task_name}"
        if task_doc:
            # Get first line of docstring
            first_line = task_doc.splitlines()[0].strip()
            task_preview += f" ({first_line})"
        
        # Dataset info
        dataset_info = (
            f"{self.dataset.name} "
            f"({len(self.dataset):,} records)"
        )
        
        # Evaluators info
        evaluator_names = [e.__name__ for e in self.evaluators]
        evaluator_info = (
            f"{len(evaluator_names)} evaluator"
            f"{'s' if len(evaluator_names) != 1 else ''}"
        )
        
        # Config preview
        config_preview = ""
        if self.config:
            # Format first few config items
            items = list(self.config.items())[:3]
            preview = ", ".join(f"{k}: {repr(v)}" for k, v in items)
            if len(self.config) > 3:
                preview += f" + {len(self.config) - 3} more"
            config_preview = f"\n  Config: {preview}"
        
        # Tags
        tags_info = ""
        if self.tags:
            tags = " ".join(f"{Color.GREY}#{tag}{Color.RESET}" for tag in self.tags)
            tags_info = f"\n  Tags: {tags}"
        
        # Execution status
        status_indicators = []
        
        # Run status
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
        
        # Evaluation status
        if self.has_evaluated:
            eval_status = f"{Color.GREEN}✓ Evaluated{Color.RESET}"
        elif self.has_run:
            eval_status = f"{Color.YELLOW}Not evaluated{Color.RESET}"
        else:
            eval_status = f"{Color.DIM}Pending run{Color.RESET}"
        status_indicators.append(eval_status)
        
        # Datadog sync status
        if self._datadog_experiment_id:
            dd_status = f"{Color.GREEN}✓ Synced{Color.RESET}"
            dd_url = f"\n  URL: {Color.BLUE}{BASE_URL}/llm/testing/experiments/{self._datadog_experiment_id}{Color.RESET}"
        else:
            dd_status = f"{Color.YELLOW}Local only{Color.RESET}"
            dd_url = ""
        status_indicators.append(dd_status)
        
        # Description (if present)
        desc_info = ""
        if self.description:
            desc_preview = (self.description[:47] + '...') if len(self.description) > 50 else self.description
            desc_info = f"\n  Description: {desc_preview}"
        
        # Build the final representation
        info = [
            f"Experiment(name={name})",
            f"  Project: {project}",
            f"  Task: {task_preview}",
            f"  Dataset: {dataset_info}",
            f"  Evaluators: {evaluator_info} ({', '.join(evaluator_names)})",
            f"  Status: {' | '.join(status_indicators)}"
        ]
        
        # Add optional sections if present
        if desc_info:
            info.insert(2, desc_info)
        if config_preview:
            info.append(config_preview)
        if tags_info:
            info.append(tags_info)
        if dd_url:
            info.append(dd_url)
        
        return '\n'.join(info)

 

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

    def _push_evals(self, chunk_size: int = 300) -> None:
        """Push the experiment evaluations to Datadog."""
        _validate_init()
        
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

        # Print completion message with link
        print(f"\n{Color.GREEN}✓ Experiment '{experiment_name}' results pushed to Datadog{Color.RESET}")
        print(f"{Color.BLUE}  {BASE_URL}/llm/testing/experiments/{experiment_id}{Color.RESET}\n")

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

    def __repr__(self) -> str:
        """Rich string representation of the ExperimentResults.
        
        Shows experiment name, dataset info, evaluation statistics, and error rates
        with color-coded indicators and formatted metrics.
        """
        # Basic experiment info with color
        exp_name = f"{Color.CYAN}{self.experiment.name}{Color.RESET}"
        dataset_name = f"{Color.CYAN}{self.dataset.name}{Color.RESET}"
        
        # Count records and errors
        total_records = len(self.merged_results)
        errors = sum(1 for r in self.merged_results if r.get("error", {}).get("message"))
        error_rate = (errors / total_records) * 100 if total_records > 0 else 0
        
        # Get evaluator names and stats
        evaluator_names = set()
        eval_stats = {}
        
        for result in self.merged_results:
            evals = result.get("evaluations", {})
            for eval_name, eval_data in evals.items():
                evaluator_names.add(eval_name)
                
                # Track stats for this evaluator
                if eval_name not in eval_stats:
                    eval_stats[eval_name] = {"count": 0, "errors": 0, "values": []}
                
                # Count evaluations and errors
                eval_stats[eval_name]["count"] += 1
                if eval_data.get("error"):
                    eval_stats[eval_name]["errors"] += 1
                
                # Collect values for numeric metrics
                value = eval_data.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    eval_stats[eval_name]["values"].append(value)
        
        # Format evaluator statistics
        eval_info = []
        for name in sorted(evaluator_names):
            stats = eval_stats[name]
            eval_error_rate = (stats["errors"] / stats["count"]) * 100 if stats["count"] > 0 else 0
            
            # Format differently based on whether we have numeric values
            if stats["values"]:
                # Calculate statistics for numeric metrics
                values = stats["values"]
                avg = sum(values) / len(values) if values else 0
                min_val = min(values) if values else 0
                max_val = max(values) if values else 0
                
                # Format with color based on error rate
                if eval_error_rate > 0:
                    eval_info.append(
                        f"    {name}: avg={avg:.2f} min={min_val:.2f} max={max_val:.2f} "
                        f"({Color.RED}{stats['errors']} errors, {eval_error_rate:.1f}%{Color.RESET})"
                    )
                else:
                    eval_info.append(
                        f"    {name}: avg={avg:.2f} min={min_val:.2f} max={max_val:.2f}"
                    )
            else:
                # Format for non-numeric metrics
                if eval_error_rate > 0:
                    eval_info.append(
                        f"    {name}: {stats['count']} evaluations "
                        f"({Color.RED}{stats['errors']} errors, {eval_error_rate:.1f}%{Color.RESET})"
                    )
                else:
                    eval_info.append(f"    {name}: {stats['count']} evaluations")
        
        # Format execution time if available
        time_info = ""
        if self.merged_results and "metadata" in self.merged_results[0]:
            durations = [r.get("metadata", {}).get("duration", 0) for r in self.merged_results]
            if any(durations):
                avg_time = sum(durations) / len(durations)
                total_time = sum(durations)
                time_info = f"\n  Time: {total_time:.1f}s total, {avg_time:.3f}s per record"
        
        # Format error information
        error_info = ""
        if errors > 0:
            error_info = f"\n  {Color.RED}Errors: {errors} ({error_rate:.1f}%){Color.RESET}"
            # Add sample of first error
            for result in self.merged_results:
                error_msg = result.get("error", {}).get("message")
                if error_msg:
                    preview = (error_msg[:60] + '...') if len(error_msg) > 60 else error_msg
                    error_info += f"\n    First error: {preview}"
                    break
        
        # Build the representation
        info = [
            f"ExperimentResults({exp_name})",
            f"  Dataset: {dataset_name} ({total_records:,} records)",
            f"  Task: {self.experiment.task.__name__}",
        ]
        
        # Add error info if present
        if error_info:
            info.append(error_info)
        
        # Add time info if available
        if time_info:
            info.append(time_info)
        
        # Add evaluator section if we have evaluations
        if evaluator_names:
            info.append(f"  Evaluations:")
            info.extend(eval_info)
        
        # Add URL if experiment is synced with Datadog
        if self.experiment._datadog_experiment_id:
            info.append(
                f"\n  {Color.BLUE}URL: {BASE_URL}/llm/testing/experiments/{self.experiment._datadog_experiment_id}{Color.RESET}"
            )
        
        return '\n'.join(info)


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
        self.row_idx = row_id
        self.original_error = original_error
        super().__init__(message)

class Color:
    """ANSI color codes for terminal output."""
    GREY = "\033[90m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

class Spinner:
    """Animated spinner patterns."""
    DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    ARROW = ["▹▹▹▹▹", "▸▹▹▹▹", "▹▸▹▹▹", "▹▹▸▹▹", "▹▹▹▸▹", "▹▹▹▹▸"]
    
class ProgressState(Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"

class ProgressReporter:
    """Enhanced progress reporter with animations and color."""
    
    def __init__(self, total: int, desc: str = "", width: int = None):
        self.total = total
        self.current = 0
        self.start_time = time.time()
        self.desc = desc
        self.error_count = 0
        self._last_len = 0
        self._last_update = self.start_time
        self._spinner_idx = 0
        self._state = ProgressState.RUNNING
        
        # Auto-detect terminal width if not specified
        if width is None:
            try:
                terminal_width = os.get_terminal_size().columns
                self.width = min(40, terminal_width - 50)  # Leave room for stats
            except OSError:
                self.width = 40  # Fallback if terminal size can't be detected
        else:
            self.width = width
            
    def update(self, advance: int = 1, error: bool = False) -> None:
        """Update progress with optional error tracking."""
        self.current += advance
        if error:
            self.error_count += 1
            self._state = ProgressState.ERROR
        
        # Throttle updates to max 15 per second
        now = time.time()
        if now - self._last_update < 0.066 and self.current < self.total:
            return
            
        self._last_update = now
        self._spinner_idx = (self._spinner_idx + 1) % len(Spinner.DOTS)
        self._print_progress()
    
    def _format_time(self, seconds: float) -> str:
        """Format time in a human-readable way."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds / 60)
        seconds = seconds % 60
        if minutes < 60:
            return f"{minutes}m {int(seconds)}s"
        hours = int(minutes / 60)
        minutes = minutes % 60
        return f"{hours}h {minutes}m"
    
    def _format_speed(self) -> str:
        """Calculate and format processing speed."""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return "0/s"
        speed = self.current / elapsed
        if speed >= 100:
            return f"{speed:.0f}/s"
        return f"{speed:.1f}/s"
    
    def _get_gradient_color(self, progress: float) -> str:
        """Return a color based on progress and state."""
        if self._state == ProgressState.ERROR:
            return Color.RED
        elif progress >= 1:
            return Color.GREEN
        else:
            return Color.BLUE
            
    def _print_progress(self) -> None:
        """Print the progress bar and statistics with color and animation."""
        elapsed = time.time() - self.start_time
        
        # Calculate times and progress
        progress = self.current / self.total
        if self.current == 0:
            eta = 0
        else:
            speed = self.current / elapsed
            remaining_items = self.total - self.current
            eta = remaining_items / speed if speed > 0 else 0
        
        # Get color theme based on state
        color = self._get_gradient_color(progress)
        
        # Build animated progress bar
        filled_width = int(self.width * progress)
        if progress < 1:
            # Show animated gradient effect at progress point
            bar = "█" * (filled_width - 1)
            if filled_width > 0:
                bar += "█"  # Pulse effect at progress point
            bar += "░" * (self.width - filled_width)
        else:
            bar = "█" * self.width
        
        # Format statistics
        percent = f"{progress * 100:.1f}%"
        counts = f"{self.current}/{self.total}"
        speed = self._format_speed()
        elapsed_str = self._format_time(elapsed)
        eta_str = self._format_time(eta)
        
        # Build error indicator if needed
        if self.error_count > 0:
            error_str = f" {Color.RED}({self.error_count} errors){Color.RESET}"
        else:
            error_str = ""
        
        # Get spinner frame
        spinner = Spinner.DOTS[self._spinner_idx] if progress < 1 else "✓"
        
        # Construct status line with color
        status = (
            f"\r{spinner} {Color.BOLD}{self.desc}{Color.RESET} "
            f"{color}|{bar}|{Color.RESET} "
            f"{Color.BOLD}{percent}{Color.RESET} • "
            f"{counts}{error_str} • "
            f"{Color.DIM}{speed} • {elapsed_str}<{eta_str}{Color.RESET}"
        )
        
        # Clear previous line if needed
        if self._last_len > len(status):
            print("\r" + " " * self._last_len, end="")
        
        print(status, end="")
        self._last_len = len(status)
        
        # Print final status on completion
        if self.current >= self.total:
            print()
            if self.error_count > 0:
                error_rate = (self.error_count / self.total) * 100
                print(f"{Color.RED}⚠️  Completed with {self.error_count} errors ({error_rate:.1f}%){Color.RESET}")
                print(f"{Color.DIM}   Set .run(raise_errors=True) to halt and see full error traces{Color.RESET}")
            else:
                print(f"{Color.GREEN}✓ Completed successfully{Color.RESET}")

