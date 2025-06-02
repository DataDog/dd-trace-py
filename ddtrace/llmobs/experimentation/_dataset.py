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

    def push(self, new_version: bool = True) -> None:
        """
        Push the local dataset state to Datadog using the *batch_update* endpoint exclusively.

        There are only two behaviours:
            • new_version=True  (default): create a brand-new remote version based on the local
              state. The first chunk uses create_new_version=True to establish the new version,
              and subsequent chunks append to that same version.

            • new_version=False: mutate the latest remote version in-place. All chunks use
              create_new_version=False and overwrite=True to modify the existing version.

        All record mutations – creation, modification and deletion – are expressed through the
        standard ``insert_records``, ``update_records`` and ``delete_records`` attributes of the
        *DatasetBatchUpdateRequest* payload.
        """
        # Validate that the user is authenticated / configured.
        _validate_init()

        # Grab remote dataset metadata (if any)
        url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={quote(self.name)}"
        resp = exp_http_request("GET", url)
        remote_items = resp.json().get("data", [])

        if remote_items:
            remote_id = remote_items[0]["id"]
            current_version = remote_items[0]["attributes"].get("current_version", 0)
            # First time we learn about the dataset – store identifiers locally.
            if not getattr(self, "_datadog_dataset_id", None):
                self._datadog_dataset_id = remote_id
                self._datadog_dataset_version = current_version
            # Defensive check – should not really happen but better be explicit.
            elif self._datadog_dataset_id != remote_id:
                raise ValueError(
                    f"Local dataset ID ({self._datadog_dataset_id}) does not match remote dataset ID ({remote_id}) "
                    f"for name '{self.name}'.  Re-pull the dataset or recreate it before pushing again."
                )
        else:
            # Dataset does not exist remotely – create it first.
            self._create_remote_dataset()

        # At this point we must have a remote id.
        if not getattr(self, "_datadog_dataset_id", None):
            raise RuntimeError("Unable to determine remote dataset id – push aborted")

        # Determine which operations need to be propagated.
        has_changes = any(len(lst) > 0 for lst in self._changes.values())
        pushing_entire_dataset = not has_changes or (not remote_items)

        if pushing_entire_dataset:
            # Either this is a brand-new dataset or the user explicitly requested a full push via
            # Dataset changes reset (e.g. they removed and re-added records).  In both situations
            # we treat all local records as *insert_records*.
            insert_records = [self._build_insert_record(rec) for rec in self._data]
            update_records = []
            delete_records = []
            # If the local dataset was already synced and there are *no* changes we can safely
            # return early – nothing to do.
            if remote_items and not has_changes:
                print(
                    f"Dataset '{self.name}' (v{self._datadog_dataset_version}) is already synced and has no pending changes."
                )
                return
        else:
            # Build incremental payloads from the tracked mutations.
            insert_records = [self._build_insert_record(r) for r in self._changes["added"]]

            update_records = []
            for _, old_r, new_r in self._changes["updated"]:
                update_records.append(self._build_update_record(old_r, new_r))
            # Filter out no-ops (where the record did not actually change).
            update_records = [u for u in update_records if len(u) > 1]

            delete_records = []
            for _, r in self._changes["deleted"]:
                if "record_id" not in r:
                    raise ValueError("Cannot delete record without a record_id – did you pull before deleting?")
                delete_records.append(r["record_id"])

            # If after diff-ing we realise nothing changed, bail early.
            if not insert_records and not update_records and not delete_records:
                print(
                    f"Dataset '{self.name}' (v{self._datadog_dataset_version}) is already synced and has no pending changes."
                )
                self._clear_changes()
                return

        # Decide value of the *overwrite* flag for the first chunk.
        first_chunk_overwrite = not new_version  # If we want a new version, overwrite must be False.

        # Fire the batches.
        self._send_batch_updates(
            insert_records=insert_records,
            update_records=update_records,
            delete_records=delete_records,
            first_chunk_overwrite=first_chunk_overwrite,
        )

        # Give the backend a little time to process, then pull fresh copy to get new record ids & version.
        time.sleep(API_PROCESSING_TIME_SLEEP)
        self._refresh_from_remote()
        print(f"{Color.GREEN}✓ Dataset '{self.name}' pushed to Datadog{Color.RESET}")

    def _create_remote_dataset(self) -> None:
        """Create the remote dataset metadata entry and store its identifier locally."""
        payload = {
            "data": {
                "type": "datasets",
                "attributes": {
                    "name": self.name,
                    "description": self.description,
                },
            }
        }
        resp = exp_http_request("POST", "/api/unstable/llm-obs/v1/datasets", body=json.dumps(payload).encode("utf-8"))
        resp_data = resp.json()
        self._datadog_dataset_id = resp_data["data"]["id"]
        self._datadog_dataset_version = 0  # Starts at 0 – the backend will increment after first batch.

    @staticmethod
    def _build_insert_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an internal record representation into the *insert_records* payload format."""
        new_rec = {
            "input": record["input"],
            "expected_output": record["expected_output"],
        }
        metadata = {k: v for k, v in record.items() if k not in ["input", "expected_output", "record_id"]}
        if metadata:
            new_rec["metadata"] = metadata
        return new_rec

    @staticmethod
    def _build_update_record(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        """Create an *update_records* payload entry by diffing two versions of a record."""
        if "record_id" not in old:
            raise ValueError("Cannot update record without a record_id – did you pull before updating?")
        upd: Dict[str, Any] = {"id": old["record_id"]}
        # Diff core fields.
        if old.get("input") != new.get("input"):
            upd["input"] = new["input"]
        if old.get("expected_output") != new.get("expected_output"):
            upd["expected_output"] = new["expected_output"]
        # Diff metadata.
        old_meta = {k: v for k, v in old.items() if k not in ["input", "expected_output", "record_id"]}
        new_meta = {k: v for k, v in new.items() if k not in ["input", "expected_output", "record_id"]}
        if old_meta != new_meta:
            upd["metadata"] = new_meta
        return upd

    @staticmethod
    def _chunk_list(items: List[Any], size: int) -> List[List[Any]]:
        """Split *items* into *size*-bounded chunks (returns at least one chunk)."""
        if not items:
            return [[]]
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _send_batch_updates(
        self,
        *,
        insert_records: List[Dict[str, Any]],
        update_records: List[Dict[str, Any]],
        delete_records: List[str],
        first_chunk_overwrite: bool,
    ) -> None:
        """Send one or many calls to the batch_update endpoint, handling chunking & overwrite semantics."""
        if not self._datadog_dataset_id:
            raise ValueError("Dataset must have a remote id before sending batch updates")

        # Split *insert_records* into chunks – updates & deletes are expected to be much smaller so
        # they travel with the first chunk only.
        insert_chunks = self._chunk_list(insert_records, DEFAULT_CHUNK_SIZE)
        total_chunks = len(insert_chunks)
        show_progress = total_chunks > 1
        if show_progress:
            _print_progress_bar(0, total_chunks, prefix="Pushing records:", suffix="Complete")

        # Determine create_new_version for the entire operation
        # first_chunk_overwrite=False means we want a new version (create_new_version=True)
        # first_chunk_overwrite=True means we want to mutate existing version (create_new_version=False)
        create_new_version = not first_chunk_overwrite

        for idx, ins_chunk in enumerate(insert_chunks):
            attrs: Dict[str, Any] = {}
            if ins_chunk:
                attrs["insert_records"] = ins_chunk
            if idx == 0:
                if update_records:
                    attrs["update_records"] = update_records
                if delete_records:
                    attrs["delete_records"] = delete_records
            
            # Use create_new_version for first chunk, then overwrite=True for subsequent chunks
            # to append to the version established by the first chunk
            if idx == 0:
                attrs["create_new_version"] = create_new_version
                # For backward compatibility, also send overwrite (legacy field)
                attrs["overwrite"] = first_chunk_overwrite
            else:
                # Subsequent chunks should append to the same version created/modified by first chunk
                attrs["create_new_version"] = False  # Don't create another new version
                attrs["overwrite"] = True  # Append to existing version

            payload = {
                "data": {
                    "type": "datasets",
                    "id": self._datadog_dataset_id,
                    "attributes": attrs,
                }
            }

            url = f"/api/unstable/llm-obs/v1/datasets/{self._datadog_dataset_id}/batch_update"
            resp = exp_http_request("POST", url, body=json.dumps(payload).encode("utf-8"))

            if show_progress:
                _print_progress_bar(idx + 1, total_chunks, prefix="Pushing records:", suffix="Complete")

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


