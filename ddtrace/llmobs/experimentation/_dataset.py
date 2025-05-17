import csv
import json
import time
from typing import Any, Dict, Iterator, List, Optional, Union, TYPE_CHECKING
from urllib.parse import quote

from .utils._http import exp_http_request
from ._config import (
    MAX_DATASET_ROWS,
    DEFAULT_CHUNK_SIZE,
    _validate_init,
    API_PROCESSING_TIME_SLEEP,
)
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
            'added': [],
            'deleted': [],
            'updated': []
        }
        self._synced = True

    def __iter__(self) -> Iterator[Dict[str, Union[str, Dict[str, Any]]]]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: Union[int, slice]) -> Union[Dict[str, Union[str, Dict[str, Any]]], List[Dict[str, Union[str, Dict[str, Any]]]]]:
        """
        Retrieve one or more dataset records by index or slice.
        Returns a copy of the record(s) without the 'record_id' field.

        Args:
            index (Union[int, slice]): Index or slice of records to retrieve.

        Returns:
            Union[Dict[str, Union[str, Dict[str, Any]]], List[Dict[str, Union[str, Dict[str, Any]]]]]:
                A copy of the dataset record(s) at the given index/slice.
        """
        if isinstance(index, slice):
            records = self._data[index]
            return [{k: v for k, v in record.copy().items() if k != "record_id"} for record in records]
        # If index is not a slice, handle single record retrieval
        record = self._data[index].copy()
        record.pop("record_id", None)
        return record

    def __delitem__(self, index: int) -> None:
        """
        Delete a record at the specified index and track the change.

        Args:
            index (int): Index of the record to delete.
        """
        deleted_record = self._data[index]
        self._changes['deleted'].append((index, deleted_record))
        del self._data[index]
        self._synced = False

    def __setitem__(self, index: int, value: Dict[str, Union[str, Dict[str, Any]]]) -> None:
        """
        Validate and update a record at the specified index, tracking the change.

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
        Validate and add a new record to the dataset, tracking the change.

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
        """Remove a record at the specified index."""
        del self[index]

    def update(self, index: int, record: Dict[str, Union[str, Dict[str, Any]]]) -> None:
        """Update a record at the specified index."""
        self[index] = record

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
        Validate the structure and size of dataset records. Allows empty data list.

        Args:
            data (List[Dict[str, Union[str, Dict[str, Any]]]]): List of dataset records to validate.

        Raises:
            ValueError: If the data exceeds size limit or has inconsistent structure (when not empty).
        """
        # Allow empty data list - skip further validation if empty
        if not data:
            return

        if len(data) > MAX_DATASET_ROWS:
            raise ValueError(f"Dataset cannot exceed {MAX_DATASET_ROWS} rows.")

        if not all(isinstance(row, dict) for row in data):
            raise ValueError("All rows must be dictionaries.")

        expected_keys = None
        if hasattr(self, '_data') and self._data:
            expected_keys = set(k for k in self._data[0].keys() if k != 'record_id')

        first_row_keys = set(data[0].keys())

        # Validate new data against existing structure if present.
        if expected_keys is not None:
            # Allow record_id to be present or not in new data
            new_keys = {k for k in first_row_keys if k != 'record_id'}
            if new_keys != expected_keys:
                raise ValueError(
                    f"Record structure doesn't match dataset. Expected keys {sorted(expected_keys)}, "
                    f"got {sorted(new_keys)}"
                )

        required_keys = {'input', 'expected_output'}
        if not required_keys.issubset(first_row_keys):
            missing = required_keys - first_row_keys
            raise ValueError(f"Records must contain 'input' and 'expected_output' fields. Missing: {missing}")

        # Validate consistency within new data
        for row in data:
            row_keys = set(row.keys())
            if row_keys != first_row_keys:
                raise ValueError(
                    f"Inconsistent keys in data. Expected {sorted(first_row_keys)}, "
                    f"got {sorted(row_keys)}"
                )

    @classmethod
    def pull(cls, name: str, version: int = None) -> "Dataset":
        """
        Create a dataset from an existing dataset hosted in Datadog.

        Args:
            name (str): Name of the dataset to retrieve from Datadog.
            version (int, optional): Specific version of the dataset to retrieve. If None, uses the latest version.

        Returns:
            Dataset: A new Dataset instance populated with the records from Datadog (can be empty).

        Raises:
            ValueError: If the dataset is not found or if the specified version doesn't exist.
            Exception: If any HTTP or unexpected error occurs during the request.
        """
        _validate_init()
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

        if overwrite:
            print(f"{Color.YELLOW}Warning: Overwriting existing dataset '{self.name}' using batch update{Color.RESET}")
        else:
            print("Pushing changes to existing dataset, new version will be created")

        payload = self._prepare_batch_payload(overwrite=overwrite)

        url = f"/api/unstable/llm-obs/v1/datasets/{self._datadog_dataset_id}/batch_update"
        exp_http_request("POST", url, body=json.dumps(payload).encode("utf-8"))

        # Sleep briefly to allow for processing on the backend using the constant
        time.sleep(API_PROCESSING_TIME_SLEEP)

        # Pull the dataset to get all record IDs and latest version, then clear changes
        self._refresh_from_remote()

    def _prepare_batch_payload(self, overwrite: bool) -> Dict[str, Any]:
        """
        Build the JSON payload for batch updating. This includes newly added, updated, and deleted records.
        """
        payload_data = {"type": "datasets", "attributes": {}}

        if self._changes['added']:
            insert_records = []
            for record in self._changes['added']:
                new_record = {"input": record["input"], "expected_output": record["expected_output"]}
                metadata = {k: v for k, v in record.items() if k not in ["input", "expected_output", "record_id"]}
                if metadata:
                    new_record["metadata"] = metadata
                insert_records.append(new_record)
            payload_data["attributes"]["insert_records"] = insert_records

        if self._changes['updated']:
            update_records = []
            for _, old_record, new_record in self._changes['updated']:
                if "record_id" not in old_record:
                    raise ValueError("Cannot update record: missing record_id")
                upd = {"id": old_record["record_id"]}

                if new_record.get("input") != old_record.get("input"):
                    upd["input"] = new_record["input"]
                if new_record.get("expected_output") != old_record.get("expected_output"):
                    upd["expected_output"] = new_record["expected_output"]

                old_metadata = {k: v for k, v in old_record.items() if k not in ["input", "expected_output", "record_id"]}
                new_metadata = {k: v for k, v in new_record.items() if k not in ["input", "expected_output", "record_id"]}
                if old_metadata != new_metadata:
                    upd["metadata"] = new_metadata

                if len(upd) > 1:
                    update_records.append(upd)

            if update_records:
                payload_data["attributes"]["update_records"] = update_records

        if self._changes['deleted']:
            delete_ids = []
            for _, record in self._changes['deleted']:
                if "record_id" not in record:
                    raise ValueError("Cannot delete record: missing record_id")
                delete_ids.append(record["record_id"])
            payload_data["attributes"]["delete_records"] = delete_ids

        if overwrite:
            payload_data["attributes"]["overwrite"] = True

        return {"data": payload_data}

    def _refresh_from_remote(self) -> None:
        """Pull the dataset from Datadog to refresh all data and mark it as synced."""
        pulled_dataset = self.pull(self.name)
        self._data = pulled_dataset._data
        self._datadog_dataset_id = pulled_dataset._datadog_dataset_id
        self._datadog_dataset_version = pulled_dataset._datadog_dataset_version
        self._clear_changes()
        self._synced = True

    def push(self, overwrite: bool = False, new_version: bool = False) -> None:
        """
        Push the local dataset state to Datadog.

        This method handles different scenarios:
        - If no remote dataset with the same name exists, it creates a new one.
        - If a remote dataset exists:
            - If the local dataset was not previously synced with Datadog, requires either
              `overwrite=True` or `new_version=True` to proceed.
            - If the local dataset is synced and has changes:
                - `overwrite=True`: Replaces the latest version of the remote dataset entirely.
                - `new_version=True`: Creates a new version remotely based on the full local dataset.
                - Default: Sends incremental changes (adds, updates, deletes) to create a new version.
            - If the local dataset is synced and has no changes, no action is taken.

        Args:
            overwrite (bool, optional): If True and a remote dataset exists, overwrite its latest
                version with the current local data. Cannot be used with `new_version=True`. Defaults to False.
            new_version (bool, optional): If True and a remote dataset exists, push the entire local
                dataset as a new version, ignoring incremental changes. Cannot be used with `overwrite=True`.
                Defaults to False.

        Raises:
            ValueError: If both `overwrite` and `new_version` are True, or if a remote dataset exists
                but the local dataset isn't synced and neither flag is specified. Also raises if there's
                an ID mismatch between local and remote state.
            Exception: If any HTTP or unexpected error occurs during the API requests.
        """
        if overwrite and new_version:
            raise ValueError("Cannot specify both overwrite=True and new_version=True")

        _validate_init()
        has_changes = any(len(chg) > 0 for chg in self._changes.values())

        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={quote(self.name)}"
        response = exp_http_request("GET", url)
        existing_dataset_data = response.json().get("data", [])

        if existing_dataset_data:
            self._handle_existing_remote_dataset(existing_dataset_data[0], has_changes, overwrite, new_version)
        else:
            self._handle_no_remote_dataset()

    def _handle_existing_remote_dataset(self, remote_data: Dict, has_changes: bool, overwrite: bool, new_version: bool) -> None:
        """Handles the logic when a remote dataset with the same name exists."""
        remote_id = remote_data["id"]
        current_version = remote_data["attributes"]["current_version"]

        # Scenario 1: Local dataset not yet synced to Datadog
        if not self._datadog_dataset_id:
            if overwrite:
                print(f"{Color.YELLOW}Warning: Found existing dataset '{self.name}'. Overwriting it with local data.{Color.RESET}")
                self._datadog_dataset_id = remote_id
                self._datadog_dataset_version = current_version
                self._push_entire_dataset(overwrite=True)
            elif new_version:
                print(f"Found existing dataset '{self.name}'. Creating a new version based on local data.")
                self._datadog_dataset_id = remote_id
                self._datadog_dataset_version = current_version
                self._push_entire_dataset(overwrite=False) # False means create new version
            else:
                raise ValueError(
                    f"Dataset '{self.name}' already exists remotely. Use push(overwrite=True) to overwrite "
                    f"or push(new_version=True) to create a new version based on local data."
                )
            return

        # Scenario 2: Local dataset is synced - check if IDs match
        if self._datadog_dataset_id != remote_id:
            # This scenario should ideally not happen if pull/push logic is correct
            raise ValueError(
                f"Local dataset ID ({self._datadog_dataset_id}) does not match remote dataset ID ({remote_id}) "
                f"for name '{self.name}'. This could happen if the remote dataset was deleted and recreated. "
                f"Consider pulling the dataset again or using push(overwrite=True)."
            )

        # Scenario 3: Local dataset is synced and IDs match
        if not has_changes:
            print(f"Dataset '{self.name}' (v{self._datadog_dataset_version}) is already synced and has no pending changes.")
            return

        # Scenario 4: Synced dataset with changes
        if overwrite:
            # Overwrite existing version by pushing all data
            self._push_entire_dataset(overwrite=True)
        elif new_version:
            # Force creation of new version by pushing all data
            self._push_entire_dataset(overwrite=False)
        else:
            # Default behavior: send incremental updates to create a new version
            self._batch_update(overwrite=False)

    def _handle_no_remote_dataset(self) -> None:
        """Handles the logic when no remote dataset with the same name exists."""
        if self._datadog_dataset_id:
            # Local dataset thought it was synced, but remote is gone (deleted/renamed)
            print(
                f"{Color.YELLOW}Warning: Local dataset '{self.name}' was previously synced (ID: {self._datadog_dataset_id}), "
                f"but no matching remote dataset found. Creating a new one based on local data.{Color.RESET}"
            )
            self._datadog_dataset_id = None
            self._datadog_dataset_version = 0 # Reset version for new creation

        # overwrite=False is implicit for creation, but explicit for clarity
        self._push_entire_dataset(overwrite=False)

    def _push_entire_dataset(self, overwrite: bool) -> None:
        """
        Internal helper to create or overwrite a dataset by pushing all records.
        If self._datadog_dataset_id is None, a new dataset is created first.
        If overwrite is True and dataset exists, it replaces the latest version.
        If overwrite is False and dataset exists, it creates a new version.
        """
        action = "Overwriting" if overwrite else "Creating new version for"
        if not self._datadog_dataset_id:
            action = "Creating" # Overwrite flag is ignored if dataset doesn't exist locally

        if self._datadog_dataset_id and overwrite:
             print(f"{Color.YELLOW}Warning: Overwriting latest version of existing dataset '{self.name}'...{Color.RESET}")
        elif self._datadog_dataset_id and not overwrite:
             print(f"Pushing entire local dataset as a new version for '{self.name}'...")
        else: # Creation case
             pass
        


        # Create dataset metadata entry if it doesn't exist locally
        if not self._datadog_dataset_id:
            payload = {
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "name": self.name,
                        "description": self.description,
                    }
                }
            }
            resp = exp_http_request("POST", "/api/unstable/llm-obs/v1/datasets", body=json.dumps(payload).encode("utf-8"))
            response_data = resp.json()
            self._datadog_dataset_id = response_data["data"]["id"]
            self._datadog_dataset_version = 0 # Version starts at 0 for creation via API

        records_list = []
        for record in self._data:
            rec_payload = {}
            if "record_id" in record:
                # Only include ID if overwriting? Backend handles this.
                rec_payload["id"] = record["record_id"]
            rec_payload["input"] = record["input"]
            if "expected_output" in record:
                rec_payload["expected_output"] = record["expected_output"]
            metadata = {k: v for k, v in record.items() if k not in ["input", "expected_output", "record_id"]}
            if metadata:
                rec_payload["metadata"] = metadata
            records_list.append(rec_payload)

        chunk_size = DEFAULT_CHUNK_SIZE
        chunks = [records_list[i : i + chunk_size] for i in range(0, len(records_list), chunk_size)]

        show_progress = len(chunks) > 1
        if show_progress:
            _print_progress_bar(0, len(chunks), prefix="Pushing records:", suffix="Complete")

        for i, chunk in enumerate(chunks):
            attributes = {"records": chunk}
            
            attributes["overwrite"] = overwrite

            push_payload = {
                "data": {
                    "type": "datasets",
                    "attributes": attributes,
                }
            }
            # Use the /records endpoint instead of /push
            url = f"/api/unstable/llm-obs/v1/datasets/{self._datadog_dataset_id}/records"
            # Safely access keys in the print statement using .get()
            print(f"Pushing records to {url}",
                  "overwriting: ", attributes.get("overwrite", False), # Default overwrite is False
                  "append: ", attributes.get("append", True)) # Default append is True
            exp_http_request("POST", url, body=json.dumps(push_payload).encode("utf-8"))

            if show_progress:
                _print_progress_bar(i + 1, len(chunks), prefix="Pushing records:", suffix="Complete")

        # Give Datadog time to process using the constant, then refresh local state from remote
        time.sleep(API_PROCESSING_TIME_SLEEP)
        self._refresh_from_remote()
        print(f"{Color.GREEN}✓ Dataset '{self.name}' pushed to Datadog{Color.RESET}")

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
            DatasetFileError: If there are issues reading the CSV file (e.g., file not found, permission error, malformed).
        """
        if input_columns is None or expected_output_columns is None:
            raise ValueError("`input_columns` and `expected_output_columns` must be provided.")

        data = []
        try:
            # First check if the file exists and is not empty before parsing
            with open(filepath, mode="r", encoding="utf-8") as csvfile:
                content = csvfile.read().strip()
                if not content:
                    raise ValueError("CSV file appears to be empty or header is missing.")

            with open(filepath, mode="r", encoding="utf-8") as csvfile:
                try:
                    reader = csv.DictReader(csvfile, delimiter=delimiter)

                    # Check header presence before trying to read rows
                    if reader.fieldnames is None:
                        # Treat files with no header at all as effectively empty
                        raise ValueError("CSV file appears to be empty or header is missing.")

                    header_columns = reader.fieldnames
                    missing_input_columns = [col for col in input_columns if col not in header_columns]
                    missing_output_columns = [col for col in expected_output_columns if col not in header_columns]

                    if missing_input_columns:
                        raise ValueError(f"Input columns not found in CSV header: {missing_input_columns}")
                    if missing_output_columns:
                        raise ValueError(f"Expected output columns not found in CSV header: {missing_output_columns}")

                    rows = list(reader)

                    if not rows:
                        # ValueError for empty data files (header-only)
                        raise ValueError("CSV file is empty (contains only header).")

                    # Determine metadata columns (all columns not used for input or expected output)
                    metadata_columns = [
                        col for col in header_columns if col not in input_columns and col not in expected_output_columns
                    ]

                    for row in rows:
                        try:
                            # Strip whitespace from all values
                            for col in row:
                                if row[col] is not None:
                                    row[col] = row[col].strip()
                        except Exception as e:
                            # Error during whitespace handling likely indicates malformed CSV
                            raise DatasetFileError(f"Error parsing CSV file (whitespace handling): {e}")

                        try:
                            input_data = row[input_columns[0]] if len(input_columns) == 1 else {col: row[col] for col in input_columns}
                            expected_output_data = row[expected_output_columns[0]] if len(expected_output_columns) == 1 else {col: row[col] for col in expected_output_columns}

                            metadata = {}
                            for col in metadata_columns:
                                # Include all remaining columns from the CSV as metadata
                                metadata[col] = row[col]
                        except KeyError as e:
                            # Missing columns in a data row indicates malformed CSV
                            raise DatasetFileError(f"Error parsing CSV file: missing column {e} in a row")
                        except Exception as e:
                            # Other errors during row processing also indicate CSV issues
                            raise DatasetFileError(f"Error parsing CSV file (row processing): {e}")

                        data.append(
                            {
                                "input": input_data,
                                "expected_output": expected_output_data,
                                **metadata,
                            }
                        )
                except csv.Error as e:
                    # Catch CSV-specific parsing errors
                    raise DatasetFileError(f"Error parsing CSV file: {e}")

        except FileNotFoundError as e:
            raise DatasetFileError(f"CSV file not found: {filepath}") from e
        except PermissionError as e:
            raise DatasetFileError(f"Permission denied when reading CSV file: {filepath}") from e
        # Allow specific ValueErrors (empty file, missing columns in header, header-only file) to propagate.
        except Exception as e:
            # Catch other unexpected exceptions during file handling/processing.
            if isinstance(e, ValueError):
                raise  # Re-raise ValueErrors intentionally raised above
            elif isinstance(e, DatasetFileError):
                raise  # Re-raise DatasetFileErrors intentionally raised above
            else:
                raise DatasetFileError(f"Unexpected error reading or processing CSV file: {e}") from e

        return cls(name=name, data=data, description=description)

    def as_dataframe(self, multiindex: bool = True) -> "pd.DataFrame":
        """
        Convert the dataset to a pandas DataFrame for analysis.

        Args:
            multiindex (bool, optional): If True, expand 'input' and 'expected_output' dictionaries
                into columns using a pandas MultiIndex ('input'/'expected_output', key).
                If False, keep them as columns containing the raw dictionaries/strings. Defaults to True.

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

                input_data = record.get("input", {})
                if isinstance(input_data, dict):
                    for k, v in input_data.items():
                        flat_record[("input", k)] = v
                        column_tuples.add(("input", k))
                else:
                    flat_record[("input", "")] = input_data # Use empty string for single input
                    column_tuples.add(("input", ""))

                expected_output = record.get("expected_output", {})
                if isinstance(expected_output, dict):
                    for k, v in expected_output.items():
                        flat_record[("expected_output", k)] = v
                        column_tuples.add(("expected_output", k))
                else:
                    flat_record[("expected_output", "")] = expected_output # Use empty string for single output
                    column_tuples.add(("expected_output", ""))

                # Add remaining top-level fields under 'metadata'
                for k, v in record.items():
                    if k not in ["input", "expected_output"]:
                        flat_record[("metadata", k)] = v
                        column_tuples.add(("metadata", k))
                data_rows.append(flat_record)

            # Sort column tuples for consistent MultiIndex order
            column_tuples = sorted(list(column_tuples))

            records_list = []
            for flat_record in data_rows:
                row = [flat_record.get(col, None) for col in column_tuples]
                records_list.append(row)

            df = pd.DataFrame(records_list, columns=pd.MultiIndex.from_tuples(column_tuples))
            return df

        # Non-multiindex case
        data = []
        for record in self._data:
            new_record = {}
            input_data = record.get("input", {})
            new_record["input"] = input_data
            expected_output = record.get("expected_output", {})
            new_record["expected_output"] = expected_output
            for k, v in record.items():
                if k not in ["input", "expected_output"]:
                    new_record[k] = v
            data.append(new_record)
        return pd.DataFrame(data)

    def __repr__(self) -> str:
        """
        Return a readable string representation of the Dataset object, including name, size,
        structure sample, and synchronization status with Datadog.

        Returns:
            str: A human-friendly representation of the Dataset.
        """
        name = f"{Color.CYAN}{self.name}{Color.RESET}"
        record_count = len(self._data)

        structure = []
        if self._data:
            sample = self._data[0]
            input_data = sample.get("input", {})
            input_desc = f"dict[{len(input_data)} keys]" if isinstance(input_data, dict) else type(input_data).__name__
            structure.append(f"input: {input_desc}")

            expected = sample.get("expected_output", {})
            output_desc = f"dict[{len(expected)} keys]" if isinstance(expected, dict) else type(expected).__name__
            structure.append(f"expected_output: {output_desc}")

            metadata = {k: v for k, v in sample.items() if k not in ["input", "expected_output", "record_id"]}
            if metadata:
                structure.append(f"metadata: {len(metadata)} fields")

        has_changes = any(len(changes) > 0 for changes in self._changes.values())
        if getattr(self, "_datadog_dataset_id", None):
            if self._synced and not has_changes:
                dd_status = f"{Color.GREEN}✓ Synced{Color.RESET} (v{self._datadog_dataset_version})"
            else:
                dd_status = f"{Color.YELLOW}⚠ Unsynced changes{Color.RESET} (v{self._datadog_dataset_version})"
        else:
            dd_status = f"{Color.YELLOW}Local only{Color.RESET}"

        # Construct Datadog URL if synced
        dd_url = ""
        if getattr(self, "_datadog_dataset_id", None):
             panel_config = json.dumps([{"t": "datasetDetailPanel", "datasetId": self._datadog_dataset_id}])
             dd_url = (f"\n  URL: {Color.BLUE}https://app.datadoghq.com/llm/testing/datasets"
                      f"?llmPanels={quote(panel_config)}"
                      f"&openApplicationConfig=false{Color.RESET}")

        info = [
            f"Dataset(name={name})",
        ]
        if self.description:
            desc_preview = (self.description[:47] + "...") if len(self.description) > 50 else self.description
            info.append(f"  Description: {desc_preview}")

        info.extend([
            f"  Records: {record_count:,}",
        ])
        # Only add structure if it was determined
        if structure:
             info.append(f"  Structure: {', '.join(structure)}")
        info.extend([
            f"  Datadog: {dd_status}",
        ])

        if has_changes:
            changes_summary = []
            if self._changes['added']:
                changes_summary.append(f"+{len(self._changes['added'])} added")
            if self._changes['deleted']:
                changes_summary.append(f"-{len(self._changes['deleted'])} deleted")
            if self._changes['updated']:
                changes_summary.append(f"~{len(self._changes['updated'])} updated")
            info.append(f"  Changes: {', '.join(changes_summary)}")

        # Append URL only if it was constructed (i.e., dataset has an ID)
        if dd_url:
            info.append(dd_url)

        return "\n".join(info)


