import csv
import json
import time
from typing import Any, Dict, Iterator, List, Optional, Union, TYPE_CHECKING
from urllib.parse import quote

from .utils._http import exp_http_request
from ._config import get_base_url, MAX_DATASET_ROWS, DEFAULT_CHUNK_SIZE, _validate_init
from .utils._ui import _print_progress_bar, Color
from .utils._exceptions import DatasetFileError

if TYPE_CHECKING:
    import pandas as pd


class Dataset:
    """
    A container for LLM experiment data that can be pushed to and retrieved from Datadog.

    This class manages collections of input-output pairs used for LLM experiments,
    providing functionality to validate data, pull or push datasets to Datadog, and
    retrieve them as Pandas DataFrames.

    Attributes:
        name (str): Name of the dataset.
        description (str): Optional description of the dataset.
        version (int): Version of the dataset (not necessarily the same as the Datadog dataset version).
    """

    def __init__(
        self, name: str, data: Optional[List[Dict[str, Union[str, Dict[str, Any]]]]] = None, description: str = ""
    ) -> None:
        """
        Initialize a Dataset instance, optionally loading data from Datadog if none is provided.

        Args:
            name (str): Name of the dataset.
            data (List[Dict[str, Union[str, Dict[str, Any]]]], optional): List of records where each record
                must contain 'input' and 'expected_output' fields. Both values can be strings or dictionaries.
                If None, attempts to pull the dataset from Datadog.
            description (str, optional): Optional description of the dataset. Defaults to "".

        Raises:
            ValueError: If the data is invalid (violates structure or size constraints).
        """
        self.name = name
        self.description = description

        # If no data provided, attempt to pull from Datadog
        if data is None:
            print(f"No data provided, pulling dataset '{name}' from Datadog...")
            pulled_dataset = self.pull(name)
            self._data = pulled_dataset._data
            self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
            self._datadog_dataset_version = pulled_dataset._datadog_dataset_version
        else:
            self._validate_data(data)
            self._data = data
            self._datadog_dataset_id = None
            self._datadog_dataset_version = 0

        self._changes = {
            'added': [],      # List of added records
            'deleted': [],    # List of deleted records with their indices
            'updated': []     # List of (index, old_record, new_record) tuples
        }
        self._synced = True

    def __iter__(self) -> Iterator[Dict[str, Union[str, Dict[str, Any]]]]:
        """
        Create an iterator over the dataset records.

        Returns:
            Iterator[Dict[str, Union[str, Dict[str, Any]]]]: An iterator of dataset records.
        """
        return iter(self._data)

    def __len__(self) -> int:
        """
        Return the number of records in the dataset.

        Returns:
            int: The size of the dataset.
        """
        return len(self._data)

    def __getitem__(self, index: int) -> Dict[str, Union[str, Dict[str, Any]]]:
        """
        Retrieve a specific dataset record by index.

        Args:
            index (int): Index of the record to retrieve.

        Returns:
            Dict[str, Union[str, Dict[str, Any]]]: A copy of the dataset record at the given index.
        """
        record = self._data[index].copy()
        # Remove the record_id from the record
        record.pop("record_id", None)
        return record

    def __delitem__(self, index: int) -> None:
        """
        Delete a record at the specified index.

        Args:
            index (int): Index of the record to delete.
        """
        deleted_record = self._data[index]
        self._changes['deleted'].append((index, deleted_record))
        del self._data[index]
        self._synced = False

    def __setitem__(self, index: int, value: Dict[str, Union[str, Dict[str, Any]]]) -> None:
        """
        Update a record at the specified index.

        Args:
            index (int): Index of the record to update.
            value (Dict[str, Union[str, Dict[str, Any]]]): New record value.

        Raises:
            ValueError: If the new record doesn't match the expected structure.
        """
        self._validate_data([value])
        old_record = self._data[index]
        self._changes['updated'].append((index, old_record, value))
        self._data[index] = value
        self._synced = False

    def __iadd__(self, record: Dict[str, Union[str, Dict[str, Any]]]) -> "Dataset":
        """
        Add a new record using the += operator.

        Args:
            record (Dict[str, Union[str, Dict[str, Any]]]): Record to add.

        Returns:
            Dataset: The dataset instance for method chaining.

        Raises:
            ValueError: If the record doesn't match the expected structure.
        """
        self.add(record)
        return self

    def add(self, record: Dict[str, Union[str, Dict[str, Any]]]) -> None:
        """
        Add a new record to the dataset.

        Args:
            record (Dict[str, Union[str, Dict[str, Any]]]): Record to add.

        Raises:
            ValueError: If the record doesn't match the expected structure.
        """
        self._validate_data([record])
        self._changes['added'].append(record)
        self._data.append(record)
        self._synced = False

    def remove(self, index: int) -> None:
        """
        Remove a record at the specified index.

        Args:
            index (int): Index of the record to remove.
        """
        del self[index]  # Use __delitem__ to track the change

    def update(self, index: int, record: Dict[str, Union[str, Dict[str, Any]]]) -> None:
        """
        Update a record at the specified index.

        Args:
            index (int): Index of the record to update.
            record (Dict[str, Union[str, Dict[str, Any]]]): New record value.

        Raises:
            ValueError: If the record doesn't match the expected structure.
        """
        self[index] = record  # Use __setitem__ to track the change

    def _get_changes(self) -> Dict[str, List]:
        """
        Get all tracked changes since the last push or pull.

        Returns:
            Dict[str, List]: Dictionary containing lists of added, deleted, and updated records.
        """
        return {
            'added': self._changes['added'].copy(),
            'deleted': self._changes['deleted'].copy(),
            'updated': self._changes['updated'].copy()
        }

    def _clear_changes(self) -> None:
        """Clear all tracked changes."""
        self._changes = {
            'added': [],
            'deleted': [],
            'updated': []
        }

    def _validate_data(self, data: List[Dict[str, Union[str, Dict[str, Any]]]]) -> None:
        """
        Validate the structure and size of dataset records.

        Args:
            data (List[Dict[str, Union[str, Dict[str, Any]]]]): List of dataset records.

        Raises:
            ValueError: If the data is empty, exceeds 50,000 records, or records are inconsistent in keys or data type.
        """
        if not data:
            raise ValueError("Data cannot be empty.")

        if len(data) > MAX_DATASET_ROWS:
            raise ValueError(f"Dataset cannot exceed {MAX_DATASET_ROWS} rows.")

        if not all(isinstance(row, dict) for row in data):
            raise ValueError("All rows must be dictionaries.")

        first_row_keys = set(data[0].keys())
        for row in data:
            if set(row.keys()) != first_row_keys:
                raise ValueError("All rows must have the same keys.")

    @classmethod
    def pull(cls, name: str, version: int = None) -> "Dataset":
        """
        Create a dataset from an existing dataset hosted in Datadog.

        Args:
            name (str): Name of the dataset to retrieve from Datadog.
            version (int, optional): Specific version of the dataset to retrieve. If None, uses the latest version.

        Returns:
            Dataset: A new Dataset instance populated with the records from Datadog.

        Raises:
            ValueError: If the dataset is not found or if the specified version doesn't exist.
            Exception: If any HTTP or unexpected error occurs during the request.
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
                        f"Version {version} not found for dataset '{name}'. " f"Available versions: {versions_str}"
                    )
                dataset_version = version
            except ValueError as e:
                if "404" in str(e):
                    raise ValueError(f"Dataset '{name}' not found with version {version}") from e
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
                        f"Version {version} not found for dataset '{name}'. " f"Available versions: {versions_str}"
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

            class_records.append(
                {
                    "record_id": record.get("id"),
                    "input": input_data,
                    "expected_output": expected_output,
                    **attrs.get("metadata", {}),
                }
            )

        # Create new dataset instance
        dataset = cls(name, class_records)
        dataset._datadog_dataset_id = dataset_id
        dataset._datadog_dataset_version = dataset_version
        return dataset

    def _batch_update(self, overwrite: bool = False) -> None:
        """
        Send all tracked changes to Datadog using the batch update endpoint.
        This method is called by push() when there are pending changes.

        Raises:
            ValueError: If the dataset is not synced with Datadog (no dataset_id)
            Exception: If the batch update request fails
        """
        if not self._datadog_dataset_id:
            raise ValueError("Dataset must be synced with Datadog before performing batch updates")

        # Prepare the payload
        payload = {
            "data": {
                "type": "datasets",
                "attributes": {}
            }
        }

        if overwrite:
            print(f"{Color.YELLOW}Warning: Overwriting existing dataset '{self.name}' using batch update{Color.RESET}")
        else:
            print("Pushing changes to existing dataset, new version will be created")

        # Add new records
        if self._changes['added']:
            insert_records = []
            for record in self._changes['added']:
                new_record = {
                    "input": record["input"],
                    "expected_output": record["expected_output"]
                }
                # Add metadata if present
                metadata = {k: v for k, v in record.items() 
                          if k not in ["input", "expected_output", "record_id"]}
                if metadata:
                    new_record["metadata"] = metadata
                insert_records.append(new_record)
            payload["data"]["attributes"]["insert_records"] = insert_records

        # Add updated records
        if self._changes['updated']:
            update_records = []
            for _, old_record, new_record in self._changes['updated']:
                # We need the record_id from the old record
                if "record_id" not in old_record:
                    raise ValueError("Cannot update record: missing record_id")
                
                update_record = {"id": old_record["record_id"]}
                
                # Only include fields that have changed
                if new_record.get("input") != old_record.get("input"):
                    update_record["input"] = new_record["input"]
                if new_record.get("expected_output") != old_record.get("expected_output"):
                    update_record["expected_output"] = new_record["expected_output"]
                
                # Handle metadata changes
                old_metadata = {k: v for k, v in old_record.items() 
                              if k not in ["input", "expected_output", "record_id"]}
                new_metadata = {k: v for k, v in new_record.items() 
                              if k not in ["input", "expected_output", "record_id"]}
                if old_metadata != new_metadata:
                    update_record["metadata"] = new_metadata
                
                if len(update_record) > 1:  # Only add if there are actual changes (more than just id)
                    update_records.append(update_record)
            if update_records:
                payload["data"]["attributes"]["update_records"] = update_records

        # Add deleted records
        if self._changes['deleted']:
            delete_records = []
            for _, record in self._changes['deleted']:
                if "record_id" not in record:
                    raise ValueError("Cannot delete record: missing record_id")
                delete_records.append(record["record_id"])
            payload["data"]["attributes"]["delete_records"] = delete_records

        if overwrite:
            payload["data"]["attributes"]["overwrite"] = True
        
        # Send the batch update request
        url = f"/api/unstable/llm-obs/v1/datasets/{self._datadog_dataset_id}/batch_update"
        resp = exp_http_request("POST", url, body=json.dumps(payload).encode("utf-8"))

        # Sleep briefly to allow for processing
        time.sleep(3)

        # Pull the dataset to get all record IDs and latest version
        pulled_dataset = self.pull(self.name)
        self._data = pulled_dataset._data
        self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
        self._datadog_dataset_version = pulled_dataset._datadog_dataset_version

        # Clear changes and mark as synced
        self._clear_changes()
        self._synced = True

    def push(self, overwrite: bool = False, new_version: bool = False) -> None:
        """
        Push the dataset to Datadog, optionally overwriting an existing dataset or creating a new version.
        
        This is the high-level method that handles:
        1. Checking for an existing dataset on Datadog by name.
        2. If dataset exists remotely:
           - For local datasets (not synced):
             * If overwrite=True: overwrite the remote dataset
             * If new_version=True: create a new version
             * If neither: show warning and return
           - For synced datasets with changes, use batch_update
        3. If dataset doesn't exist, create new

        Args:
            overwrite (bool, optional): If True, overwrite the existing dataset if found. Defaults to False.
            new_version (bool, optional): If True and dataset exists, create a new version. Defaults to False.
        """
        if overwrite and new_version:
            raise ValueError("Cannot specify both overwrite=True and new_version=True")

        _validate_init()

        # Check for an existing dataset with the same name
        encoded_name = quote(self.name)
        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
        resp = exp_http_request("GET", url)
        existing_dataset = resp.json().get("data", [])

        # Detect if we have any local changes
        has_changes = any(len(changes) > 0 for changes in self._changes.values())

        if existing_dataset:
            remote_id = existing_dataset[0]["id"]
            
            # Case 1: We have a local dataset (not synced) with same name as remote
            if not self._datadog_dataset_id:
                if overwrite:
                    # If overwrite=True, proceed with overwrite
                    self._datadog_dataset_id = remote_id
                    self._datadog_dataset_version = existing_dataset[0]["attributes"]["current_version"]
                    self._push_entire_dataset(overwrite=True)
                    return
                elif new_version:
                    # If new_version=True, create new version via push
                    self._datadog_dataset_id = remote_id
                    self._datadog_dataset_version = existing_dataset[0]["attributes"]["current_version"]
                    self._push_entire_dataset(overwrite=False)  # Push will create new version
                    return
                else:
                    print(f"{Color.YELLOW}Dataset '{self.name}' already exists in Datadog. "
                          f"Use push(overwrite=True) to overwrite the remote dataset "
                          f"or push(new_version=True) to create a new version.{Color.RESET}")
                    return

            # Case 2: We have a synced dataset
            if self._datadog_dataset_id == remote_id:
                if has_changes:
                    if overwrite:
                        # If we have changes and overwrite=True, use push to overwrite
                        self._push_entire_dataset(overwrite=True)
                    elif new_version:
                        # If we have changes and new_version=True, use push to create new version
                        self._push_entire_dataset(overwrite=False)
                    else:
                        # If we have changes but no overwrite/new_version, use batch_update
                        self._batch_update(overwrite=False)
                    return
                else:
                    print("Dataset is already synced and has no changes.")
                    return

            # Case 3: We have a synced dataset but with different ID (shouldn't happen)
            raise ValueError(f"Dataset '{self.name}' exists but with different ID. This shouldn't happen.")

        else:
            # No dataset found on Datadog; create new
            if self._datadog_dataset_id:
                # We thought we were synced but the remote dataset is gone
                print(f"{Color.YELLOW}Warning: Remote dataset no longer exists. Creating new dataset.{Color.RESET}")
                self._datadog_dataset_id = None
                self._datadog_dataset_version = 0
            
            self._push_entire_dataset(overwrite=False)

    def _push_entire_dataset(self, overwrite: bool) -> None:
        """
        Internal helper to create or overwrite a dataset.
        
        If self._datadog_dataset_id is set, it will update that dataset (potentially
        overwriting it). If no dataset_id is present, it will:
        1. First create a new dataset
        2. Then add all records using the /push endpoint
        """
        if overwrite:
            print(f"{Color.YELLOW}Warning: Overwriting existing dataset '{self.name}' using push{Color.RESET}")

        # If we don't have a dataset_id, create the dataset first
        if not self._datadog_dataset_id:
            # Create new dataset (metadata only)
            dataset_payload = {
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "name": self.name,
                        "description": self.description,
                    }
                }
            }
            resp = exp_http_request(
                "POST", "/api/unstable/llm-obs/v1/datasets", 
                body=json.dumps(dataset_payload).encode("utf-8")
            )
            response_data = resp.json()
            self._datadog_dataset_id = response_data["data"]["id"]
            self._datadog_dataset_version = 0

        # Now that we have a dataset_id (either from creation or existing),
        # prepare the records to push
        records_list = []
        for record in self._data:
            record_payload = {}
            # If this record was previously pulled from Datadog, preserve its ID
            if "record_id" in record:
                record_payload["id"] = record["record_id"]

            # Always include input and expected_output
            record_payload["input"] = record["input"]
            if "expected_output" in record:
                record_payload["expected_output"] = record["expected_output"]

            # Gather any other keys as metadata
            metadata = {
                k: v
                for k, v in record.items()
                if k not in ["input", "expected_output", "record_id"]
            }
            if metadata:
                record_payload["metadata"] = metadata

            records_list.append(record_payload)

        # Split records into chunks if needed (e.g., if there are many records)
        chunk_size = DEFAULT_CHUNK_SIZE
        chunks = [records_list[i:i + chunk_size] for i in range(0, len(records_list), chunk_size)]

        # Show progress bar for large datasets
        show_progress = len(chunks) > 1
        if show_progress:
            _print_progress_bar(0, len(chunks), prefix="Pushing records:", suffix="Complete")

        # Push each chunk of records
        for i, chunk in enumerate(chunks):
            push_payload = {
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "records": chunk,
                        "overwrite": overwrite if i == 0 else False  # Only overwrite on first chunk
                    }
                }
            }

            url = f"/api/unstable/llm-obs/v1/datasets/{self._datadog_dataset_id}/push"
            resp = exp_http_request("POST", url, body=json.dumps(push_payload).encode("utf-8"))

            if show_progress:
                _print_progress_bar(i + 1, len(chunks), prefix="Pushing records:", suffix="Complete")

        # Sleep briefly to allow processing
        time.sleep(3)

        # Pull the dataset to get all record IDs and latest version
        pulled_dataset = self.pull(self.name)
        self._data = pulled_dataset._data
        self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
        self._datadog_dataset_version = pulled_dataset._datadog_dataset_version

        # Clear local changes and mark as synced
        self._clear_changes()
        self._synced = True

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
        """
        Create a Dataset from a CSV file by specifying which columns correspond
        to inputs and which columns correspond to expected outputs.

        Args:
            filepath (str): Path to the CSV file.
            name (str): Name of the dataset (this will be used in Datadog).
            description (str, optional): Optional description of the dataset. Defaults to "".
            delimiter (str, optional): CSV delimiter character, defaults to comma.
            input_columns (List[str], optional): List of column names that should be treated as input data.
            expected_output_columns (List[str], optional): List of column names that should be treated as expected output data.

        Returns:
            Dataset: A new Dataset instance containing the CSV data, structured for LLM experiments.

        Raises:
            ValueError: If input_columns or expected_output_columns are not provided,
                or if the CSV is missing those columns, or if the file is empty.
            DatasetFileError: If there are issues reading the CSV file (e.g., file not found or IO permission error).
        """
        if input_columns is None or expected_output_columns is None:
            raise ValueError("`input_columns` and `expected_output_columns` must be provided.")

        data = []
        try:
            with open(filepath, mode="r", encoding="utf-8") as csvfile:
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
                metadata_columns = [
                    col for col in header_columns if col not in input_columns and col not in expected_output_columns
                ]

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

                    data.append(
                        {
                            "input": input_data,
                            "expected_output": expected_output_data,
                            **metadata,
                        }
                    )
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
        """
        Convert the dataset to a pandas DataFrame for further analysis and manipulation.

        Args:
            multiindex (bool, optional): If True, expand 'input' and 'expected_output' dictionaries
                into columns using a pandas MultiIndex. If False, keep them as columns containing
                raw dictionaries.

        Returns:
            pd.DataFrame: DataFrame representation of the dataset.

        Raises:
            ImportError: If pandas is not installed.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required to convert dataset to DataFrame. " "Please install it with `pip install pandas`"
            )

        if multiindex:
            column_tuples = set()
            data_rows = []
            for record in self._data:
                flat_record = {}

                # Handle 'input' fields
                input_data = record.get("input", {})
                if isinstance(input_data, dict):
                    for k, v in input_data.items():
                        flat_record[("input", k)] = v
                        column_tuples.add(("input", k))
                else:
                    flat_record[("input", "")] = input_data
                    column_tuples.add(("input", ""))

                # Handle 'expected_output' fields
                expected_output = record.get("expected_output", {})
                if isinstance(expected_output, dict):
                    for k, v in expected_output.items():
                        flat_record[("expected_output", k)] = v
                        column_tuples.add(("expected_output", k))
                else:
                    flat_record[("expected_output", "")] = expected_output
                    column_tuples.add(("expected_output", ""))

                # Handle any other top-level fields
                for k, v in record.items():
                    if k not in ["input", "expected_output"]:
                        flat_record[("metadata", k)] = v
                        column_tuples.add(("metadata", k))
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
            input_data = record.get("input", {})
            new_record["input"] = input_data
            expected_output = record.get("expected_output", {})
            new_record["expected_output"] = expected_output
            # Copy other fields
            for k, v in record.items():
                if k not in ["input", "expected_output"]:
                    new_record[k] = v
            data.append(new_record)
        return pd.DataFrame(data)

    def __repr__(self) -> str:
        """
        Return a readable string representation of the Dataset object, including information such as
        dataset name, size, structure, and synchronization status with Datadog.

        Returns:
            str: A human-friendly representation of the Dataset.
        """
        name = f"{Color.CYAN}{self.name}{Color.RESET}"
        record_count = len(self._data)

        # Get sample structure from first record
        structure = []
        if self._data:
            sample = self._data[0]
            # Input structure
            input_data = sample.get("input", {})
            if isinstance(input_data, dict):
                input_desc = f"dict[{len(input_data)} keys]"
            else:
                input_desc = type(input_data).__name__
            structure.append(f"input: {input_desc}")

            # Expected output structure
            expected = sample.get("expected_output", {})
            if isinstance(expected, dict):
                output_desc = f"dict[{len(expected)} keys]"
            else:
                output_desc = type(expected).__name__
            structure.append(f"expected_output: {output_desc}")

            # Metadata fields
            metadata = {k: v for k, v in sample.items() if k not in ["input", "expected_output", "record_id"]}
            if metadata:
                structure.append(f"metadata: {len(metadata)} fields")

        # Datadog sync status and changes summary
        has_changes = any(len(changes) > 0 for changes in self._changes.values())
        if getattr(self, "_datadog_dataset_id", None):
            if self._synced and not has_changes:
                dd_status = f"{Color.GREEN}✓ Synced{Color.RESET} (v{self._datadog_dataset_version})"
            else:
                dd_status = f"{Color.YELLOW}⚠ Unsynced changes{Color.RESET} (v{self._datadog_dataset_version})"
        else:
            dd_status = f"{Color.YELLOW}Local only{Color.RESET}"

        panel_config = json.dumps([{"t": "datasetDetailPanel", "datasetId": self._datadog_dataset_id}])
        dd_url = (f"\n  URL: {Color.BLUE}https://app.datadoghq.com/llm/testing/datasets"
                 f"?llmPanels={quote(panel_config)}"
                 f"&openApplicationConfig=false{Color.RESET}"
                 if getattr(self, "_datadog_dataset_id", None) else "")

        # Build the representation
        info = [
            f"Dataset(name={name})",
            f"  Records: {record_count:,}",
            f"  Structure: {', '.join(structure)}",
            f"  Datadog: {dd_status}",
        ]

        # Add description if present
        if self.description:
            desc_preview = (self.description[:47] + "...") if len(self.description) > 50 else self.description
            info.insert(1, f"  Description: {desc_preview}")

        # Add changes summary if there are any changes
        if has_changes:
            changes_summary = []
            if self._changes['added']:
                changes_summary.append(f"+{len(self._changes['added'])} added")
            if self._changes['deleted']:
                changes_summary.append(f"-{len(self._changes['deleted'])} deleted")
            if self._changes['updated']:
                changes_summary.append(f"~{len(self._changes['updated'])} updated")
            info.append(f"  Changes: {', '.join(changes_summary)}")

        # Add URL if dataset is synced
        if dd_url and self._synced:
            info.append(dd_url)

        return "\n".join(info)


