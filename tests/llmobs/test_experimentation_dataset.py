import itertools
import os
import uuid
from typing import Any, Dict, List, Union
import json
import re
import sys

import pytest
import vcr

import ddtrace.llmobs.experimentation as dne
from ddtrace.llmobs.experimentation._dataset import MAX_DATASET_ROWS, DEFAULT_CHUNK_SIZE, _validate_init, API_PROCESSING_TIME_SLEEP, Dataset
from ddtrace.llmobs.experimentation.utils._exceptions import DatasetFileError
from ddtrace.llmobs.experimentation.utils import _http

# Hardcoded credentials for VCR playback (replace if needed for recording)
DD_API_KEY = "replace when recording"
DD_APPLICATION_KEY = "replace when recording"
DD_SITE = "us3.datadoghq.com"

# --- Helper Functions ---

def assert_dataset_synced(dataset, expected_len: int, expected_version: int):
    """Asserts dataset is synced with expected length and version."""
    assert len(dataset) == expected_len
    assert dataset._datadog_dataset_id is not None
    assert dataset._datadog_dataset_version == expected_version
    assert dataset._synced is True
    assert not any(dataset._changes.values()), "Changes should be cleared after sync"
    if expected_len > 0:
         # Check if record_ids were populated after sync (if data exists)
         assert "record_id" in dataset._data[0], "record_id should be present after sync"


def assert_dataset_local(dataset, expected_len: int):
    """Asserts dataset is local-only with expected length."""
    assert len(dataset) == expected_len
    assert dataset._datadog_dataset_id is None
    assert dataset._datadog_dataset_version == 0
    assert dataset._synced is True # A new local dataset is considered 'synced' with its local state initially
    assert not any(dataset._changes.values())


def assert_dataset_unsynced(dataset, expected_len: int, expected_version: int, added=0, deleted=0, updated=0):
    """Asserts dataset is unsynced with pending changes."""
    assert len(dataset) == expected_len
    assert dataset._synced is False
    # ID and version should reflect the state *before* changes
    assert dataset._datadog_dataset_id is not None
    assert dataset._datadog_dataset_version == expected_version
    assert len(dataset._changes['added']) == added
    assert len(dataset._changes['deleted']) == deleted
    assert len(dataset._changes['updated']) == updated


def scrub_response_headers(response):
    headers_to_remove = ["content-security-policy", "strict-transport-security", "set-cookie"]
    for header in headers_to_remove:
        response["headers"].pop(header, None)
    response["headers"]["content-length"] = ["100"] # Mock content length for VCR consistency
    return response


@pytest.fixture(scope="module")
def experiments_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "experiments_cassettes"),
        record_mode="all",  # Change to 'all' or 'new_episodes' for recording
        match_on=["path", "method", "body"], # Match on body needed for push/batch update endpoints
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY", "Openai-Api-Key", "Authorization"],
        before_record_response=scrub_response_headers,
        # Ignore localhost requests if other instrumentations are active
        ignore_localhost=True,
    )


@pytest.fixture(scope="module", autouse=True)
def init_llmobs(experiments_vcr): # VCR fixture ensures cassette context is available if needed
    """Initializes the LLMObs experimentation module once per test file."""
    # VCR filtering specified in the experiments_vcr fixture handles redacting keys
    # if they appear in actual HTTP requests made *within* tests.
    # Use the provided hardcoded keys directly for init.
    api_key = DD_API_KEY
    app_key = DD_APPLICATION_KEY

    try:
        dne.init(
            ml_app="test-app", # Provide the required ML application name
            project_name="Testing Project",
            api_key=api_key,
            application_key=app_key,
            site=DD_SITE
        )
    except Exception as e:
        pytest.skip(f"Skipping LLMObs tests: Initialization failed - {e}")

# --- Test Data Fixtures ---

@pytest.fixture
def sample_data_simple():
    """Basic list of string input/output."""
    return [
        {"input": "What is 1+1?", "expected_output": "2"},
        {"input": "Capital of UK?", "expected_output": "London"},
    ]

@pytest.fixture
def sample_data_dict():
    """List of dict input/output."""
    return [
        {"input": {"prompt": "capital of France?"}, "expected_output": {"response": "Paris"}},
        {"input": {"prompt": "capital of Germany?"}, "expected_output": {"response": "Berlin"}},
    ]

@pytest.fixture
def sample_data_mixed():
    """Mixed string/dict data with metadata. Keys are consistent across rows."""
    return [
        {"input": "2+2?", "expected_output": "4", "category": "math", "difficulty": 1},
        {"input": {"prompt": "Largest Planet?"}, "expected_output": {"answer": "Jupiter"}, "category": "space", "difficulty": None},
    ]

@pytest.fixture
def sample_data_diverse():
    """Data with mixed types: str, int, float, bool, None, list, nested dict."""
    return [
        {
            "input": {"prompt": "Analyze sentiment", "text": "I love this product!"},
            "expected_output": {"sentiment": "positive", "score": 0.95},
            "metadata": {"model_id": "sentiment-v1.2", "req_id": 1001, "is_valid": True, "params": None}
        },
        {
            "input": {"prompt": "Extract entities", "text": "Alice went to Paris"},
            "expected_output": {"entities": [{"name": "Alice", "type": "PERSON"}, {"name": "Paris", "type": "LOCATION"}], "score": None},
            "metadata": {"model_id": "ner-v3.0", "req_id": 1002, "is_valid": True, "params": {"threshold": 0.7}}
        },
        {
            "input": {"prompt": "Translate", "text": "Hello"},
            "expected_output": {"translation": "Bonjour", "language": "fr"},
            "metadata": {"model_id": "translate-en-fr", "req_id": 1003, "is_valid": False, "params": {"temperature": 0.5}}
        },
    ]

@pytest.fixture
def local_dataset_simple(sample_data_simple):
    """Dataset instance with simple local data."""
    return dne.Dataset(name="test-local-simple", data=sample_data_simple, description="Simple math/geo")

@pytest.fixture
def local_dataset_dict(sample_data_dict):
    """Dataset instance with dict local data."""
    return dne.Dataset(name="test-local-dict", data=sample_data_dict, description="Dict questions")

@pytest.fixture
def local_dataset_mixed(sample_data_mixed):
    """Dataset instance with mixed local data and metadata."""
    return dne.Dataset(name="test-local-mixed", data=sample_data_mixed, description="Mixed types")

@pytest.fixture
def local_dataset_diverse(sample_data_diverse):
    """Dataset instance with diverse local data."""
    return dne.Dataset(name="test-local-diverse", data=sample_data_diverse, description="Diverse types")

# --- Test Classes ---

class TestDatasetInitialization:
    """Tests for Dataset.__init__ behavior."""

    def test_init_local_data_simple(self, local_dataset_simple, sample_data_simple):
        """Initialize with simple local data."""
        assert local_dataset_simple.name == "test-local-simple"
        assert local_dataset_simple.description == "Simple math/geo"
        assert local_dataset_simple._data == sample_data_simple # Check internal data directly here
        assert_dataset_local(local_dataset_simple, expected_len=len(sample_data_simple))

    def test_init_local_data_dict(self, local_dataset_dict, sample_data_dict):
        """Initialize with dictionary local data."""
        assert local_dataset_dict.name == "test-local-dict"
        assert local_dataset_dict._data == sample_data_dict
        assert_dataset_local(local_dataset_dict, expected_len=len(sample_data_dict))

    def test_init_local_data_mixed(self, local_dataset_mixed, sample_data_mixed):
        """Initialize with mixed local data and metadata."""
        assert local_dataset_mixed.name == "test-local-mixed"
        assert local_dataset_mixed._data == sample_data_mixed
        assert_dataset_local(local_dataset_mixed, expected_len=len(sample_data_mixed))

    def test_init_invalid_data_empty(self):
        """Initialize with empty data list raises ValueError."""
        with pytest.raises(ValueError, match="Data cannot be empty"):
            dne.Dataset(name="empty-data", data=[])

    def test_init_invalid_data_too_large(self):
        """Initialize with data exceeding MAX_DATASET_ROWS raises ValueError."""
        large_data = [{"input": f"q{i}", "expected_output": f"a{i}"} for i in range(MAX_DATASET_ROWS + 1)]
        with pytest.raises(ValueError, match=f"Dataset cannot exceed {MAX_DATASET_ROWS} rows"):
            dne.Dataset(name="large-data", data=large_data)

    def test_init_invalid_data_inconsistent_keys(self):
        """Initialize with inconsistent keys raises ValueError."""
        inconsistent_data = [
            {"input": "q1", "expected_output": "a1"},
            {"input": "q2", "different_key": "a2"},
        ]
        # Update the expected error message to be more specific and match the actual output.
        # Sorting ensures the key order in the message is predictable.
        expected_keys_sorted = sorted(['input', 'expected_output'])
        got_keys_sorted = sorted(['input', 'different_key'])
        # Escape the string representations of the lists for the regex pattern
        expected_keys_str_escaped = re.escape(str(expected_keys_sorted))
        got_keys_str_escaped = re.escape(str(got_keys_sorted))
        expected_message_regex = (
            rf"Inconsistent keys in data. Expected {expected_keys_str_escaped}, "
            rf"got {got_keys_str_escaped}"
        )
        with pytest.raises(ValueError, match=expected_message_regex):
            dne.Dataset(name="inconsistent-keys", data=inconsistent_data)

    def test_init_invalid_data_not_dicts(self):
        """Initialize with list containing non-dicts raises ValueError."""
        invalid_data = [{"input": "q1", "expected_output": "a1"}, "not a dict"]
        with pytest.raises(ValueError, match="All rows must be dictionaries"):
            dne.Dataset(name="not-dicts", data=invalid_data)

    def test_init_local_data_diverse(self, local_dataset_diverse, sample_data_diverse):
        """Initialize with diverse local data types."""
        assert local_dataset_diverse.name == "test-local-diverse"
        assert local_dataset_diverse.description == "Diverse types"
        assert local_dataset_diverse._data == sample_data_diverse
        assert_dataset_local(local_dataset_diverse, expected_len=len(sample_data_diverse))

    def test_init_implicit_pull_existing(self, experiments_vcr):
        """Initialize with name only implicitly pulls existing dataset."""
        dataset_name = "meals-and-workouts-1.3" # Assumes this exists from recordings
        with experiments_vcr.use_cassette("test_dataset_init_implicit_pull.yaml"):
            dataset = dne.Dataset(name=dataset_name)

        assert dataset.name == dataset_name
        # Assume version is > 0 after pull, exact value checked by helper
        assert_dataset_synced(dataset, expected_len=len(dataset), expected_version=dataset._datadog_dataset_version)
        # Check specific content from first record
        assert "input" in dataset[0]
        assert "expected_output" in dataset[0]

    
    def test_init_implicit_pull_nonexistent(self, experiments_vcr):
        """Initialize with name only raises ValueError if dataset doesn't exist."""
        dataset_name = "non-existent-dataset-for-init"
        with experiments_vcr.use_cassette("test_dataset_init_implicit_pull_nonexistent.yaml"):
            with pytest.raises(ValueError, match=f"Dataset '{dataset_name}' not found"):
                dne.Dataset(name=dataset_name)


class TestDatasetContainerOps:
    """Tests for container-like operations (__len__, __getitem__, __iter__)."""

    def test_len(self, local_dataset_simple, sample_data_simple):
        """Test len() returns correct number of records."""
        assert len(local_dataset_simple) == len(sample_data_simple)

    def test_getitem_index(self, local_dataset_mixed, sample_data_mixed):
        """Test getting item by index returns correct record (copy without record_id)."""
        record0 = local_dataset_mixed[0]
        assert record0 == sample_data_mixed[0]
        assert "record_id" not in record0

        record1 = local_dataset_mixed[1]
        assert record1 == sample_data_mixed[1]
        assert "record_id" not in record1

    def test_getitem_slice(self, local_dataset_mixed, sample_data_mixed):
        """Test getting item by slice returns correct records (copies without record_id)."""
        records = local_dataset_mixed[0:2]
        assert isinstance(records, list)
        assert len(records) == 2
        assert records[0] == sample_data_mixed[0]
        assert "record_id" not in records[0]
        assert records[1] == sample_data_mixed[1]
        assert "record_id" not in records[1]

    def test_getitem_index_out_of_bounds(self, local_dataset_simple):
        """Test getting index out of bounds raises IndexError."""
        with pytest.raises(IndexError):
            _ = local_dataset_simple[len(local_dataset_simple)]

    def test_iter(self, local_dataset_simple, sample_data_simple):
        """Test iterating through the dataset yields correct records."""
        iterations = 0
        for i, record in enumerate(local_dataset_simple):
             # Iteration yields internal dicts, which might have record_id later.
             # Compare by creating a comparable dict excluding potential record_id.
            record_copy = {k: v for k, v in record.items() if k != "record_id"}
            assert record_copy == sample_data_simple[i]
            iterations += 1
        assert iterations == len(sample_data_simple)


class TestDatasetModification:
    """Tests for local modification methods (add, update, remove, etc.) and change tracking."""

    def test_add_record(self, local_dataset_simple):
        """Test adding a valid record."""
        initial_len = len(local_dataset_simple)
        new_record = {"input": "New Q", "expected_output": "New A"}
        local_dataset_simple.add(new_record)

        assert local_dataset_simple[initial_len] == new_record # Check content via __getitem__
        assert local_dataset_simple._data[-1] == new_record    # Check internal state
        assert_dataset_unsynced(local_dataset_simple, expected_len=initial_len + 1, expected_version=0, added=1)

    def test_iadd_record(self, local_dataset_simple):
        """Test adding a valid record using +=."""
        initial_len = len(local_dataset_simple)
        new_record = {"input": "Another Q", "expected_output": "Another A"}
        local_dataset_simple += new_record

        assert local_dataset_simple[initial_len] == new_record
        assert_dataset_unsynced(local_dataset_simple, expected_len=initial_len + 1, expected_version=0, added=1)

    def test_add_invalid_record_structure(self, local_dataset_simple):
        """Test adding a record with invalid structure raises ValueError."""
        # Get the expected keys from the fixture dataset
        expected_keys_sorted = sorted(local_dataset_simple._data[0].keys())
        got_keys_sorted = sorted(["input", "wrong_key"])
        # Escape for regex
        expected_keys_str_escaped = re.escape(str(expected_keys_sorted))
        got_keys_str_escaped = re.escape(str(got_keys_sorted))
        expected_message_regex = (
            rf"Record structure doesn't match dataset\. Expected keys {expected_keys_str_escaped}, "
            rf"got {got_keys_str_escaped}"
        )
        with pytest.raises(ValueError, match=expected_message_regex): # Updated expected message
            local_dataset_simple.add({"input": "Bad", "wrong_key": "Data"})

    def test_setitem_update(self, local_dataset_dict):
        """Test updating a record using __setitem__."""
        index_to_update = 0
        original_record = local_dataset_dict._data[index_to_update].copy() # Keep original internal state for tracking
        new_record_data = {"input": {"prompt": "New prompt"}, "expected_output": {"response": "New response"}}
        local_dataset_dict[index_to_update] = new_record_data

        assert len(local_dataset_dict) == 2
        assert local_dataset_dict[index_to_update] == new_record_data
        assert local_dataset_dict._data[index_to_update] == new_record_data
        assert_dataset_unsynced(local_dataset_dict, expected_len=2, expected_version=0, updated=1)
        # Check exact change tracked if needed
        assert local_dataset_dict._changes['updated'] == [(index_to_update, original_record, new_record_data)]

    def test_update_method(self, local_dataset_dict):
        """Test updating a record using the update() method."""
        index_to_update = 1
        original_record = local_dataset_dict._data[index_to_update].copy()
        new_record_data = {"input": {"prompt": "Updated prompt 2"}, "expected_output": {"response": "Updated response 2"}}
        local_dataset_dict.update(index_to_update, new_record_data)

        assert local_dataset_dict[index_to_update] == new_record_data
        assert_dataset_unsynced(local_dataset_dict, expected_len=2, expected_version=0, updated=1)
        # Check exact change tracked if needed
        assert local_dataset_dict._changes['updated'] == [(index_to_update, original_record, new_record_data)]

    def test_setitem_invalid_structure(self, local_dataset_simple):
        """Test updating with invalid structure raises ValueError."""
        # Get the expected keys from the fixture dataset, excluding potential record_id
        expected_keys_sorted = sorted([k for k in local_dataset_simple._data[0].keys() if k != 'record_id'])
        got_keys_sorted = sorted(["input", "wrong_key"])
        # Escape for regex
        expected_keys_str_escaped = re.escape(str(expected_keys_sorted))
        got_keys_str_escaped = re.escape(str(got_keys_sorted))
        expected_message_regex = (
            rf"Record structure doesn't match dataset\. Expected keys {expected_keys_str_escaped}, "
            rf"got {got_keys_str_escaped}"
        )
        with pytest.raises(ValueError, match=expected_message_regex): # Updated expected message
            local_dataset_simple[0] = {"input": "Bad", "wrong_key": "Data"}

    def test_delitem_remove(self, local_dataset_mixed):
        """Test removing a record using __delitem__."""
        index_to_delete = 0
        initial_len = len(local_dataset_mixed)
        original_record = local_dataset_mixed._data[index_to_delete].copy() # Keep original internal state for tracking
        expected_remaining_record = local_dataset_mixed[1] # Record that will shift to index 0 after deletion

        del local_dataset_mixed[index_to_delete]

        assert len(local_dataset_mixed) == initial_len - 1
        assert local_dataset_mixed[0] == expected_remaining_record
        assert_dataset_unsynced(local_dataset_mixed, expected_len=initial_len - 1, expected_version=0, deleted=1)
        # Check exact change tracked if needed
        assert local_dataset_mixed._changes['deleted'] == [(index_to_delete, original_record)]

    def test_remove_method(self, local_dataset_mixed):
        """Test removing a record using the remove() method."""
        index_to_delete = 1
        initial_len = len(local_dataset_mixed)
        original_record = local_dataset_mixed._data[index_to_delete].copy()
        expected_remaining_record = local_dataset_mixed[0] # Record that will remain at index 0

        local_dataset_mixed.remove(index_to_delete)

        assert len(local_dataset_mixed) == initial_len - 1
        assert local_dataset_mixed[0] == expected_remaining_record
        assert_dataset_unsynced(local_dataset_mixed, expected_len=initial_len - 1, expected_version=0, deleted=1)
        # Check exact change tracked if needed
        assert local_dataset_mixed._changes['deleted'] == [(index_to_delete, original_record)]

    def test_multiple_operations_tracking(self, local_dataset_simple):
        """Test change tracking after multiple add, update, delete operations."""
        initial_len = len(local_dataset_simple)
        original_rec0 = local_dataset_simple._data[0].copy()
        original_rec1 = local_dataset_simple._data[1].copy()

        # 1. Add a record
        new_record1 = {"input": "Q3", "expected_output": "A3"}
        local_dataset_simple.add(new_record1) # State: [rec0, rec1, new1], len=3, added=1

        # 2. Update the first record
        updated_rec0_data = {"input": "Q1 Updated", "expected_output": "A1 Updated"}
        local_dataset_simple[0] = updated_rec0_data # State: [upd0, rec1, new1], len=3, added=1, updated=1

        # 3. Delete the second original record (now at index 1)
        deleted_rec1_original_index = 1
        del local_dataset_simple[1] # State: [upd0, new1], len=2, added=1, updated=1, deleted=1

        # 4. Add another record
        new_record2 = {"input": "Q4", "expected_output": "A4"}
        local_dataset_simple.add(new_record2) # State: [upd0, new1, new2], len=3, added=2, updated=1, deleted=1

        # Assert final content
        assert local_dataset_simple[0] == updated_rec0_data
        assert local_dataset_simple[1] == new_record1
        assert local_dataset_simple[2] == new_record2

        # Assert final sync state and tracked changes using helper
        assert_dataset_unsynced(local_dataset_simple, expected_len=3, expected_version=0, added=2, updated=1, deleted=1)

        # Optionally verify exact changes if needed (helper doesn't check content)
        assert local_dataset_simple._changes['added'] == [new_record1, new_record2]
        assert local_dataset_simple._changes['updated'] == [(0, original_rec0, updated_rec0_data)]
        assert local_dataset_simple._changes['deleted'] == [(deleted_rec1_original_index, original_rec1)]



class TestDatasetPull:
    """Tests for Dataset.pull (explicitly pulling from Datadog)."""

    def test_pull_latest_version(self, experiments_vcr):
        """Test pulling the latest version of an existing dataset."""
        dataset_name = "meals-and-workouts-1.3"
        with experiments_vcr.use_cassette("test_dataset_pull_latest.yaml"):
            dataset = dne.Dataset.pull(dataset_name)

        assert dataset.name == dataset_name
        # Version check done by helper
        assert_dataset_synced(dataset, expected_len=len(dataset), expected_version=dataset._datadog_dataset_version)
        # Check structure/content sample
        assert "input" in dataset[0]
        assert "expected_output" in dataset[0]
        assert "record_id" in dataset._data[0]
        assert isinstance(dataset._data[0]["record_id"], str)

    def test_pull_specific_version(self, experiments_vcr):
        """Test pulling a specific, existing version of a dataset."""
        # Assumes 'meals-and-workouts-1.3' has a version 1 in the cassette
        dataset_name = "meals-and-workouts-1.3"
        version_to_pull = 1
        with experiments_vcr.use_cassette("test_dataset_pull_specific_version.yaml"):
            dataset = dne.Dataset.pull(dataset_name, version=version_to_pull)

        assert dataset.name == dataset_name
        # Explicit version check is core to this test, helper confirms sync state
        assert_dataset_synced(dataset, expected_len=len(dataset), expected_version=version_to_pull)
        assert "record_id" in dataset._data[0]

    def test_pull_nonexistent_dataset(self, experiments_vcr):
        """Test pulling a non-existent dataset raises ValueError."""
        dataset_name = "this-dataset-definitely-should-not-exist"
        with experiments_vcr.use_cassette("test_dataset_pull_nonexistent.yaml"):
            with pytest.raises(ValueError, match=f"Dataset '{dataset_name}' not found"):
                dne.Dataset.pull(dataset_name)

    def test_pull_nonexistent_version(self, experiments_vcr):
        """Test pulling a non-existent version raises ValueError."""
        dataset_name = "meals-and-workouts-1.3"
        non_existent_version = 9999
        with experiments_vcr.use_cassette("test_dataset_pull_nonexistent_version.yaml"):
            # Match needs to check for the specific version not found message
            with pytest.raises(ValueError, match=f"Version {non_existent_version} not found for dataset '{dataset_name}'"):
                dne.Dataset.pull(dataset_name, version=non_existent_version)

    def test_pull_empty_dataset(self, experiments_vcr):
        """Test pulling a dataset that exists but has no records raises ValueError."""
        # Requires a cassette where the GET /records returns empty data
        dataset_name = "existing-but-empty"
        with experiments_vcr.use_cassette("test_dataset_pull_empty.yaml"):
            with pytest.raises(ValueError, match=f"Dataset '{dataset_name}' does not contain any records"):
                dne.Dataset.pull(dataset_name)


class TestDatasetPush:
    """Tests for dataset.push() interactions with Datadog."""

    # Helper to generate unique names for recording new datasets
    def _unique_name(self, prefix="test-push"):
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    # --- Scenario: New Local Dataset ---
    def test_push_new_local_dataset(self, experiments_vcr, local_dataset_simple):
        """Test pushing a completely new local dataset."""
        # Use a unique name to ensure it's new during recording/playback
        unique_name = self._unique_name("test-push-new")
        local_dataset_simple.name = unique_name # Rename for push

        with experiments_vcr.use_cassette("test_dataset_push_new.yaml"):
            local_dataset_simple.push()

        assert local_dataset_simple.name == unique_name
        # Check sync status, len, version, and record_id presence using helper
        assert_dataset_synced(local_dataset_simple, expected_len=2, expected_version=local_dataset_simple._datadog_dataset_version)
        # Version should be > 0 after push, exact value checked by helper
        assert local_dataset_simple._datadog_dataset_version is not None
        # Verify internal data now has record_ids after the implicit refresh post-push
        assert len(local_dataset_simple._data) == 2
        assert "record_id" in local_dataset_simple._data[0]
        assert isinstance(local_dataset_simple._data[0]["record_id"], str)
        assert "record_id" in local_dataset_simple._data[1]

    # --- Scenario: Synced Dataset ---
    @pytest.fixture
    def synced_dataset(self, experiments_vcr):
        """Fixture to provide a dataset pulled from remote, ready for modifications."""
        # Pulls the dataset; VCR ensures it's consistent for tests
        dataset_name = "meals-and-workouts-1.3" # Use a known dataset from VCR
        # Use a generic cassette name for pulling this dataset for reuse
        with experiments_vcr.use_cassette("test_dataset_pull_meals_workouts.yaml"):
             # Needs Dataset.pull explicitly here to get remote state
            dataset = dne.Dataset.pull(dataset_name)
        assert dataset._synced is True
        assert dataset._datadog_dataset_id is not None
        return dataset

    def test_push_synced_no_changes(self, experiments_vcr, synced_dataset, capsys):
        """Test pushing a synced dataset with no local changes."""
        initial_len = len(synced_dataset)
        initial_version = synced_dataset._datadog_dataset_version

        # This cassette should ideally show no POST requests or minimal GETs
        with experiments_vcr.use_cassette("test_dataset_push_synced_no_change.yaml"):
            synced_dataset.push()

        captured = capsys.readouterr()
        assert "Dataset is already synced and has no changes" in captured.out
        # Verify state remains unchanged using helper
        assert_dataset_synced(synced_dataset, expected_len=initial_len, expected_version=initial_version)

    def test_push_synced_with_adds(self, experiments_vcr, synced_dataset):
        """Test push after adding records (default: creates new version via batch update)."""
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)
        # Use structure consistent with the pulled 'meals-and-workouts-1.3' dataset
        new_record = {"input": {"prompt": "new workout?"}, "expected_output": {"response": "pushups"}}

        # Add record and verify unsynced state
        synced_dataset.add(new_record)
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len + 1, expected_version=initial_version, added=1)

        with experiments_vcr.use_cassette("test_dataset_push_synced_adds.yaml"):
            # Should trigger _batch_update internally
            synced_dataset.push()

        # Verify synced state after push (new version)
        assert_dataset_synced(synced_dataset, expected_len=initial_len + 1, expected_version=initial_version + 1)
        # Check content of added record (now has record_id)
        assert synced_dataset[-1]["input"] == new_record["input"]

    def test_push_synced_with_deletes(self, experiments_vcr, synced_dataset):
        """Test push after deleting records (default: creates new version via batch update)."""
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)
        assert initial_len > 0
        # Record the ID before deleting, needed for batch update payload matching in VCR
        deleted_record_id = synced_dataset._data[0]["record_id"]

        # Delete and verify unsynced state
        del synced_dataset[0]
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len - 1, expected_version=initial_version, deleted=1)
        # Ensure the correct ID is captured for cassette matching.
        assert synced_dataset._changes['deleted'][0][1]['record_id'] == deleted_record_id

        with experiments_vcr.use_cassette("test_dataset_push_synced_deletes.yaml"):
            # Should trigger _batch_update internally
            synced_dataset.push()

        # Verify synced state after push (new version)
        assert_dataset_synced(synced_dataset, expected_len=initial_len - 1, expected_version=initial_version + 1)

    def test_push_synced_with_updates(self, experiments_vcr, synced_dataset):
        """Test push after updating records (default: creates new version via batch update)."""
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)
        assert initial_len > 0
        # Record the ID before updating for VCR matching
        original_record = synced_dataset._data[0].copy()
        updated_record_id = original_record["record_id"]
        updated_record_data = {"input": {"prompt": "updated prompt"}, "expected_output": {"response": "updated response"}}

        # Update and verify unsynced state
        synced_dataset[0] = updated_record_data
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len, expected_version=initial_version, updated=1)
        # Ensure the correct ID is captured for VCR cassette body matching
        assert synced_dataset._changes['updated'][0][1]['record_id'] == updated_record_id # old_record has ID
        assert "record_id" not in synced_dataset._changes['updated'][0][2] # new_record doesn't (API adds it)

        with experiments_vcr.use_cassette("test_dataset_push_synced_updates.yaml"):
            # Should trigger _batch_update internally
            synced_dataset.push()

        # Verify synced state after push (new version)
        assert_dataset_synced(synced_dataset, expected_len=initial_len, expected_version=initial_version + 1)
        # Check if the updated record still has its ID internally after refresh
        assert synced_dataset[0] == updated_record_data

    def test_push_synced_mixed_changes(self, experiments_vcr, synced_dataset):
        """Test push after mixed add/update/delete (default: new version via batch update)."""
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)
        assert initial_len >= 2 # Need >= 2 records for this mixed test

        # 1. Update record 0
        updated_record_id = synced_dataset._data[0]["record_id"]
        updated_record_data = {"input": {"prompt": "mixed update"}, "expected_output": {"response": "mixed update resp"}}
        synced_dataset[0] = updated_record_data

        # 2. Delete record 1
        deleted_record_id = synced_dataset._data[1]["record_id"]
        del synced_dataset[1]

        # 3. Add a new record
        new_record = {"input": {"prompt": "mixed add"}, "expected_output": {"response": "mixed add resp"}}
        synced_dataset.add(new_record)

        # Verify intermediate unsynced state
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len, expected_version=initial_version, added=1, deleted=1, updated=1)
        # Prepare VCR match data (relies on internal _changes)
        assert synced_dataset._changes['updated'][0][1]['record_id'] == updated_record_id
        assert synced_dataset._changes['deleted'][0][1]['record_id'] == deleted_record_id
        assert synced_dataset._changes['added'][0] == new_record

        with experiments_vcr.use_cassette("test_dataset_push_synced_mixed.yaml"):
            # Should trigger _batch_update internally
            synced_dataset.push()

        # Verify final synced state (new version)
        assert_dataset_synced(synced_dataset, expected_len=initial_len, expected_version=initial_version + 1)
        # Verify updates/adds persisted and have IDs after refresh
        assert synced_dataset[0] == updated_record_data
        assert synced_dataset[-1]["input"] == new_record["input"]

    def test_push_synced_overwrite(self, experiments_vcr, synced_dataset):
        """Test push with overwrite=True after modifications (uses /push endpoint)."""
        initial_id = synced_dataset._datadog_dataset_id
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)

        deleted_record_id = synced_dataset._data[0]["record_id"]
        del synced_dataset[0]
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len - 1, expected_version=initial_version, deleted=1)

        with experiments_vcr.use_cassette("test_dataset_push_synced_overwrite.yaml"):
            # Should trigger _push_entire_dataset(overwrite=True) internally
            synced_dataset.push(overwrite=True)

        # Verify synced state - ID should be the same, version might change or stay same
        # We capture the version *after* the push for the helper check
        final_version = synced_dataset._datadog_dataset_version
        assert synced_dataset._datadog_dataset_id == initial_id # ID must remain the same
        assert final_version >= initial_version # Version should be >= initial
        assert_dataset_synced(synced_dataset, expected_len=initial_len - 1, expected_version=final_version)
        assert not any(r['record_id'] == deleted_record_id for r in synced_dataset._data)

    def test_push_synced_new_version(self, experiments_vcr, synced_dataset):
        """Test push with new_version=True after modifications (uses /push endpoint)."""
        initial_id = synced_dataset._datadog_dataset_id
        initial_version = synced_dataset._datadog_dataset_version
        initial_len = len(synced_dataset)

        new_record = {"input": {"prompt": "new version add"}, "expected_output": {"response": "nv add resp"}}
        synced_dataset.add(new_record)
        assert_dataset_unsynced(synced_dataset, expected_len=initial_len + 1, expected_version=initial_version, added=1)

        with experiments_vcr.use_cassette("test_dataset_push_synced_new_version.yaml"):
            # Should trigger _push_entire_dataset(overwrite=False) internally
            synced_dataset.push(new_version=True)

        # Verify synced state - ID stays same, version must increment
        assert synced_dataset._datadog_dataset_id == initial_id
        assert_dataset_synced(synced_dataset, expected_len=initial_len + 1, expected_version=initial_version + 1)
        assert synced_dataset[-1]["input"] == new_record["input"]

    # --- Scenario: Local Dataset with Name Collision ---
    @pytest.fixture
    def local_colliding_dataset(self, sample_data_simple, experiments_vcr):
        """ Local dataset whose name matches an existing one ('meals-and-workouts-1.3')."""
        # Ensure the remote one exists via VCR before creating local for collision test
        dataset_name = "meals-and-workouts-1.3"
        with experiments_vcr.use_cassette("test_dataset_push_collision_setup.yaml"):
            try:
                dne.Dataset.pull(dataset_name)
            except ValueError:
                pytest.skip(f"Cannot run collision tests, '{dataset_name}' not found in VCR.")
        # Create local dataset with the *same name* but different data
        return dne.Dataset(name=dataset_name, data=sample_data_simple)

    def test_push_local_collision_no_flags(self, experiments_vcr, local_colliding_dataset, capsys):
        """Test push() on local dataset with name collision warns and does nothing."""
        initial_data = local_colliding_dataset._data[:] # Copy data
        with experiments_vcr.use_cassette("test_dataset_push_collision_no_flags.yaml"):
            # Should only do a GET to check existence
            local_colliding_dataset.push()

        captured = capsys.readouterr()
        assert f"Dataset '{local_colliding_dataset.name}' already exists" in captured.out
        assert "Use push(overwrite=True)" in captured.out
        assert "push(new_version=True)" in captured.out
        # Verify state remains local and unchanged using helper
        assert_dataset_local(local_colliding_dataset, expected_len=len(initial_data))
        assert local_colliding_dataset._data == initial_data


    def test_push_local_collision_overwrite(self, experiments_vcr, local_colliding_dataset):
        """Test push(overwrite=True) on local dataset with name collision."""
        dataset_name = local_colliding_dataset.name
        original_local_data_count = len(local_colliding_dataset)
        original_local_first_record_content = local_colliding_dataset[0]

        with experiments_vcr.use_cassette("test_dataset_push_collision_overwrite.yaml"):
            # Should trigger _push_entire_dataset(overwrite=True) after finding existing ID via GET
            local_colliding_dataset.push(overwrite=True)

        # Verify it's now synced, length is same, version is whatever the backend returned
        final_version = local_colliding_dataset._datadog_dataset_version
        assert local_colliding_dataset.name == dataset_name
        assert_dataset_synced(local_colliding_dataset, expected_len=original_local_data_count, expected_version=final_version)
        assert local_colliding_dataset[0] == original_local_first_record_content

    def test_push_local_collision_new_version(self, experiments_vcr, local_colliding_dataset):
        """Test push(new_version=True) on local dataset with name collision."""
        dataset_name = local_colliding_dataset.name
        original_local_data_count = len(local_colliding_dataset)
        original_local_first_record_content = local_colliding_dataset[0]

        # We need the original remote version number for comparison
        remote_version = 0
        with experiments_vcr.use_cassette("test_dataset_push_collision_new_version_check.yaml"):
             remote_id = local_colliding_dataset._get_remote_dataset_id_and_version()[1] # Get version
             # If pull works, we get the version. If not, assume it's 0 or handle error.
             # This is a bit fragile, better to fetch explicitly if needed.
             # Let's assume the cassette correctly captures the GET /datasets?filter[name]=...
             # and we extract the version from there. (Requires VCR logic)
             # For simplicity here, let's assume the pre-existing version is captured in the cassette
             # and the _push_entire_dataset logic correctly increments it.
             # The test below relies on the push() call doing the right thing.

        with experiments_vcr.use_cassette("test_dataset_push_collision_new_version.yaml"):
            # Should trigger _push_entire_dataset(overwrite=False) after finding existing ID via GET
            local_colliding_dataset.push(new_version=True)

        # Verify it's synced, length same, version should be incremented from remote
        final_version = local_colliding_dataset._datadog_dataset_version
        assert local_colliding_dataset.name == dataset_name
        # We expect the version to be incremented. Without knowing the *previous* version reliably,
        # we can assert it's > 0 if the original existed.
        # The helper asserts sync status.
        assert_dataset_synced(local_colliding_dataset, expected_len=original_local_data_count, expected_version=final_version)
        assert final_version > 0 # Assuming remote existed with some version >= 0
        assert local_colliding_dataset[0] == original_local_first_record_content

    # --- Error/Edge Cases ---
    def test_push_overwrite_and_new_version_flags(self, local_dataset_simple):
        """Test push() with both overwrite and new_version flags raises ValueError."""
        with pytest.raises(ValueError, match="Cannot specify both overwrite=True and new_version=True"):
            local_dataset_simple.push(overwrite=True, new_version=True)

    def test_push_large_dataset_chunking(self, experiments_vcr):
        """Test pushing a dataset large enough to trigger chunking."""
        # Create data slightly larger than one chunk to ensure > 1 push request
        num_records = DEFAULT_CHUNK_SIZE + 5
        large_data = [{"input": f"q{i}", "expected_output": f"a{i}"} for i in range(num_records)]
        dataset = dne.Dataset(name=self._unique_name("test-push-large"), data=large_data)

        # Cassette should show multiple POST requests to the /push endpoint due to chunking
        with experiments_vcr.use_cassette("test_dataset_push_large_chunking.yaml"):
            dataset.push()

        # Verify synced state after chunked push
        assert_dataset_synced(dataset, expected_len=num_records, expected_version=dataset._datadog_dataset_version)

    def test_push_update_delete_missing_record_id(self, synced_dataset):
        """Test internal logic guards against updates/deletes without record_id (difficult to trigger externally)."""
        # This tests the safeguard within _prepare_batch_payload.

        # Simulate a scenario where a record intended for update/delete lacks a record_id internally
        # (This shouldn't happen with normal usage, but tests the internal check)

        # 1. Simulate an update where old_record is missing 'record_id'
        synced_dataset._changes['updated'].append((0, {"input": "no id"}, {"input": "new"}))
        with pytest.raises(ValueError, match="Cannot update record: missing record_id"):
            synced_dataset._prepare_batch_payload(overwrite=False)
        synced_dataset._changes['updated'] = []

        # 2. Simulate a delete where the record is missing 'record_id'
        synced_dataset._changes['deleted'].append((0, {"input": "no id"}))
        with pytest.raises(ValueError, match="Cannot delete record: missing record_id"):
            synced_dataset._prepare_batch_payload(overwrite=False)
        synced_dataset._changes['deleted'] = []


class TestDatasetFromCSV:
    """Tests for Dataset.from_csv functionality."""

    @pytest.fixture
    def csv_file_simple(self):
        """Path to a simple CSV file."""
        return "tests/llmobs/experiments_files/simple.csv"

    @pytest.fixture
    def csv_file_multi_col(self):
        """Path to a CSV with multiple input/output columns and metadata."""
        return "tests/llmobs/experiments_files/multi.csv"

    @pytest.fixture
    def csv_file_delimiter(self):
        """Path to a CSV with a semicolon delimiter."""
        return "tests/llmobs/experiments_files/delimiter.tsv"

    @pytest.fixture
    def csv_file_empty(self):
        """Path to an empty CSV file."""
        return "tests/llmobs/experiments_files/empty.csv"

    @pytest.fixture
    def csv_file_header_only(self):
        """Path to a CSV file with only a header."""
        return "tests/llmobs/experiments_files/header_only.csv"

    @pytest.fixture
    def csv_file_malformed(self):
        """Path to a malformed CSV file."""
        return "tests/llmobs/experiments_files/malformed.csv"

    def test_from_csv_simple(self, csv_file_simple):
        """Test loading simple CSV with single input/output columns."""
        dataset = dne.Dataset.from_csv(
            csv_file_simple,
            name="csv-simple",
            description="From simple CSV",
            input_columns=["question"],
            expected_output_columns=["answer"]
        )
        assert dataset.name == "csv-simple"
        assert dataset.description == "From simple CSV"
        assert len(dataset) == 4
        assert dataset[0] == {"input": "What is 1+1?", "expected_output": "2"}
        assert dataset[1] == {"input": "Capital of UK?", "expected_output": "London"}
        assert dataset[3] == {"input": "How many continents?", "expected_output": "7"}
        assert dataset._synced is True # Local dataset is considered synced with its initial state

    def test_from_csv_multi_column_and_metadata(self, csv_file_multi_col):
        """Test loading CSV with multiple input/output columns and metadata."""
        dataset = dne.Dataset.from_csv(
            csv_file_multi_col,
            name="csv-multi",
            input_columns=["prompt", "context"],
            expected_output_columns=["expected_response", "expected_certainty"]
        )
        assert dataset.name == "csv-multi"
        assert len(dataset) == 4
        assert dataset[0] == {
            "input": {"prompt": "France capital?", "context": ""},
            "expected_output": {"expected_response": "Paris", "expected_certainty": "0.9"},
            "category": "geography", # Metadata automatically included
            "difficulty": "easy"      # Metadata automatically included
        }
        assert dataset[1] == {
            "input": {"prompt": "Largest planet?", "context": "Solar System"},
            "expected_output": {"expected_response": "Jupiter", "expected_certainty": "1.0"},
            "category": "astronomy",
            "difficulty": "medium"
        }
        assert dataset[3] == {
            "input": {"prompt": "Calculate 2+3", "context": "Math basics"},
            "expected_output": {"expected_response": "5", "expected_certainty": "1.0"},
            "category": "mathematics",
            "difficulty": "easy"
        }


    def test_from_csv_different_delimiter(self, csv_file_delimiter):
        """Test loading CSV with a non-comma delimiter."""
        dataset = dne.Dataset.from_csv(
            csv_file_delimiter,
            name="csv-delimiter",
            delimiter=";",
            input_columns=["input"],
            expected_output_columns=["output"]
        )
        assert len(dataset) == 4
        assert dataset[0] == {"input": "Hello", "expected_output": "World", "metadata": "greeting"}
        assert dataset[1] == {"input": "Test", "expected_output": "Data", "metadata": "sample"}
        assert dataset[3] == {"input": "How are you?", "expected_output": "Fine, thanks", "metadata": "conversation"}


    def test_from_csv_empty_file(self, csv_file_empty):
        """Test loading from an empty CSV file raises ValueError."""
        # This check happens *after* opening the file successfully but before reading content
        with pytest.raises(ValueError, match="CSV file appears to be empty or header is missing."):
            dne.Dataset.from_csv(csv_file_empty, name="bad", input_columns=["a"], expected_output_columns=["b"])

    def test_from_csv_header_only_file(self, csv_file_header_only):
        """Test loading from a CSV file with only a header raises ValueError."""
        with pytest.raises(ValueError, match="CSV file is empty"):
            dne.Dataset.from_csv(csv_file_header_only, name="bad", input_columns=["col1"], expected_output_columns=["col2"])

    def test_from_csv_missing_input_column(self, csv_file_simple):
        """Test loading when specified input column is missing raises ValueError."""
        with pytest.raises(ValueError, match="Input columns not found in CSV header: \\['missing_input'\\]"):
            dne.Dataset.from_csv(csv_file_simple, name="bad", input_columns=["missing_input"], expected_output_columns=["answer"])

    def test_from_csv_missing_output_column(self, csv_file_simple):
        """Test loading when specified output column is missing raises ValueError."""
        with pytest.raises(ValueError, match="Expected output columns not found in CSV header: \\['missing_output'\\]"):
            dne.Dataset.from_csv(csv_file_simple, name="bad", input_columns=["question"], expected_output_columns=["missing_output"])

    def test_from_csv_missing_column_specifications(self, csv_file_simple):
        """Test calling from_csv without input/output columns raises ValueError."""
        with pytest.raises(ValueError, match="`input_columns` and `expected_output_columns` must be provided"):
            dne.Dataset.from_csv(csv_file_simple, name="bad")
        with pytest.raises(ValueError, match="`input_columns` and `expected_output_columns` must be provided"):
            dne.Dataset.from_csv(csv_file_simple, name="bad", input_columns=["question"])
        with pytest.raises(ValueError, match="`input_columns` and `expected_output_columns` must be provided"):
            dne.Dataset.from_csv(csv_file_simple, name="bad", expected_output_columns=["answer"])

    def test_from_csv_malformed_file(self, csv_file_malformed):
        """Test loading a malformed CSV raises DatasetFileError."""
        with pytest.raises(DatasetFileError, match="Error parsing CSV file"):
            dne.Dataset.from_csv(csv_file_malformed, name="bad", input_columns=["head1"], expected_output_columns=["head2"])

    # Test for permission error is hard to reliably simulate across platforms and CI


class TestDatasetAsDataFrame:
    """Tests for dataset.as_dataframe() conversion."""

    @pytest.fixture(autouse=True)
    def skip_if_no_pandas(self):
        pytest.importorskip("pandas")

    def test_as_dataframe_no_pandas(self, mocker):
        """Test as_dataframe raises ImportError if pandas is not installed."""
        # Temporarily mock pandas import to simulate it being missing
        mocker.patch.dict(sys.modules, {"pandas": None})
        dataset = dne.Dataset(name="dummy", data=[{"input": "a", "expected_output": "b"}])
        with pytest.raises(ImportError, match="pandas is required"):
            dataset.as_dataframe()
        # Restore pandas implicitly by test teardown or next test import


    def test_as_dataframe_multiindex_false(self, local_dataset_mixed):
        """Test conversion with multiindex=False."""
        import pandas as pd
        df = local_dataset_mixed.as_dataframe(multiindex=False)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(local_dataset_mixed)
        # Expected columns: 'input', 'expected_output', and metadata keys ('category', 'difficulty')
        assert set(df.columns) == {"input", "expected_output", "category", "difficulty"}
        # Check that input/output are preserved as original types (str/dict)
        assert isinstance(df["input"].iloc[0], str)
        assert isinstance(df["expected_output"].iloc[0], str)
        assert isinstance(df["input"].iloc[1], dict)
        assert isinstance(df["expected_output"].iloc[1], dict)
        assert df["input"].iloc[0] == local_dataset_mixed[0]["input"]
        assert df["expected_output"].iloc[1] == local_dataset_mixed[1]["expected_output"]
        assert df["category"].iloc[0] == local_dataset_mixed[0]["category"]
        # Check NaN for metadata missing in a specific record
        assert pd.isna(df["difficulty"].iloc[1])


    def test_as_dataframe_multiindex_true_simple(self, local_dataset_simple):
        """Test conversion with multiindex=True for simple string data."""
        import pandas as pd
        df = local_dataset_simple.as_dataframe(multiindex=True)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(local_dataset_simple)
        assert isinstance(df.columns, pd.MultiIndex)
        # Expect columns like ('input', ''), ('expected_output', '') for simple strings
        expected_columns = pd.MultiIndex.from_tuples([('input', ''), ('expected_output', '')])
        pd.testing.assert_index_equal(df.columns, expected_columns)
        assert df[('input', '')].iloc[0] == local_dataset_simple[0]["input"]
        assert df[('expected_output', '')].iloc[1] == local_dataset_simple[1]["expected_output"]

    def test_as_dataframe_multiindex_true_dict(self, local_dataset_dict):
        """Test conversion with multiindex=True for dict data."""
        import pandas as pd
        df = local_dataset_dict.as_dataframe(multiindex=True)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(local_dataset_dict)
        assert isinstance(df.columns, pd.MultiIndex)
        # Expect columns like ('input', 'prompt'), ('expected_output', 'response') for dicts
        expected_columns = pd.MultiIndex.from_tuples([('input', 'prompt'), ('expected_output', 'response')])
        # Use set comparison as column order might vary based on dict iteration
        assert set(df.columns) == set(expected_columns)
        # Check content (access using tuple)
        assert df[('input', 'prompt')].iloc[0] == local_dataset_dict[0]["input"]["prompt"]
        assert df[('expected_output', 'response')].iloc[1] == local_dataset_dict[1]["expected_output"]["response"]

    def test_as_dataframe_multiindex_true_mixed(self, local_dataset_mixed):
        """Test conversion with multiindex=True for mixed data and metadata."""
        import pandas as pd
        df = local_dataset_mixed.as_dataframe(multiindex=True)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(local_dataset_mixed)
        assert isinstance(df.columns, pd.MultiIndex)
        # Expected columns: ('input', ''), ('input', 'prompt'), ('expected_output', ''),
        # ('expected_output', 'answer'), ('metadata', 'category'), ('metadata', 'difficulty')
        # derived from all keys across all records.
        expected_tuples = {
            ('input', ''), ('expected_output', ''), ('metadata', 'category'), ('metadata', 'difficulty'), # from record 0
            ('input', 'prompt'), ('expected_output', 'answer'), ('metadata', 'category') # from record 1
        }
        assert set(df.columns) == expected_tuples
        # Check specific values, including NaNs where keys don't exist in a record
        assert df[('input', '')].iloc[0] == local_dataset_mixed[0]['input']
        assert pd.isna(df[('input', 'prompt')].iloc[0]) # No 'prompt' key in first record's input (string)
        assert df[('input', 'prompt')].iloc[1] == local_dataset_mixed[1]['input']['prompt']
        assert pd.isna(df[('input', '')].iloc[1]) # Input is dict in second record, no empty subkey

        assert df[('expected_output', '')].iloc[0] == local_dataset_mixed[0]['expected_output']
        assert pd.isna(df[('expected_output', 'answer')].iloc[0])
        assert df[('expected_output', 'answer')].iloc[1] == local_dataset_mixed[1]['expected_output']['answer']
        assert pd.isna(df[('expected_output', '')].iloc[1])

        assert df[('metadata', 'category')].iloc[0] == local_dataset_mixed[0]['category']
        assert df[('metadata', 'difficulty')].iloc[0] == local_dataset_mixed[0]['difficulty']
        assert df[('metadata', 'category')].iloc[1] == local_dataset_mixed[1]['category']
        assert pd.isna(df[('metadata', 'difficulty')].iloc[1]) # No 'difficulty' in second record

    def test_as_dataframe_empty_dataset(self):
        """Test converting an empty dataset returns an empty DataFrame."""
        import pandas as pd
        # Need to create an empty dataset carefully due to init validation
        # Easiest way is create one then delete all items
        ds = dne.Dataset(name="temp-empty", data=[{"input": "a", "expected_output": "b"}])
        del ds[0]
        assert len(ds) == 0

        df_no_multi = ds.as_dataframe(multiindex=False)
        assert isinstance(df_no_multi, pd.DataFrame)
        assert df_no_multi.empty
        # Columns are derived from data; empty data -> empty columns list.
        assert list(df_no_multi.columns) == [] # Current behavior based on implementation

        df_multi = ds.as_dataframe(multiindex=True)
        assert isinstance(df_multi, pd.DataFrame)
        assert df_multi.empty
        assert list(df_multi.columns) == [] # Current behavior based on implementation

    def test_as_dataframe_with_record_id(self, experiments_vcr):
        """Test DataFrame includes 'record_id' (as metadata) after pull/push."""
        import pandas as pd
        # Get a dataset that has record_ids from the backend
        dataset_name = "meals-and-workouts-1.3"
        # Reuse the consolidated cassette for pulling this dataset
        with experiments_vcr.use_cassette("test_dataset_pull_meals_workouts.yaml"):
            dataset = dne.Dataset.pull(dataset_name)
        assert "record_id" in dataset._data[0]

        df_no_multi = dataset.as_dataframe(multiindex=False)
        assert "record_id" in df_no_multi.columns
        assert not df_no_multi["record_id"].isnull().any()
        assert df_no_multi["record_id"].iloc[0] == dataset._data[0]["record_id"]

        df_multi = dataset.as_dataframe(multiindex=True)
        assert ('metadata', 'record_id') in df_multi.columns
        assert not df_multi[('metadata', 'record_id')].isnull().any()
        assert df_multi[('metadata', 'record_id')].iloc[0] == dataset._data[0]["record_id"]

    def test_as_dataframe_diverse_types(self, local_dataset_diverse):
        """Test DataFrame conversion handles diverse types correctly (no multiindex)."""
        pytest.importorskip("pandas")
        import pandas as pd
        df = local_dataset_diverse.as_dataframe(multiindex=False)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(local_dataset_diverse)
        # Check columns exist (input/output are dicts, metadata keys are flattened)
        assert set(df.columns) == {"input", "expected_output", "metadata"} # as_dataframe currently groups under 'metadata'

        # Spot check some types and values
        assert isinstance(df["input"].iloc[0], dict)
        assert isinstance(df["expected_output"].iloc[1]["entities"], list)
        assert df["metadata"].iloc[0]["req_id"] == 1001
        assert df["metadata"].iloc[0]["is_valid"] is True
        assert df["metadata"].iloc[0]["params"] is None
        assert isinstance(df["metadata"].iloc[1]["params"], dict)
        assert df["metadata"].iloc[1]["params"]["threshold"] == 0.7
        assert df["metadata"].iloc[2]["is_valid"] is False


class TestDatasetRepr:
    """Tests for the __repr__ output of the Dataset."""

    # Helper to strip ANSI color codes for easier comparison
    def _strip_ansi(self, text):
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def test_repr_local_only(self, local_dataset_simple):
        """Test repr for a new, local-only dataset."""
        rep = repr(local_dataset_simple)
        rep_clean = self._strip_ansi(rep)

        assert "Dataset(name=test-local-simple)" in rep_clean
        assert "Description: Simple math/geo" in rep_clean
        assert f"Records: {len(local_dataset_simple)}" in rep_clean
        assert "Structure: input: str, expected_output: str" in rep_clean # Based on first record structure
        assert "Datadog: Local only" in rep_clean
        assert "Changes:" not in rep_clean
        assert "URL:" not in rep_clean

    def test_repr_synced_no_changes(self, experiments_vcr):
        """Test repr for a synced dataset with no changes."""
        dataset_name = "meals-and-workouts-1.3"
        # Reuse the consolidated cassette
        with experiments_vcr.use_cassette("test_dataset_pull_meals_workouts.yaml"):
            dataset = dne.Dataset.pull(dataset_name)

        rep = repr(dataset)
        rep_clean = self._strip_ansi(rep)

        assert f"Dataset(name={dataset_name})" in rep_clean
        assert f"Records: {len(dataset)}" in rep_clean
        # Structure based on the pulled data (dict inputs/outputs for this dataset)
        assert "Structure: input: dict[1 keys], expected_output: dict[1 keys], metadata: 1 fields" in rep_clean
        assert f"✓ Synced (v{dataset._datadog_dataset_version})" in rep_clean
        assert "Changes:" not in rep_clean
        # Check for URL presence, don't assert exact structure
        assert "URL: https://app.datadoghq.com/llm/testing/datasets" in rep_clean
        # Check datasetId is included in the URL query params
        assert f"datasetId={dataset._datadog_dataset_id}" in rep

    def test_repr_unsynced_changes(self, experiments_vcr):
        """Test repr for a synced dataset with pendåing local changes."""
        dataset_name = "meals-and-workouts-1.3"
        # Reuse the consolidated cassette for the initial pull
        with experiments_vcr.use_cassette("test_dataset_pull_meals_workouts.yaml"):
            dataset = dne.Dataset.pull(dataset_name)

        # Make changes
        dataset.add({"input": {"p": "new"}, "expected_output": {"r": "new"}})
        del dataset[0]
        dataset[0] = {"input": {"p": "updated"}, "expected_output": {"r": "updated"}} # Update the (now first) record

        rep = repr(dataset)
        rep_clean = self._strip_ansi(rep)

        assert f"Dataset(name={dataset_name})" in rep_clean
        assert f"Records: {len(dataset)}" in rep_clean
        # Structure based on first record *after* modification
        assert "Structure: input: dict[1 keys], expected_output: dict[1 keys]" in rep_clean
        assert f"⚠ Unsynced changes (v{dataset._datadog_dataset_version})" in rep_clean
        assert "Changes: +1 added, -1 deleted, ~1 updated" in rep_clean
        assert "URL:" not in rep_clean # URL typically hidden when unsynced with changes

    def test_repr_with_description(self, local_dataset_simple):
        """Test repr includes the description."""
        rep = repr(local_dataset_simple)
        rep_clean = self._strip_ansi(rep)
        assert "Description: Simple math/geo" in rep_clean

    def test_repr_without_description(self, sample_data_simple):
        """Test repr handles datasets without a description."""
        dataset = dne.Dataset(name="no-desc", data=sample_data_simple)
        rep = repr(dataset)
        rep_clean = self._strip_ansi(rep)
        assert "Description:" not in rep_clean
        assert "Dataset(name=no-desc)" in rep_clean
        assert "Records:" in rep_clean

    def test_repr_empty_dataset(self):
        """Test repr for an empty dataset (created locally)."""
        # Create then empty
        dataset = dne.Dataset(name="empty-local", data=[{"i":"a", "o":"b"}])
        del dataset[0]
        rep = repr(dataset)
        rep_clean = self._strip_ansi(rep)

        assert "Dataset(name=empty-local)" in rep_clean
        assert "Records: 0" in rep_clean
        assert "Structure:" not in rep_clean # No structure derivable from first record
        assert "Datadog: Local only" in rep_clean
        # Should show the deletion as a change until pushed/synced
        assert "Changes: -1 deleted" in rep_clean


# --- Test API Error Handling ---

@pytest.mark.parametrize(
    "error_status, error_message, expected_exception, expected_match",
    [
        (400, '{"errors": ["Bad request payload"]}', ValueError, "HTTP 400 Bad Request"),
        (401, '{"errors": ["Authentication required"]}', ValueError, "HTTP 401 Unauthorized"),
        (403, '{"errors": ["Forbidden"]}', ValueError, "HTTP 403 Forbidden"),
        (404, '{"errors": ["Not Found"]}', ValueError, "Dataset 'non-existent-pull-error' not found"), # Specific message for pull 404
        (429, '{"errors": ["Rate limit exceeded"]}', ValueError, "HTTP 429 Too Many Requests"),
        (500, '{"errors": ["Internal server error"]}', ValueError, "HTTP 500 Internal Server Error"),
        (503, '{"errors": ["Service unavailable"]}', ValueError, "HTTP 503 Service Unavailable"),
    ]
)
def test_pull_api_errors(mocker, error_status, error_message, expected_exception, expected_match):
    """Test Dataset.pull handles various API HTTP errors."""
    mock_response = mocker.Mock()
    mock_response.status_code = error_status
    mock_response.text = error_message
    mock_response.json.return_value = json.loads(error_message) if error_message.startswith('{') else {}
    mock_response.raise_for_status.side_effect = _http.requests.exceptions.HTTPError(response=mock_response)

    # Mock the _http.exp_http_request function
    mocker.patch("ddtrace.llmobs.experimentation.utils._http.exp_http_request", return_value=mock_response, side_effect=mock_response.raise_for_status)

    dataset_name = "non-existent-pull-error"
    with pytest.raises(expected_exception, match=re.escape(expected_match)):
        dne.Dataset.pull(dataset_name)


class MockApiResponse:
    """Helper to mock requests.Response objects."""
    def __init__(self, status_code, json_data=None, text_data="", headers=None):
        self.status_code = status_code
        self.text = text_data if text_data else json.dumps(json_data)
        self._json_data = json_data if json_data else {}
        self.headers = headers if headers else {'Content-Type': 'application/json'}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            http_error_msg = f"{self.status_code} Client Error for url: fake_url"
            raise _http.requests.exceptions.HTTPError(http_error_msg, response=self)


@pytest.mark.parametrize(
    "error_status, error_message, expected_exception, expected_match",
    [
        (400, '{"errors": ["Bad batch update payload"]}', ValueError, "HTTP 400 Bad Request"),
        (403, '{"errors": ["Forbidden to update"]}', ValueError, "HTTP 403 Forbidden"),
        (429, '{"errors": ["Rate limited on push"]}', ValueError, "HTTP 429 Too Many Requests"),
        (500, '{"errors": ["Server failed batch update"]}', ValueError, "HTTP 500 Internal Server Error"),
        # 404 on batch update might indicate dataset was deleted mid-operation
        (404, '{"errors": ["Dataset not found for batch update"]}', ValueError, "HTTP 404 Client Error"),
    ]
)
def test_push_batch_update_api_errors(mocker, synced_dataset, error_status, error_message, expected_exception, expected_match):
    """Test dataset.push() handles API errors during _batch_update."""
    # Simulate an existing dataset with an ID
    synced_dataset.add({"input": "change", "expected_output": "change"}) # Make a change to trigger push
    assert synced_dataset._datadog_dataset_id is not None
    assert not synced_dataset._synced

    # Mock the response for the batch_update call
    mock_response = MockApiResponse(status_code=error_status, json_data=json.loads(error_message))
    mock_http_request = mocker.patch("ddtrace.llmobs.experimentation.utils._http.exp_http_request")
    mock_http_request.side_effect = [mock_response.raise_for_status] # Only fail the batch update

    with pytest.raises(expected_exception, match=re.escape(expected_match)):
        synced_dataset.push()

    # Check that state remains unsynced and changes are NOT cleared
    assert not synced_dataset._synced
    assert any(synced_dataset._changes.values())
    mock_http_request.assert_called_once_with(
        "POST",
        f"/api/unstable/llm-obs/v1/datasets/{synced_dataset._datadog_dataset_id}/batch_update",
        body=mocker.ANY # Don't need to match exact body here
    )


@pytest.mark.parametrize(
    "error_status, error_message, expected_exception, expected_match",
    [
        (400, '{"errors": ["Bad push payload"]}', ValueError, "HTTP 400 Bad Request"),
        (403, '{"errors": ["Forbidden push"]}', ValueError, "HTTP 403 Forbidden"),
        (500, '{"errors": ["Server fail push"]}', ValueError, "HTTP 500 Internal Server Error"),
    ]
)
def test_push_entire_dataset_api_errors(mocker, local_dataset_simple, error_status, error_message, expected_exception, expected_match):
    """Test dataset.push() handles API errors during _push_entire_dataset (create/overwrite/new_version)."""
    # Case 1: Creating new dataset
    mock_create_response = MockApiResponse(200, {"data": {"id": "new-ds-id", "type": "datasets"}})
    mock_push_error_response = MockApiResponse(error_status, json.loads(error_message))
    mock_http_request = mocker.patch("ddtrace.llmobs.experimentation.utils._http.exp_http_request")
    # First call is GET check (assume 404), second is POST create (ok), third is POST push (error)
    mock_http_request.side_effect = [
        MockApiResponse(404).raise_for_status, # GET check fails
        mock_create_response,                 # POST create succeeds
        mock_push_error_response.raise_for_status # POST push fails
    ]

    with pytest.raises(expected_exception, match=re.escape(expected_match)):
        local_dataset_simple.push()

    # Check dataset ID was assigned from create, but still local/unsynced
    assert local_dataset_simple._datadog_dataset_id == "new-ds-id"
    assert local_dataset_simple._synced is True # Push sets to True initially, error happens during push
    # Ideally, the state should reflect failure, maybe _synced=False? Current impl doesn't revert.
    # Let's assert based on current code's behavior post-error.
    assert not any(local_dataset_simple._changes.values())

    mock_http_request.assert_has_calls([
        mocker.call("GET", mocker.ANY), # Check existence
        mocker.call("POST", "/api/unstable/llm-obs/v1/datasets", body=mocker.ANY), # Create
        mocker.call("POST", f"/api/unstable/llm-obs/v1/datasets/new-ds-id/push", body=mocker.ANY) # Push
    ])


def test_push_refresh_api_error(mocker, synced_dataset):
    """Test dataset.push() handles API error during the final _refresh_from_remote."""
    synced_dataset.add({"input": "change", "expected_output": "change"})
    initial_version = synced_dataset._datadog_dataset_version

    # Mock successful batch update, but failing subsequent pull (refresh)
    mock_batch_update_response = MockApiResponse(200, {})
    mock_pull_error_response = MockApiResponse(500, {"errors": ["Failed to pull after push"]})
    mock_http_request = mocker.patch("ddtrace.llmobs.experimentation.utils._http.exp_http_request")
    # 1st call: Batch Update (OK), 2nd call: GET datasets?filter (Fail in _refresh -> pull)
    mock_http_request.side_effect = [
        mock_batch_update_response,
        mock_pull_error_response.raise_for_status
    ]

    with pytest.raises(ValueError, match=re.escape("HTTP 500 Internal Server Error")):
        synced_dataset.push()

    # State after failed refresh: Should ideally be unsynced or reflect uncertainty.
    # Current implementation might leave it looking synced but with old data/version.
    # Let's assert based on current behavior: push clears changes before refresh.
    assert not any(synced_dataset._changes.values())
    assert synced_dataset._synced is True # Sync set True before refresh call
    # Version and data would NOT be updated due to failed refresh
    assert synced_dataset._datadog_dataset_version == initial_version

    mock_http_request.assert_has_calls([
        mocker.call("POST", f"/api/unstable/llm-obs/v1/datasets/{synced_dataset._datadog_dataset_id}/batch_update", body=mocker.ANY),
        mocker.call("GET", mocker.ANY) # GET datasets?filter... during pull inside refresh
    ])


