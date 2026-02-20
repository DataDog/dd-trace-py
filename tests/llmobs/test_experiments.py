"""
To run these tests, you need to set the following environment variables:

- RECORD_REQUESTS=1  # used to delay tests until data is ready from the backend
- DD_APP_KEY=...  # your datadog application key
- DD_API_KEY=...  # your datadog api key

and must have the test agent (>=1.27.0) running locally and configured to use the vcr cassette directory

eg. VCR_CASSETTES_DIRECTORY=tests/cassettes ddapm-test-agent ...
"""

import asyncio
import os
import re
import tempfile
import time
from typing import Generator
from typing import Optional
from unittest.mock import MagicMock
from uuid import UUID

import mock
import pytest

import ddtrace
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import _ExperimentRunInfo
from tests.utils import override_global_config


TMP_CSV_FILE = "tmp.csv"
TEST_PROJECT_NAME = os.environ.get("DD_LLMOBS_PROJECT_NAME", "test-project-clean")


def wait_for_backend(sleep_dur=2):
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(sleep_dur)


def dummy_task(input_data, config):
    return input_data


def faulty_task(input_data, config):
    raise ValueError("This is a test error")


def dummy_evaluator(input_data, output_data, expected_output):
    return int(output_data == expected_output)


def dummy_evaluator_with_extra_return_values(input_data, output_data, expected_output):
    return EvaluatorResult(
        value=expected_output == output_data,
        reasoning="it matches" if expected_output == output_data else "it doesn't match",
        assessment="pass" if expected_output == output_data else "fail",
        metadata={"difficulty": "easy"},
        tags={"task": "question_answering"},
    )


def faulty_evaluator(input_data, output_data, expected_output):
    raise ValueError("This is a test error in evaluator")


def faulty_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    raise ValueError("This is a test error in a summary evaluator")


def dummy_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    return len(inputs) + len(outputs) + len(expected_outputs) + len(evaluators_results["dummy_evaluator"])


def dummy_summary_evaluator_using_missing_eval_results(inputs, outputs, expected_outputs, evaluators_results):
    return len(inputs) + len(outputs) + len(expected_outputs) + len(evaluators_results["non_existent_evaluator"])


DUMMY_EXPERIMENT_FIRST_RUN_ID = UUID("12345678-abcd-abcd-abcd-123456789012")

# Timestamp in nanoseconds for mocked experiment runs.
# Must be within 24 hours of current time for server validation.
# To regenerate when re-recording cassettes: python3 -c "import time; print(time.time_ns())"
MOCK_TIMESTAMP_NS = 1771430149829292000


def run_info_with_stable_id(iteration: int, run_id: Optional[str] = None) -> _ExperimentRunInfo:
    eri = _ExperimentRunInfo(iteration)
    eri._id = "12345678-abcd-abcd-abcd-123456789012"
    if run_id is not None:
        eri._id = run_id
    return eri


def mock_async_process_record():
    """Context manager that mocks Experiment._process_record with stable IDs and timestamps."""
    from contextlib import contextmanager

    @contextmanager
    def _mock_context():
        with mock.patch("ddtrace.llmobs._experiment.Experiment._process_record") as mock_process_record:

            async def mock_process(*args, **kwargs):
                return {
                    "idx": 0,
                    "span_id": "123",
                    "trace_id": "456",
                    "timestamp": int(time.time()) * 1000000000,
                    "output": {"prompt": "What is the capital of France?"},
                    "metadata": {
                        "dataset_record_index": 0,
                        "experiment_name": "test_async_experiment",
                        "dataset_name": "test-dataset-123",
                    },
                    "error": {"message": None, "type": None, "stack": None},
                }

            mock_process_record.side_effect = mock_process
            with mock.patch("ddtrace.llmobs._experiment._ExperimentRunInfo") as mock_experiment_run_info:
                mock_experiment_run_info.return_value = run_info_with_stable_id(0)
                yield

    return _mock_context()


@pytest.fixture
def test_dataset_records() -> list[DatasetRecord]:
    return []


@pytest.fixture
def test_dataset_name(request) -> str:
    from tests.conftest import get_original_test_name

    return f"test-dataset-{get_original_test_name(request)}"


@pytest.fixture
def test_dataset(llmobs, test_dataset_records, test_dataset_name) -> Generator[Dataset, None, None]:
    ds = llmobs.create_dataset(
        dataset_name=test_dataset_name,
        description="A test dataset",
        records=test_dataset_records,
    )

    # When recording the requests, we need to wait for the dataset to be queryable.
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record(llmobs):
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
        )
    ]
    ds = llmobs.create_dataset(dataset_name="test-dataset-123", description="A test dataset", records=records)
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record_w_metadata(llmobs):
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
            metadata={"difficulty": "easy"},
        )
    ]
    ds = llmobs.create_dataset(dataset_name="test-dataset-123", description="A test dataset", records=records)
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record_separate_project(llmobs):
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of Massachusetts?"},
            expected_output={"answer": "Boston"},
        )
    ]
    ds = llmobs.create_dataset(
        dataset_name="test-dataset-857",
        project_name="boston-project",
        description="A boston dataset",
        records=records,
    )
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record_with_tags(llmobs):
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
            tags=["env:prod", "version:1.0"],
        )
    ]
    ds = llmobs.create_dataset(
        dataset_name="test-dataset-with-tags", description="A test dataset with tags", records=records
    )
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)



@pytest.fixture
def test_dataset_one_record_with_single_tag(llmobs):
    """Fixture that creates a dataset with a record containing a single tag."""
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of Germany?"},
            expected_output={"answer": "Berlin"},
            tags=["env:staging"],
        )
    ]
    ds = llmobs.create_dataset(
        dataset_name="test-dataset-single-tag", description="A test dataset with single tag", records=records
    )
    wait_for_backend()
    yield ds
    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record_separate_project_with_tags(llmobs):
    """Fixture that creates a dataset in a separate project with a record containing tags."""
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of Massachusetts?"},
            expected_output={"answer": "Boston"},
            tags=["team:ml", "priority:high"],
        )
    ]
    ds = llmobs.create_dataset(
        dataset_name="test-dataset-857-tags",
        project_name="boston-project",
        description="A boston dataset with tags",
        records=records,
    )
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)

# this fixture is needed because the tmp file call in _writer.py's dataset_bulk_upload
# will result in random names, causing a new POST request every time to the upload endpoint
# leading to repeated CI test failures
@pytest.fixture
def tmp_csv_file_for_upload(llmobs) -> Generator[MagicMock, None, None]:
    stable_dir = tempfile.gettempdir()
    stable_path = os.path.join(stable_dir, TMP_CSV_FILE)
    with open(stable_path, "w"):
        pass

    fake_tmp = MagicMock()
    fake_tmp.__enter__.return_value.name = stable_path

    yield fake_tmp

    if not os.path.exists(stable_path):
        os.remove(stable_path)


@pytest.fixture
def test_dataset_large_num_records(llmobs):
    records = []
    for i in range(3000):
        records.append({"input_data": f"input_{i}", "expected_output": f"output_{i}"})

    ds = llmobs.create_dataset(
        dataset_name="test-dataset-large-num-records",
        description="A test dataset with a large number of records",
        records=records,
    )
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


def test_dataset_create_delete(llmobs):
    dataset = llmobs.create_dataset(dataset_name="test-dataset-2", description="A second test dataset")
    assert dataset._id is not None
    assert dataset.url == f"https://app.datadoghq.com/llm/datasets/{dataset._id}"
    assert dataset.project.get("name") == TEST_PROJECT_NAME
    assert dataset.project.get("_id")

    llmobs._delete_dataset(dataset_id=dataset._id)


def test_dataset_create_delete_project_override(llmobs):
    dataset = llmobs.create_dataset(
        dataset_name="test-dataset-2",
        project_name="second project",
        description="A second test dataset",
    )
    assert dataset._id is not None
    assert dataset.url == f"https://app.datadoghq.com/llm/datasets/{dataset._id}"
    assert dataset.project.get("name") == "second project"
    assert dataset.project.get("_id")

    llmobs._delete_dataset(dataset_id=dataset._id)


def test_dataset_url_diff_site(llmobs, test_dataset_one_record):
    with override_global_config(dict(_dd_site="us3.datadoghq.com")):
        dataset = test_dataset_one_record
        assert dataset.url == f"https://us3.datadoghq.com/llm/datasets/{dataset._id}"


def test_dataset_url_diff_site_eu(llmobs, test_dataset_one_record):
    with override_global_config(dict(_dd_site="datadoghq.eu")):
        dataset = test_dataset_one_record
        assert dataset.url == f"https://app.datadoghq.eu/llm/datasets/{dataset._id}"


def test_dataset_as_dataframe(llmobs, test_dataset_one_record):
    dataset = test_dataset_one_record
    df = dataset.as_dataframe()
    assert len(df.columns) == 2
    assert df.size == 2  # size is num elements in a series


def test_csv_dataset_as_dataframe(llmobs, tmp_csv_file_for_upload):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None

    with mock.patch(
        "ddtrace.llmobs._writer.tempfile.NamedTemporaryFile",
        return_value=tmp_csv_file_for_upload,
    ):
        try:
            dataset = llmobs.create_dataset_from_csv(
                csv_path=csv_path,
                dataset_name="test-dataset-good-csv",
                description="A good csv dataset",
                input_data_columns=["in0", "in1", "in2"],
                expected_output_columns=["out0", "out1"],
                metadata_columns=["m0"],
            )
            dataset_id = dataset._id
            assert len(dataset) == 2

            df = dataset.as_dataframe()
            assert len(df.columns) == 6
            assert sorted(df.columns) == [
                ("expected_output", "out0"),
                ("expected_output", "out1"),
                ("input_data", "in0"),
                ("input_data", "in1"),
                ("input_data", "in2"),
                ("metadata", "m0"),
            ]
        finally:
            if dataset_id:
                llmobs._delete_dataset(dataset_id=dataset_id)


def test_dataset_csv_missing_input_col(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    with pytest.raises(
        ValueError,
        match=re.escape("Input columns not found in CSV header: ['in998', 'in999']"),
    ):
        llmobs.create_dataset_from_csv(
            csv_path=csv_path,
            dataset_name="test-dataset-good-csv",
            description="A good csv dataset",
            input_data_columns=["in998", "in999"],
            expected_output_columns=["out0", "out1"],
        )


def test_dataset_csv_missing_output_col(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    with pytest.raises(
        ValueError,
        match=re.escape("Expected output columns not found in CSV header: ['out999']"),
    ):
        llmobs.create_dataset_from_csv(
            csv_path=csv_path,
            dataset_name="test-dataset-good-csv",
            description="A good csv dataset",
            input_data_columns=["in0", "in1", "in2"],
            expected_output_columns=["out999"],
        )


def test_dataset_csv_empty_csv(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/empty.csv")
    with pytest.raises(
        ValueError,
        match=re.escape("CSV file appears to be empty or header is missing."),
    ):
        llmobs.create_dataset_from_csv(
            csv_path=csv_path,
            dataset_name="test-dataset-empty-csv",
            description="not a real csv dataset",
            input_data_columns=["in0", "in1", "in2"],
            expected_output_columns=["out0"],
        )


def test_dataset_csv_no_expected_output(llmobs, tmp_csv_file_for_upload):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None
    with mock.patch(
        "ddtrace.llmobs._writer.tempfile.NamedTemporaryFile",
        return_value=tmp_csv_file_for_upload,
    ):
        try:
            dataset = llmobs.create_dataset_from_csv(
                csv_path=csv_path,
                dataset_name="test-dataset-good-csv-without-expected-output",
                description="A good csv dataset without expected_output columns",
                input_data_columns=["in0", "in1", "in2"],
            )
            dataset_id = dataset._id
            assert len(dataset) == 2
            assert len(dataset[0]["input_data"]) == 3
            assert dataset[0]["input_data"]["in0"] == "r0v1"
            assert dataset[0]["input_data"]["in1"] == "r0v2"
            assert dataset[0]["input_data"]["in2"] == "r0v3"
            assert dataset[1]["input_data"]["in0"] == "r1v1"
            assert dataset[1]["input_data"]["in1"] == "r1v2"
            assert dataset[1]["input_data"]["in2"] == "r1v3"

            assert len(dataset[0]["expected_output"]) == 0

            assert dataset.description == "A good csv dataset without expected_output columns"

            assert dataset._id is not None

            wait_for_backend(4)
            ds = llmobs.pull_dataset(dataset_name=dataset.name)

            assert len(ds) == len(dataset)
            assert ds.name == dataset.name
            assert ds.description == dataset.description
            assert ds.latest_version == 1
            assert ds.latest_version == ds.version
        finally:
            if dataset_id:
                llmobs._delete_dataset(dataset_id=dataset_id)


def test_dataset_csv(llmobs, tmp_csv_file_for_upload):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None
    with mock.patch(
        "ddtrace.llmobs._writer.tempfile.NamedTemporaryFile",
        return_value=tmp_csv_file_for_upload,
    ):
        try:
            dataset = llmobs.create_dataset_from_csv(
                csv_path=csv_path,
                dataset_name="test-dataset-good-csv-1",
                description="A good csv dataset",
                input_data_columns=["in0", "in1", "in2"],
                expected_output_columns=["out0", "out1"],
            )
            assert dataset.project.get("name") == TEST_PROJECT_NAME
            assert dataset.project.get("_id")
            dataset_id = dataset._id
            assert len(dataset) == 2
            assert len(dataset[0]["input_data"]) == 3
            assert dataset[0]["input_data"]["in0"] == "r0v1"
            assert dataset[0]["input_data"]["in1"] == "r0v2"
            assert dataset[0]["input_data"]["in2"] == "r0v3"
            assert dataset[1]["input_data"]["in0"] == "r1v1"
            assert dataset[1]["input_data"]["in1"] == "r1v2"
            assert dataset[1]["input_data"]["in2"] == "r1v3"

            assert len(dataset[0]["expected_output"]) == 2
            assert dataset[0]["expected_output"]["out0"] == "r0v4"
            assert dataset[0]["expected_output"]["out1"] == "r0v5"
            assert dataset[1]["expected_output"]["out0"] == "r1v4"
            assert dataset[1]["expected_output"]["out1"] == "r1v5"

            assert dataset.description == "A good csv dataset"

            assert dataset._id is not None

            wait_for_backend()
            ds = llmobs.pull_dataset(dataset_name=dataset.name)

            assert len(ds) == len(dataset)
            assert ds.name == dataset.name
            assert ds.description == dataset.description
            assert ds.latest_version == 1
            assert ds.latest_version == ds.version
        finally:
            if dataset_id:
                llmobs._delete_dataset(dataset_id=dataset_id)


def test_dataset_csv_pipe_separated(llmobs, tmp_csv_file_for_upload):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset_pipe_separated.csv")
    dataset_id = None
    with mock.patch(
        "ddtrace.llmobs._writer.tempfile.NamedTemporaryFile",
        return_value=tmp_csv_file_for_upload,
    ):
        try:
            dataset = llmobs.create_dataset_from_csv(
                csv_path=csv_path,
                dataset_name="test-dataset-good-csv-pipe",
                description="A good pipe separated csv dataset",
                input_data_columns=["in0", "in1", "in2"],
                expected_output_columns=["out0", "out1"],
                metadata_columns=["m0"],
                csv_delimiter="|",
            )
            assert dataset.project.get("name") == TEST_PROJECT_NAME
            assert dataset.project.get("_id")
            dataset_id = dataset._id
            assert len(dataset) == 2
            assert len(dataset[0]["input_data"]) == 3
            assert dataset[0]["input_data"]["in0"] == "r0v1"
            assert dataset[0]["input_data"]["in1"] == "r0v2"
            assert dataset[0]["input_data"]["in2"] == "r0v3"
            assert dataset[1]["input_data"]["in0"] == "r1v1"
            assert dataset[1]["input_data"]["in1"] == "r1v2"
            assert dataset[1]["input_data"]["in2"] == "r1v3"

            assert len(dataset[0]["expected_output"]) == 2
            assert dataset[0]["expected_output"]["out0"] == "r0v4"
            assert dataset[0]["expected_output"]["out1"] == "r0v5"
            assert dataset[1]["expected_output"]["out0"] == "r1v4"
            assert dataset[1]["expected_output"]["out1"] == "r1v5"

            assert len(dataset[0]["metadata"]) == 1
            assert dataset[0]["metadata"]["m0"] == "r0v6"
            assert dataset[1]["metadata"]["m0"] == "r1v6"

            assert dataset.description == "A good pipe separated csv dataset"

            assert dataset._id is not None

            wait_for_backend()
            ds = llmobs.pull_dataset(dataset_name=dataset.name)

            assert len(ds) == len(dataset)
            assert ds.name == dataset.name
            assert ds.description == dataset.description
            assert ds.latest_version == 1
            assert ds.latest_version == ds.version
        finally:
            if dataset_id:
                llmobs._delete_dataset(dataset_id=dataset._id)


def test_dataset_pull_non_existent(llmobs):
    with pytest.raises(ValueError):
        llmobs.pull_dataset(dataset_name="test-dataset-non-existent")


def test_dataset_pull_non_existent_project(llmobs):
    with pytest.raises(ValueError):
        llmobs.pull_dataset(dataset_name="test-dataset-non-existent", project_name="some project")


def test_dataset_pull_large_num_records(llmobs, test_dataset_large_num_records):
    pds = llmobs.pull_dataset(dataset_name=test_dataset_large_num_records.name)
    assert pds.project.get("name") == TEST_PROJECT_NAME
    assert pds.project.get("_id")
    assert len(pds) == len(test_dataset_large_num_records)
    assert pds.name == test_dataset_large_num_records.name
    assert pds.description == test_dataset_large_num_records.description
    assert pds.latest_version == test_dataset_large_num_records.latest_version == 1
    assert pds.version == test_dataset_large_num_records.version == 1

    dataset = sorted(pds, key=lambda r: int(r["input_data"].lstrip("input_")))
    for i, d in enumerate(dataset):
        assert d["input_data"] == f"input_{i}"
        assert d["expected_output"] == f"output_{i}"


@pytest.mark.parametrize("test_dataset_records", [[]])
def test_dataset_pull_exists_but_no_records(llmobs, test_dataset, test_dataset_records, test_dataset_name):
    dataset = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert dataset.project.get("name") == TEST_PROJECT_NAME
    assert dataset.project.get("_id")
    assert dataset._id is not None
    assert len(dataset) == 0


def test_dataset_pull_exists_with_record(llmobs):
    name = "test-dataset-one-rec"
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
        )
    ]
    ds = llmobs.create_dataset(dataset_name=name, description="A test dataset", records=records)
    wait_for_backend(10)

    dataset = llmobs.pull_dataset(dataset_name=name)
    assert dataset.project.get("name") == TEST_PROJECT_NAME
    assert dataset.project.get("_id")
    assert len(dataset) == 1
    assert dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert dataset[0]["expected_output"] == {"answer": "Paris"}
    assert dataset.name == name
    assert dataset.latest_version == 1
    assert dataset.version == 1
    assert dataset[0]["record_id"] != ""
    assert dataset[0]["canonical_id"]

    llmobs._delete_dataset(dataset_id=ds._id)

# def test_dataset_pull_with_tags(llmobs, test_dataset_one_record_with_tags):
#     """Test that pull_dataset properly passes tags parameter and filters records by tags."""
#     tags = ["env:prod", "version:1.0"]
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_tags.name, tags=tags)
#
#     # Verify basic dataset properties
#     assert dataset.project.get("name") == "test-project"
#     assert dataset.project.get("_id")
#     assert len(dataset) == 1
#     assert dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
#     assert dataset[0]["expected_output"] == {"answer": "Paris"}
#     assert dataset.name == test_dataset_one_record_with_tags.name
#     assert dataset.description == test_dataset_one_record_with_tags.description
#     assert dataset.latest_version == test_dataset_one_record_with_tags.latest_version == 1
#     assert dataset.version == test_dataset_one_record_with_tags.version == 1
#
#     # Verify the record has the expected tags (order-independent comparison)
#     assert set(dataset[0]["tags"]) == set(tags)
#     # Verify filter_tags are stored in the Dataset object (order-independent comparison)
#     assert set(dataset.filter_tags) == set(tags)
#     assert len(dataset.filter_tags) == 2
#     assert "env:prod" in dataset.filter_tags
#     assert "version:1.0" in dataset.filter_tags
#
#
# def test_dataset_pull_with_nonexistent_tags(llmobs, test_dataset_one_record_with_tags):
#     """Test pull_dataset with tags that don't exist on any records returns empty dataset."""
#     # The dataset has tags ["env:prod", "version:1.0"], but we're filtering for non-existent tags
#     tags = ["env:nonexistent", "version:99.0"]
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_tags.name, tags=tags)
#
#     # Verify dataset properties
#     assert dataset.project.get("name") == "test-project"
#     assert dataset.project.get("_id")
#     assert dataset.name == test_dataset_one_record_with_tags.name
#     # Should return 0 records since no records match the non-existent tags
#     assert len(dataset) == 0
#     # Verify filter_tags are stored even when no records match (order-independent)
#     assert set(dataset.filter_tags) == set(tags)
#
#
# def test_dataset_pull_with_partial_tag_match(llmobs, test_dataset_one_record_with_tags):
#     """Test pull_dataset with a subset of tags returns records that have those tags."""
#     # The dataset has tags ["env:prod", "version:1.0"], filter with just one of them
#     tags = ["env:prod"]
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_tags.name, tags=tags)
#
#     # Verify basic dataset properties
#     assert dataset.project.get("name") == "test-project"
#     assert dataset.project.get("_id")
#     assert len(dataset) == 1
#     assert dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
#     assert dataset[0]["expected_output"] == {"answer": "Paris"}
#
#     # Record should have all its original tags, not just the filtered ones
#     assert "env:prod" in dataset[0]["tags"]
#     assert "version:1.0" in dataset[0]["tags"]
#     # Verify filter_tags only contains the requested filter
#     assert dataset.filter_tags == tags
#     assert len(dataset.filter_tags) == 1
#
#
# def test_dataset_pull_with_one_matching_one_nonexistent_tag(llmobs, test_dataset_one_record_with_tags):
#     """Test pull_dataset with one matching tag and one non-existent tag."""
#     # The dataset has tags ["env:prod", "version:1.0"], filter with one matching and one not
#     tags = ["env:prod", "nonexistent:tag"]
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_tags.name, tags=tags)
#
#     # Behavior depends on backend: AND vs OR logic for tags
#     # If AND logic: should return 0 records (record doesn't have "nonexistent:tag")
#     # If OR logic: should return 1 record (record has "env:prod")
#     # Verify filter_tags are stored (order-independent)
#     assert set(dataset.filter_tags) == set(tags)
#     assert dataset.project.get("name") == "test-project"
#
#
# def test_dataset_pull_without_tags_returns_all_records(llmobs, test_dataset_one_record_with_tags):
#     """Test pull_dataset without tags parameter returns all records regardless of their tags."""
#     # Pull without specifying tags
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_tags.name)
#
#     # Verify all records are returned
#     assert dataset.project.get("name") == "test-project"
#     assert dataset.project.get("_id")
#     assert len(dataset) == 1
#     assert dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
#     assert dataset[0]["expected_output"] == {"answer": "Paris"}
#     # Record should still have its tags
#     assert "env:prod" in dataset[0]["tags"]
#     assert "version:1.0" in dataset[0]["tags"]
#     # filter_tags should be None or empty when not filtering
#     assert dataset.filter_tags is None or dataset.filter_tags == []
#
#
# def test_dataset_pull_with_single_tag(llmobs, test_dataset_one_record_with_single_tag):
#     """Test pull_dataset with a single tag."""
#     tags = ["env:staging"]
#     dataset = llmobs.pull_dataset(dataset_name=test_dataset_one_record_with_single_tag.name, tags=tags)
#
#     # Verify basic dataset properties
#     assert dataset.project.get("name") == "test-project"
#     assert dataset.project.get("_id")
#     assert len(dataset) == 1
#     assert dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
#     assert dataset[0]["expected_output"] == {"answer": "Berlin"}
#
#     # Verify the record has the expected tag (order-independent comparison)
#     assert set(dataset[0]["tags"]) == set(tags)
#     # Verify single filter_tag is stored (order-independent comparison)
#     assert set(dataset.filter_tags) == set(tags)
#     assert len(dataset.filter_tags) == 1
#     assert "env:staging" in dataset.filter_tags
#
#
# def test_dataset_pull_with_tags_and_project(llmobs, test_dataset_one_record_separate_project_with_tags):
#     """Test pull_dataset with tags and custom project name."""
#     wait_for_backend()
#     tags = ["team:ml", "priority:high"]
#     dataset = llmobs.pull_dataset(
#         dataset_name=test_dataset_one_record_separate_project_with_tags.name,
#         project_name="boston-project",
#         tags=tags,
#     )
#
#     # Verify dataset is from the correct project
#     assert dataset.project.get("name") == "boston-project"
#     assert dataset.project.get("_id")
#     assert len(dataset) == 1
#     assert dataset[0]["input_data"] == {"prompt": "What is the capital of Massachusetts?"}
#     assert dataset[0]["expected_output"] == {"answer": "Boston"}
#     assert dataset.name == test_dataset_one_record_separate_project_with_tags.name
#     assert dataset.description == test_dataset_one_record_separate_project_with_tags.description
#     assert dataset.latest_version == test_dataset_one_record_separate_project_with_tags.latest_version == 1
#     assert dataset.version == test_dataset_one_record_separate_project_with_tags.version == 1
#
#     # Verify the record has the expected tags (order-independent comparison)
#     assert set(dataset[0]["tags"]) == set(tags)
#     # Verify filter_tags are stored (order-independent comparison)
#     assert set(dataset.filter_tags) == set(tags)
#     assert len(dataset.filter_tags) == 2
#     assert "team:ml" in dataset.filter_tags
#     assert "priority:high" in dataset.filter_tags

@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_pull_w_versions(llmobs, test_dataset, test_dataset_records):
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    test_dataset.append(
        {
            "input_data": {"prompt": "What is the capital of China?"},
            "expected_output": {"answer": "Beijing"},
        }
    )
    test_dataset.push()
    wait_for_backend(4)

    dataset_v2 = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert len(dataset_v2) == 2
    assert dataset_v2[1]["input_data"] == {"prompt": "What is the capital of France?"}
    assert dataset_v2[1]["expected_output"] == {"answer": "Paris"}
    assert dataset_v2[0]["input_data"] == {"prompt": "What is the capital of China?"}
    assert dataset_v2[0]["expected_output"] == {"answer": "Beijing"}
    assert dataset_v2.name == test_dataset.name
    assert dataset_v2.description == test_dataset.description
    assert dataset_v2.latest_version == test_dataset.latest_version == 2
    assert dataset_v2.version == test_dataset.version == 2

    dataset_v1 = llmobs.pull_dataset(dataset_name=test_dataset.name, version=1)
    assert len(dataset_v1) == 1
    assert dataset_v1[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert dataset_v1[0]["expected_output"] == {"answer": "Paris"}
    assert dataset_v1.name == test_dataset.name
    assert dataset_v1.description == test_dataset.description
    assert dataset_v1.latest_version == test_dataset.latest_version == 2
    assert dataset_v1.version == 1


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_pull_w_invalid_version(llmobs, test_dataset, test_dataset_records):
    with pytest.raises(
        ValueError,
        match="Failed to pull dataset records for.*version is greater than the current version or negative",
    ):
        llmobs.pull_dataset(dataset_name=test_dataset.name, version=420)


def test_dataset_pull_from_project(llmobs, test_dataset_one_record_separate_project):
    dataset = llmobs.pull_dataset(
        dataset_name=test_dataset_one_record_separate_project.name,
        project_name="boston-project",
    )
    assert dataset.project.get("name") == "boston-project"
    assert dataset.project.get("_id")
    assert len(dataset) == 1
    assert dataset[0]["input_data"] == {"prompt": "What is the capital of Massachusetts?"}
    assert dataset[0]["expected_output"] == {"answer": "Boston"}
    assert dataset.name == test_dataset_one_record_separate_project.name
    assert dataset.description == test_dataset_one_record_separate_project.description
    assert dataset.latest_version == test_dataset_one_record_separate_project.latest_version == 1
    assert dataset.version == test_dataset_one_record_separate_project.version == 1


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of China?"},
                expected_output={"answer": "Beijing"},
            ),
        ]
    ],
)
def test_dataset_modify_records_multiple_times(llmobs, test_dataset, test_dataset_records):
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    test_dataset.update(
        0,
        DatasetRecord(input_data={"prompt": "What is the capital of Germany?"}),
    )

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert "metadata" not in test_dataset[0]
    assert test_dataset[0]["record_id"] != ""

    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of China?"}
    assert test_dataset[1]["expected_output"] == {"answer": "Beijing"}
    assert "metadata" not in test_dataset[1]
    assert test_dataset[1]["record_id"] != ""

    test_dataset.update(0, {"expected_output": {"answer": "Berlin"}})
    test_dataset.update(
        1,
        {
            "input_data": {"prompt": "What is the capital of Mexico?"},
            "metadata": {"difficulty": "easy"},
        },
    )
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]
    assert test_dataset[0]["record_id"] != ""

    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Mexico?"}
    assert test_dataset[1]["expected_output"] == {"answer": "Beijing"}
    assert test_dataset[1]["metadata"] == {"difficulty": "easy"}
    assert test_dataset[1]["record_id"] != ""

    test_dataset.update(1, {"expected_output": None})
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Mexico?"}
    assert test_dataset[1]["expected_output"] is None
    assert test_dataset[1]["metadata"] == {"difficulty": "easy"}
    assert test_dataset[1]["record_id"] != ""

    test_dataset.push()
    assert len(test_dataset) == 2
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]
    assert test_dataset[0]["record_id"] != ""

    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Mexico?"}
    assert test_dataset[1]["expected_output"] is None
    assert test_dataset[1]["metadata"] == {"difficulty": "easy"}
    assert test_dataset[1]["record_id"] != ""

    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    sds = sorted(ds, key=lambda r: r["input_data"]["prompt"])
    assert sds[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert sds[0]["expected_output"] == {"answer": "Berlin"}
    assert sds[0]["metadata"] == {}

    assert sds[1]["input_data"] == {"prompt": "What is the capital of Mexico?"}
    assert sds[1]["expected_output"] == ""
    assert sds[1]["metadata"] == {"difficulty": "easy"}
    assert len(ds) == 2
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds.latest_version == 2
    assert ds.version == 2


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_modify_single_record(llmobs, test_dataset, test_dataset_records):
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    test_dataset.update(
        0,
        DatasetRecord(
            input_data={"prompt": "What is the capital of Germany?"},
            expected_output={"answer": "Berlin"},
        ),
    )
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert ds[0]["expected_output"] == {"answer": "Berlin"}
    assert len(ds) == 1
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds.latest_version == 2
    assert ds.version == 2


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_modify_single_record_empty_record(llmobs, test_dataset, test_dataset_records):
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    with pytest.raises(
        ValueError,
        match="invalid update, record should contain at least one of "
        "input_data, expected_output, or metadata to update",
    ):
        test_dataset.update(0, {})


def test_dataset_estimate_size(llmobs, test_dataset):
    test_dataset.append(
        {
            "input_data": {"prompt": "What is the capital of France?"},
            "expected_output": {"answer": "Paris"},
        }
    )
    assert 200 <= test_dataset._estimate_delta_size() <= 220


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_modify_record_on_optional(llmobs, test_dataset, test_dataset_records):
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    test_dataset.update(0, {"expected_output": None})
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] is None
    assert "metadata" not in test_dataset[0]

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] is None
    assert "metadata" not in test_dataset[0]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert ds[0]["expected_output"] == ""
    assert len(ds) == 1
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds.latest_version == 2
    assert ds.version == 2


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
                metadata={"difficulty": "easy"},
            )
        ]
    ],
)
def test_dataset_modify_record_on_input(llmobs, test_dataset, test_dataset_records):
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1
    test_dataset.update(0, {"input_data": "A"})
    assert test_dataset[0]["input_data"] == "A"
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[0]["metadata"] == {"difficulty": "easy"}

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    assert test_dataset[0]["input_data"] == "A"
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[0]["metadata"] == {"difficulty": "easy"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    wait_for_backend(4)

    # assert that the version is consistent with a new pull

    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds[0]["input_data"] == "A"
    assert ds[0]["expected_output"] == {"answer": "Paris"}
    assert ds[0]["metadata"] == {"difficulty": "easy"}
    assert len(ds) == 1
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds.latest_version == 2
    assert ds.version == 2


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_append(llmobs, test_dataset):
    test_dataset.append(
        DatasetRecord(
            input_data={"prompt": "What is the capital of Italy?"},
            expected_output={"answer": "Rome"},
        )
    )
    assert len(test_dataset) == 2
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[1]["expected_output"] == {"answer": "Rome"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 2
    # note: it looks like dataset order is not deterministic
    assert ds[1]["input_data"] == {"prompt": "What is the capital of France?"}
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_extend(llmobs, test_dataset):
    test_dataset.extend(
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of Italy?"},
                expected_output={"answer": "Rome"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Sweden?"},
                expected_output={"answer": "Stockholm"},
            ),
        ]
    )
    assert len(test_dataset) == 3
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert len(test_dataset) == 3
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[1]["expected_output"] == {"answer": "Rome"}
    assert test_dataset[2]["input_data"] == {"prompt": "What is the capital of Sweden?"}
    assert test_dataset[2]["expected_output"] == {"answer": "Stockholm"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 3
    # order is non deterministic
    input_data_set = {r["input_data"]["prompt"] for r in ds}
    assert input_data_set == {
        "What is the capital of France?",
        "What is the capital of Italy?",
        "What is the capital of Sweden?",
    }
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            )
        ]
    ],
)
def test_dataset_append_no_expected_output(llmobs, test_dataset):
    test_dataset.append(DatasetRecord(input_data={"prompt": "What is the capital of Sealand?"}))
    assert len(test_dataset) == 2
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Sealand?"}
    assert "expected_output" not in test_dataset[1]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 2
    # note: it looks like dataset order is not deterministic
    assert ds[1]["input_data"] == {"prompt": "What is the capital of France?"}
    assert ds[1]["expected_output"] == {"answer": "Paris"}
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Sealand?"}
    assert ds[0]["expected_output"] is None
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Italy?"},
                expected_output={"answer": "Rome"},
            ),
        ],
    ],
)
def test_dataset_delete(llmobs, test_dataset):
    test_dataset.delete(0)
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert ds[0]["expected_output"] == {"answer": "Rome"}


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(input_data={"prompt": "What is the capital of Nauru?"}),
            DatasetRecord(input_data={"prompt": "What is the capital of Sealand?"}),
        ],
    ],
)
def test_dataset_delete_no_expected_output(llmobs, test_dataset):
    test_dataset.delete(1)
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Nauru?"}
    assert "expected_output" not in test_dataset[0]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Nauru?"}
    assert ds[0]["expected_output"] is None


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Italy?"},
                expected_output={"answer": "Rome"},
            ),
        ],
    ],
)
def test_dataset_delete_after_update(llmobs, test_dataset):
    test_dataset.update(0, {"input_data": "A"})
    assert test_dataset[0]["input_data"] == "A"
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert "metadata" not in test_dataset[0]
    assert len(test_dataset) == 2

    test_dataset.delete(0)
    assert len(test_dataset) == 1
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert ds[0]["expected_output"] == {"answer": "Rome"}


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Italy?"},
                expected_output={"answer": "Rome"},
            ),
        ],
    ],
)
def test_dataset_delete_after_append(llmobs, test_dataset):
    test_dataset.append({"input_data": "A", "expected_output": 1})
    test_dataset.append({"input_data": "B", "expected_output": 2})
    test_dataset.append({"input_data": {"prompt": "What is the capital of Sweden?"}})

    test_dataset.delete(2)
    test_dataset.delete(2)
    test_dataset.delete(0)
    # all that remains should be Italy and Sweden questions
    assert len(test_dataset) == 2
    assert test_dataset.latest_version == 1
    assert test_dataset.version == 1

    assert len(test_dataset._new_records_by_record_id) == 1
    assert len(test_dataset._deleted_record_ids) == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset.latest_version == 2
    assert test_dataset.version == 2
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Sweden?"}
    assert "expected_output" not in test_dataset[1]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(dataset_name=test_dataset.name)
    assert ds.latest_version == 2
    assert ds.version == 2
    assert len(ds) == 2
    sds = sorted(ds, key=lambda r: r["input_data"]["prompt"])
    assert sds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert sds[0]["expected_output"] == {"answer": "Rome"}
    assert sds[1]["input_data"] == {"prompt": "What is the capital of Sweden?"}
    assert sds[1]["expected_output"] is None


def test_project_create_new_project(llmobs):
    project = llmobs._instance._dne_client.project_create_or_get(name="test-project-dne-sdk")
    assert project.get("_id") == "905824bc-ccec-4444-a48d-401931d5065b"
    assert project.get("name") == "test-project-dne-sdk"


def test_project_get_existing_project(llmobs):
    project = llmobs._instance._dne_client.project_create_or_get(name=TEST_PROJECT_NAME)
    assert project.get("_id")
    assert project.get("name") == TEST_PROJECT_NAME


def test_experiment_invalid_task_type_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="task must be a callable function."):
        llmobs.experiment("test_experiment", 123, test_dataset_one_record, [dummy_evaluator])


def test_experiment_invalid_task_signature_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="Task function must have 'input_data' and 'config' parameters."):

        def my_task(not_input):
            pass

        llmobs.experiment("test_experiment", my_task, test_dataset_one_record, [dummy_evaluator])
    with pytest.raises(TypeError, match="Task function must have 'input_data' and 'config' parameters."):

        def my_task(input_data, not_config):
            pass

        llmobs.experiment("test_experiment", my_task, test_dataset_one_record, [dummy_evaluator])


def test_experiment_invalid_dataset_raises(llmobs):
    with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset object."):
        llmobs.experiment("test_experiment", dummy_task, 123, [dummy_evaluator])


def test_experiment_invalid_evaluators_type_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="Evaluators must be a list of callable functions or BaseEvaluator instances."):
        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [])
    with pytest.raises(TypeError, match="Evaluator 123 must be callable or an instance of BaseEvaluator."):
        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [123])


def test_experiment_invalid_evaluator_signature_raises(llmobs, test_dataset_one_record):
    expected_err = "Evaluator function must have parameters ('input_data', 'output_data', 'expected_output')."
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_expected_output(input_data, output_data):
            pass

        llmobs.experiment(
            "test_experiment",
            dummy_task,
            test_dataset_one_record,
            [my_evaluator_missing_expected_output],
        )
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_input(output_data, expected_output):
            pass

        llmobs.experiment(
            "test_experiment",
            dummy_task,
            test_dataset_one_record,
            [my_evaluator_missing_input],
        )
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_output(input_data, expected_output):
            pass

        llmobs.experiment(
            "test_experiment",
            dummy_task,
            test_dataset_one_record,
            [my_evaluator_missing_output],
        )


def test_project_name_set(run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath), "DD_TRACE_ENABLED": "0"})
    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="ml-app", project_name="test-project-123")
assert LLMObs._project_name == "test-project-123"
""",
        env=env,
    )
    assert status == 0, err


def test_project_name_set_env(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            "DD_TRACE_ENABLED": "0",
            "DD_LLMOBS_PROJECT_NAME": "test-project-123",
            "DD_LLMOBS_ENABLED": "1",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.llmobs import LLMObs

assert LLMObs._project_name == "test-project-123"
""",
        env=env,
    )
    assert status == 0, err


def test_project_name_not_set_env(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            "DD_TRACE_ENABLED": "0",
            "DD_LLMOBS_ENABLED": "1",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import DEFAULT_PROJECT_NAME

assert LLMObs._project_name == DEFAULT_PROJECT_NAME
""",
        env=env,
    )
    assert status == 0, err


def test_experiment_init(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        description="lorem ipsum",
    )
    assert exp._experiment.name == "test_experiment"
    assert exp._experiment._task == dummy_task
    assert exp._experiment._dataset == test_dataset_one_record
    assert exp._experiment._evaluators == [dummy_evaluator]
    assert exp._experiment._project_name == TEST_PROJECT_NAME
    assert exp._experiment._description == "lorem ipsum"
    assert exp._experiment._project_id is None
    assert exp._experiment._run_name is None
    assert exp._experiment._id is None


def test_experiment_create(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        description="This is a test experiment",
        tags={"tag1": "value1", "tag2": "value2"},
        config={"models": ["gpt-4.1"]},
    )
    project = llmobs._instance._dne_client.project_create_or_get(TEST_PROJECT_NAME)
    project_id = project.get("_id")
    exp_id, exp_run_name = llmobs._instance._dne_client.experiment_create(
        exp._experiment.name,
        exp._experiment._dataset._id,
        project_id,
        exp._experiment._dataset.latest_version,
        exp._experiment._config,
    )
    assert exp_id is not None
    assert exp_run_name.startswith("test_experiment")


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Canada?"},
                expected_output={"answer": "Ottawa"},
            ),
        ]
    ],
)
def test_experiment_run_task(llmobs, test_dataset, test_dataset_records):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset,
        [dummy_evaluator],
        config={"models": ["gpt-4.1"]},
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 2
    assert task_results[0] == {
        "idx": 0,
        "span_id": mock.ANY,
        "trace_id": mock.ANY,
        "timestamp": mock.ANY,
        "output": {"prompt": "What is the capital of France?"},
        "metadata": {
            "dataset_record_index": 0,
            "experiment_name": "test_experiment",
            "dataset_name": "test-dataset-test_experiment_run_task[test_dataset_records0]",
        },
        "error": mock.ANY,
    }
    assert task_results[1] == {
        "idx": 1,
        "span_id": mock.ANY,
        "trace_id": mock.ANY,
        "timestamp": mock.ANY,
        "output": {"prompt": "What is the capital of Canada?"},
        "metadata": {
            "dataset_record_index": 1,
            "experiment_name": "test_experiment",
            "dataset_name": "test-dataset-test_experiment_run_task[test_dataset_records0]",
        },
        "error": mock.ANY,
    }


def test_experiment_run_task_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", faulty_task, test_dataset_one_record, [dummy_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    assert task_results == [
        {
            "idx": 0,
            "span_id": mock.ANY,
            "trace_id": mock.ANY,
            "timestamp": mock.ANY,
            "output": None,
            "metadata": {
                "dataset_record_index": 0,
                "experiment_name": "test_experiment",
                "dataset_name": "test-dataset-123",
            },
            "error": mock.ANY,
        }
    ]
    assert task_results[0]["error"]["message"] == "This is a test error"
    assert task_results[0]["error"]["stack"] is not None
    assert task_results[0]["error"]["type"] == "builtins.ValueError"


def test_experiment_run_task_error_raises(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", faulty_task, test_dataset_one_record, [dummy_evaluator])
    with pytest.raises(
        RuntimeError,
        match=re.compile(
            "Error on record 0: This is a test error\n.*ValueError.*in faulty_task.*",
            flags=re.DOTALL,
        ),
    ):
        asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=True))


def test_experiment_run_evaluators(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_evaluator": {"value": False, "error": None}},
    }


def test_experiment_run_evaluators_with_extra_return_values(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator_with_extra_return_values]
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {
            "dummy_evaluator_with_extra_return_values": dict(
                value=False,
                error=None,
                reasoning="it doesn't match",
                assessment="fail",
                metadata={"difficulty": "easy"},
                tags={"task": "question_answering"},
            )
        },
    }


def test_experiment_run_summary_evaluators(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        summary_evaluators=[dummy_summary_evaluator],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_evaluator": {"value": False, "error": None}},
    }
    summary_eval_results = asyncio.run(
        exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    )
    assert len(summary_eval_results) == 1
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_summary_evaluator": {"value": 4, "error": None}},
    }


def test_experiment_run_evaluators_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"faulty_evaluator": {"value": None, "error": mock.ANY}},
    }
    err = eval_results[0]["evaluations"]["faulty_evaluator"]["error"]
    assert err["message"] == "This is a test error in evaluator"
    assert err["type"] == "ValueError"
    assert err["stack"] is not None


def test_experiment_run_summary_evaluators_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        summary_evaluators=[faulty_summary_evaluator],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_evaluator": {"value": False, "error": None}},
    }
    summary_eval_results = asyncio.run(
        exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    )
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {"faulty_summary_evaluator": {"value": None, "error": mock.ANY}},
    }
    err = summary_eval_results[0]["evaluations"]["faulty_summary_evaluator"]["error"]
    assert err["message"] == "This is a test error in a summary evaluator"
    assert err["type"] == "ValueError"
    assert err["stack"] is not None


def test_experiment_summary_evaluators_missing_eval_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        summary_evaluators=[dummy_summary_evaluator_using_missing_eval_results],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_evaluator": {"value": False, "error": None}},
    }
    summary_eval_results = asyncio.run(
        exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    )
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {
            "dummy_summary_evaluator_using_missing_eval_results": {
                "value": None,
                "error": mock.ANY,
            }
        },
    }
    err = summary_eval_results[0]["evaluations"]["dummy_summary_evaluator_using_missing_eval_results"]["error"]
    assert err["message"] == "'non_existent_evaluator'"
    assert err["type"] == "KeyError"
    assert err["stack"] is not None


def test_experiment_run_evaluators_error_raises(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    with pytest.raises(RuntimeError, match="Evaluator faulty_evaluator failed on row 0"):
        asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=True))


def test_experiment_run_summary_evaluators_error_raises(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        summary_evaluators=[faulty_summary_evaluator],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    with pytest.raises(RuntimeError, match="Summary evaluator faulty_summary_evaluator failed"):
        asyncio.run(exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=True))


def test_experiment_summary_eval_missing_results_raises(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        summary_evaluators=[dummy_summary_evaluator_using_missing_eval_results],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(task_results) == 1
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    with pytest.raises(
        RuntimeError,
        match="Summary evaluator dummy_summary_evaluator_using_missing_eval_results failed",
    ):
        asyncio.run(exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=True))


def test_experiment_merge_results(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    merged_results = exp._experiment._merge_results(run_info_with_stable_id(0), task_results, eval_results, None)

    assert len(merged_results.rows) == 1
    assert merged_results.run_iteration == 1
    assert merged_results.run_id is not None
    exp_result = merged_results.rows[0]
    assert exp_result["idx"] == 0
    assert exp_result["record_id"] != ""
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp_result["timestamp"] == mock.ANY
    assert exp_result["span_id"] == mock.ANY
    assert exp_result["trace_id"] == mock.ANY
    assert exp_result["evaluations"] == {"dummy_evaluator": {"value": False, "error": None}}
    assert exp_result["metadata"] == {
        "dataset_record_index": 0,
        "experiment_name": "test_experiment",
        "dataset_name": "test-dataset-123",
        "tags": mock.ANY,
    }
    assert exp_result["metadata"]["tags"][0].startswith("ddtrace.version:")
    assert exp_result["error"] == {"message": None, "type": None, "stack": None}


def test_experiment_merge_err_results(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
    merged_results = exp._experiment._merge_results(run_info_with_stable_id(0), task_results, eval_results, None)

    assert len(merged_results.rows) == 1
    assert merged_results.run_iteration == 1
    assert merged_results.run_id is not None
    exp_result = merged_results.rows[0]
    assert exp_result["idx"] == 0
    assert exp_result["record_id"] != ""
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp_result["timestamp"] == mock.ANY
    assert exp_result["span_id"] == mock.ANY
    assert exp_result["trace_id"] == mock.ANY
    assert exp_result["evaluations"] == {"faulty_evaluator": {"value": None, "error": mock.ANY}}
    assert exp_result["evaluations"]["faulty_evaluator"]["error"] == {
        "message": "This is a test error in evaluator",
        "type": "ValueError",
        "stack": mock.ANY,
    }
    assert exp_result["metadata"] == {
        "dataset_record_index": 0,
        "experiment_name": "test_experiment",
        "dataset_name": "test-dataset-123",
        "tags": mock.ANY,
    }
    assert exp_result["metadata"]["tags"][0].startswith("ddtrace.version:")
    assert exp_result["error"] == {"message": None, "type": None, "stack": None}


def test_experiment_run(llmobs, test_dataset_one_record):
    with mock.patch("ddtrace.llmobs._experiment.Experiment._process_record") as mock_process_record:
        # This is to ensure that the eval event post request contains the same span/trace IDs and timestamp.
        mock_process_record.return_value = {
            "idx": 0,
            "span_id": "123",
            "trace_id": "456",
            "timestamp": int(time.time()) * 1000000000,
            "output": {"prompt": "What is the capital of France?"},
            "metadata": {
                "dataset_record_index": 0,
                "experiment_name": "test_experiment",
                "dataset_name": "test-dataset-123",
            },
            "error": {"message": None, "type": None, "stack": None},
        }
        with mock.patch("ddtrace.llmobs._experiment._ExperimentRunInfo") as mock_experiment_run_info:
            # this is to ensure that the UUID for the run is always the same
            mock_experiment_run_info.return_value = run_info_with_stable_id(0)
            exp = llmobs.experiment(
                "test_experiment",
                dummy_task,
                test_dataset_one_record,
                [dummy_evaluator],
            )
            exp._experiment._tags = {
                "ddtrace.version": "1.2.3"
            }  # FIXME: this is a hack to set the tags for the experiment
            exp_results = exp.run()

    assert len(exp_results.get("summary_evaluations")) == 0
    assert len(exp_results.get("rows")) == 1
    assert len(exp_results.get("runs")) == 1
    assert len(exp_results.get("runs")[0].summary_evaluations) == 0
    assert len(exp_results.get("runs")[0].rows) == 1
    exp_result = exp_results.get("rows")[0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp.url == f"https://app.datadoghq.com/llm/experiments/{exp._experiment._id}"

    project = llmobs._instance._dne_client.project_create_or_get(name=TEST_PROJECT_NAME)
    assert project.get("_id")
    assert project.get("name") == TEST_PROJECT_NAME
    assert exp._experiment._project_id == project.get("_id")
    assert exp._experiment._project_name == project.get("name")


def test_experiment_run_w_different_project(llmobs, test_dataset_one_record):
    with mock.patch("ddtrace.llmobs._experiment.Experiment._process_record") as mock_process_record:
        # This is to ensure that the eval event post request contains the same span/trace IDs and timestamp.
        mock_process_record.return_value = {
            "idx": 0,
            "span_id": "123",
            "trace_id": "456",
            "timestamp": int(time.time()) * 1000000000,
            "output": {"prompt": "What is the capital of France?"},
            "metadata": {
                "dataset_record_index": 0,
                "experiment_name": "test_experiment",
                "dataset_name": "test-dataset-123",
            },
            "error": {"message": None, "type": None, "stack": None},
        }
        with mock.patch("ddtrace.llmobs._experiment._ExperimentRunInfo") as mock_experiment_run_info:
            # this is to ensure that the UUID for the run is always the same
            mock_experiment_run_info.return_value = run_info_with_stable_id(0)
            exp = llmobs.experiment(
                "test_experiment",
                dummy_task,
                test_dataset_one_record,
                [dummy_evaluator],
                project_name="new-different-project",
            )
            exp._experiment._tags = {
                "ddtrace.version": "1.2.3"
            }  # FIXME: this is a hack to set the tags for the experiment
            exp_results = exp.run()

    assert len(exp_results["summary_evaluations"]) == 0
    assert len(exp_results["rows"]) == 1
    exp_result = exp_results["rows"][0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp.url == f"https://app.datadoghq.com/llm/experiments/{exp._experiment._id}"

    project = llmobs._instance._dne_client.project_create_or_get(name="new-different-project")
    assert project.get("_id") == "c4b49fb5-7b16-46e1-86f0-de5800e8a56c"
    assert project.get("name") == "new-different-project"
    assert exp._experiment._project_id == project.get("_id")
    assert exp._experiment._project_name == project.get("name")


def test_experiment_run_w_summary(llmobs, test_dataset_one_record):
    with mock.patch("ddtrace.llmobs._experiment.Experiment._process_record") as mock_process_record:
        # This is to ensure that the eval event post request contains the same span/trace IDs and timestamp.
        mock_process_record.return_value = {
            "idx": 0,
            "span_id": "123",
            "trace_id": "456",
            "timestamp": int(time.time()) * 1000000000,
            "output": {"prompt": "What is the capital of France?"},
            "metadata": {
                "dataset_record_index": 0,
                "experiment_name": "test_experiment",
                "dataset_name": "test-dataset-123",
            },
            "error": {"message": None, "type": None, "stack": None},
        }
        with mock.patch("ddtrace.llmobs._experiment._ExperimentRunInfo") as mock_experiment_run_info:
            # this is to ensure that the UUID for the run is always the same
            mock_experiment_run_info.return_value = run_info_with_stable_id(0)
            exp = llmobs.experiment(
                "test_experiment",
                dummy_task,
                test_dataset_one_record,
                [dummy_evaluator],
                summary_evaluators=[dummy_summary_evaluator],
            )
            exp._experiment._tags = {
                "ddtrace.version": "1.2.3"
            }  # FIXME: this is a hack to set the tags for the experiment
            exp_results = exp.run()

    assert len(exp_results["summary_evaluations"]) == 1
    summary_eval = exp_results["summary_evaluations"]["dummy_summary_evaluator"]
    assert summary_eval["value"] == 4
    assert summary_eval["error"] is None
    assert len(exp_results["rows"]) == 1
    exp_result = exp_results["rows"][0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp.url == f"https://app.datadoghq.com/llm/experiments/{exp._experiment._id}"


def test_experiment_span_written_to_experiment_scope(llmobs, llmobs_events, test_dataset_one_record_w_metadata):
    test_dataset_one_record_w_metadata._records[0]["canonical_id"] = "some-id"
    """Assert that the experiment span includes expected output field and includes the experiment scope."""
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record_w_metadata,
        [dummy_evaluator],
    )
    exp._experiment._id = "1234567890"
    asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["name"] == "dummy_task"
    for key in ("span_id", "trace_id", "parent_id", "start_ns", "duration", "metrics"):
        assert event[key] == mock.ANY
    assert event["status"] == "ok"
    assert event["meta"]["input"] == {"prompt": "What is the capital of France?"}
    assert event["meta"]["output"] == {"prompt": "What is the capital of France?"}
    assert event["meta"]["expected_output"] == {"answer": "Paris"}
    assert event["meta"]["metadata"] == {"difficulty": "easy"}
    assert "dataset_name:{}".format(test_dataset_one_record_w_metadata.name) in event["tags"]
    assert f"project_name:{TEST_PROJECT_NAME}" in event["tags"]
    assert "experiment_name:test_experiment" in event["tags"]
    assert "dataset_id:{}".format(test_dataset_one_record_w_metadata._id) in event["tags"]
    assert "dataset_record_id:{}".format(test_dataset_one_record_w_metadata._records[0]["record_id"]) in event["tags"]
    assert (
        "dataset_record_canonical_id:{}".format(test_dataset_one_record_w_metadata._records[0]["canonical_id"])
        in event["tags"]
    )
    assert "experiment_id:1234567890" in event["tags"]
    assert f"run_id:{DUMMY_EXPERIMENT_FIRST_RUN_ID}" in event["tags"]
    assert "run_iteration:1" in event["tags"]
    assert f"ddtrace.version:{ddtrace.__version__}" in event["tags"]
    assert event["_dd"]["scope"] == "experiments"


def test_experiment_span_multi_run_tags(llmobs, llmobs_events, test_dataset_one_record_w_metadata):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record_w_metadata,
        [dummy_evaluator],
    )
    exp._experiment._id = "1234567890"
    for i in range(2):
        asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(i), raise_errors=False))
        assert len(llmobs_events) == i + 1
        event = llmobs_events[i]
        assert event["name"] == "dummy_task"
        for key in (
            "span_id",
            "trace_id",
            "parent_id",
            "start_ns",
            "duration",
            "metrics",
        ):
            assert event[key] == mock.ANY
        assert event["status"] == "ok"
        assert event["meta"]["input"] == {"prompt": "What is the capital of France?"}
        assert event["meta"]["output"] == {"prompt": "What is the capital of France?"}
        assert event["meta"]["expected_output"] == {"answer": "Paris"}
        assert event["meta"]["metadata"] == {"difficulty": "easy"}
        assert "dataset_name:{}".format(test_dataset_one_record_w_metadata.name) in event["tags"]
        assert f"project_name:{TEST_PROJECT_NAME}" in event["tags"]
        assert "experiment_name:test_experiment" in event["tags"]
        assert "dataset_id:{}".format(test_dataset_one_record_w_metadata._id) in event["tags"]
        assert (
            "dataset_record_id:{}".format(test_dataset_one_record_w_metadata._records[0]["record_id"]) in event["tags"]
        )
        assert "experiment_id:1234567890" in event["tags"]
        assert f"run_id:{DUMMY_EXPERIMENT_FIRST_RUN_ID}" in event["tags"]
        assert f"run_iteration:{i + 1}" in event["tags"]
        assert f"ddtrace.version:{ddtrace.__version__}" in event["tags"]
        assert event["_dd"]["scope"] == "experiments"


def test_evaluators_run_with_jobs_parameter(llmobs, test_dataset_one_record):
    """Test that evaluators can run with jobs > 1."""

    def eval_1(input_data, output_data, expected_output):
        return 1

    def eval_2(input_data, output_data, expected_output):
        return 2

    def eval_3(input_data, output_data, expected_output):
        return 3

    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [eval_1, eval_2, eval_3],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))

    # Run with jobs=3 - should complete successfully
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False, jobs=3))

    assert len(eval_results) == 1
    assert eval_results[0]["evaluations"]["eval_1"]["value"] == 1
    assert eval_results[0]["evaluations"]["eval_1"]["error"] is None
    assert eval_results[0]["evaluations"]["eval_2"]["value"] == 2
    assert eval_results[0]["evaluations"]["eval_2"]["error"] is None
    assert eval_results[0]["evaluations"]["eval_3"]["value"] == 3
    assert eval_results[0]["evaluations"]["eval_3"]["error"] is None


def test_evaluators_with_errors_concurrent(llmobs, test_dataset_one_record):
    """Test that errors in one evaluator don't block others when running concurrently."""

    def failing_evaluator(input_data, output_data, expected_output):
        raise ValueError("Test error")

    def successful_evaluator(input_data, output_data, expected_output):
        return 100

    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [failing_evaluator, successful_evaluator],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))

    # Run with jobs=2 - failing evaluator shouldn't block the successful one
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False, jobs=2))

    assert len(eval_results) == 1
    evals_dict = eval_results[0]["evaluations"]

    # Failing evaluator has error
    assert evals_dict["failing_evaluator"]["error"] is not None
    assert "Test error" in evals_dict["failing_evaluator"]["error"]["message"]
    assert evals_dict["failing_evaluator"]["value"] is None

    # Successful evaluator completed
    assert evals_dict["successful_evaluator"]["value"] == 100
    assert evals_dict["successful_evaluator"]["error"] is None


def test_summary_evaluators_run_concurrently(llmobs, test_dataset_one_record):
    """Test that summary evaluators can run with jobs > 1."""

    def summary_eval_1(inputs, outputs, expected_outputs, evaluators_results):
        return 10

    def summary_eval_2(inputs, outputs, expected_outputs, evaluators_results):
        return 20

    def summary_eval_3(inputs, outputs, expected_outputs, evaluators_results):
        return 30

    def simple_evaluator(input_data, output_data, expected_output):
        return 1

    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [simple_evaluator],
        summary_evaluators=[summary_eval_1, summary_eval_2, summary_eval_3],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False, jobs=1))

    # Run with jobs=3 - should complete successfully
    summary_eval_results = asyncio.run(
        exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False, jobs=3)
    )

    # Verify all summary evaluators completed
    assert len(summary_eval_results) == 3
    summary_evals_dict = {}
    for eval_result in summary_eval_results:
        summary_evals_dict.update(eval_result["evaluations"])

    assert summary_evals_dict["summary_eval_1"]["value"] == 10
    assert summary_evals_dict["summary_eval_1"]["error"] is None
    assert summary_evals_dict["summary_eval_2"]["value"] == 20
    assert summary_evals_dict["summary_eval_2"]["error"] is None
    assert summary_evals_dict["summary_eval_3"]["value"] == 30
    assert summary_evals_dict["summary_eval_3"]["error"] is None


def test_summary_evaluators_with_errors_concurrent(llmobs, test_dataset_one_record):
    """Test that errors in one summary evaluator don't block others when running concurrently."""

    def failing_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
        raise ValueError("Test error")

    def successful_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
        return 100

    def simple_evaluator(input_data, output_data, expected_output):
        return 1

    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [simple_evaluator],
        summary_evaluators=[failing_summary_evaluator, successful_summary_evaluator],
    )
    task_results = asyncio.run(exp._experiment._run_task(1, run=run_info_with_stable_id(0), raise_errors=False))
    eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False, jobs=1))

    # Run with jobs=2 - failing evaluator shouldn't block the successful one
    summary_eval_results = asyncio.run(
        exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False, jobs=2)
    )

    assert len(summary_eval_results) == 2
    summary_evals_dict = {}
    for eval_result in summary_eval_results:
        summary_evals_dict.update(eval_result["evaluations"])

    # Failing evaluator has error
    assert summary_evals_dict["failing_summary_evaluator"]["error"] is not None
    assert "Test error" in summary_evals_dict["failing_summary_evaluator"]["error"]["message"]
    assert summary_evals_dict["failing_summary_evaluator"]["value"] is None

    # Successful evaluator completed
    assert summary_evals_dict["successful_summary_evaluator"]["value"] == 100
    assert summary_evals_dict["successful_summary_evaluator"]["error"] is None


# =============================================================================
# AsyncExperiment Tests
# =============================================================================


async def async_dummy_task(input_data, config):
    """Async version of dummy_task."""
    return input_data


async def async_faulty_task(input_data, config):
    """Async version of faulty_task."""
    raise ValueError("This is an async test error")


async def async_dummy_evaluator(input_data, output_data, expected_output):
    """Async version of dummy_evaluator."""
    return int(output_data == expected_output)


async def async_faulty_evaluator(input_data, output_data, expected_output):
    """Async faulty evaluator."""
    raise ValueError("This is an async test error in evaluator")


async def async_dummy_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    """Async version of dummy_summary_evaluator."""
    return len(inputs) + len(outputs) + len(expected_outputs) + len(evaluators_results.get("async_dummy_evaluator", []))


async def async_faulty_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    """Async faulty summary evaluator."""
    raise ValueError("This is an async test error in a summary evaluator")


# --- Factory method validation tests ---


def test_async_experiment_invalid_task_not_async_raises(llmobs, test_dataset_one_record):
    """Test that async_experiment raises TypeError if task is not async."""
    with pytest.raises(TypeError, match="task must be an async function"):
        llmobs.async_experiment("test_experiment", dummy_task, test_dataset_one_record, [async_dummy_evaluator])


def test_async_experiment_invalid_task_type_raises(llmobs, test_dataset_one_record):
    """Test that async_experiment raises TypeError if task is not callable."""
    with pytest.raises(TypeError, match="task must be a callable function."):
        llmobs.async_experiment("test_experiment", 123, test_dataset_one_record, [async_dummy_evaluator])


def test_async_experiment_invalid_task_signature_raises(llmobs, test_dataset_one_record):
    """Test that async_experiment raises TypeError if task has wrong signature."""
    with pytest.raises(TypeError, match="Task function must have 'input_data' and 'config' parameters."):

        async def my_async_task(not_input):
            pass

        llmobs.async_experiment("test_experiment", my_async_task, test_dataset_one_record, [async_dummy_evaluator])


def test_async_experiment_invalid_dataset_raises(llmobs):
    """Test that async_experiment raises TypeError if dataset is not a Dataset."""
    with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset object."):
        llmobs.async_experiment("test_experiment", async_dummy_task, 123, [async_dummy_evaluator])


def test_async_experiment_invalid_evaluators_type_raises(llmobs, test_dataset_one_record):
    """Test that async_experiment raises TypeError if evaluators is empty or invalid."""
    with pytest.raises(
        TypeError, match="Evaluators must be a list of callable functions, BaseEvaluator, or BaseAsyncEvaluator"
    ):
        llmobs.async_experiment("test_experiment", async_dummy_task, test_dataset_one_record, [])
    with pytest.raises(TypeError, match="Evaluator 123 must be callable or an instance of BaseEvaluator"):
        llmobs.async_experiment("test_experiment", async_dummy_task, test_dataset_one_record, [123])


def test_async_experiment_invalid_evaluator_signature_raises(llmobs, test_dataset_one_record):
    """Test that async_experiment raises TypeError if evaluator has wrong signature."""
    # Use a pattern that matches regardless of parameter order (since it's derived from a set)
    expected_err = "Evaluator function must have parameters ('input_data', 'output_data', 'expected_output')."
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        async def my_async_evaluator_missing_expected_output(input_data, output_data):
            pass

        llmobs.async_experiment(
            "test_experiment",
            async_dummy_task,
            test_dataset_one_record,
            [my_async_evaluator_missing_expected_output],
        )


def test_async_experiment_accepts_sync_evaluators(llmobs, test_dataset_one_record):
    """Test that async_experiment accepts sync evaluators."""
    # Should not raise - sync evaluators are allowed in async experiments
    exp = llmobs.async_experiment(
        "test_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],  # sync evaluator
    )
    assert exp.name == "test_experiment"


def test_async_experiment_accepts_mixed_evaluators(llmobs, test_dataset_one_record):
    """Test that async_experiment accepts both sync and async evaluators."""
    exp = llmobs.async_experiment(
        "test_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [dummy_evaluator, async_dummy_evaluator],  # mixed
    )
    assert exp.name == "test_experiment"
    assert len(exp._evaluators) == 2


# --- AsyncExperiment init tests ---


def test_async_experiment_init(llmobs, test_dataset_one_record):
    """Test AsyncExperiment initialization."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [async_dummy_evaluator],
        description="async lorem ipsum",
    )
    assert exp.name == "test_async_experiment"
    assert exp._task == async_dummy_task
    assert exp._dataset == test_dataset_one_record
    assert exp._evaluators == [async_dummy_evaluator]
    assert exp._project_name == "test-project-clean"
    assert exp._description == "async lorem ipsum"
    assert exp._project_id is None
    assert exp._run_name is None
    assert exp._id is None


# --- AsyncExperiment _run_task tests ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(
                input_data={"prompt": "What is the capital of France?"},
                expected_output={"answer": "Paris"},
            ),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Canada?"},
                expected_output={"answer": "Ottawa"},
            ),
        ]
    ],
)
async def test_async_experiment_run_task(llmobs, test_dataset, test_dataset_records):
    """Test AsyncExperiment._run_task with async task."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset,
        [async_dummy_evaluator],
        config={"models": ["gpt-4.1"]},
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 2
    # Results may be in any order due to async execution
    outputs = [r["output"] for r in task_results]
    assert {"prompt": "What is the capital of France?"} in outputs
    assert {"prompt": "What is the capital of Canada?"} in outputs


@pytest.mark.asyncio
async def test_async_experiment_run_task_error(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_task with async task that raises."""
    exp = llmobs.async_experiment(
        "test_async_experiment", async_faulty_task, test_dataset_one_record, [async_dummy_evaluator]
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    assert task_results[0]["output"] is None
    assert task_results[0]["error"]["message"] == "This is an async test error"
    assert task_results[0]["error"]["type"] == "builtins.ValueError"
    assert task_results[0]["error"]["stack"] is not None


@pytest.mark.asyncio
async def test_async_experiment_run_task_error_raises(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_task with raise_errors=True."""
    exp = llmobs.async_experiment(
        "test_async_experiment", async_faulty_task, test_dataset_one_record, [async_dummy_evaluator]
    )
    with pytest.raises(
        RuntimeError,
        match=re.compile(
            "Error on record 0: This is an async test error\n.*ValueError.*",
            flags=re.DOTALL,
        ),
    ):
        await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=True)


# --- AsyncExperiment _run_evaluators tests ---


@pytest.mark.asyncio
async def test_async_experiment_run_evaluators_async(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_evaluators with async evaluator."""
    exp = llmobs.async_experiment(
        "test_async_experiment", async_dummy_task, test_dataset_one_record, [async_dummy_evaluator]
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"async_dummy_evaluator": {"value": False, "error": None}},
    }


@pytest.mark.asyncio
async def test_async_experiment_run_evaluators_sync(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_evaluators with sync evaluator (run via to_thread)."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],  # sync evaluator
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_evaluator": {"value": False, "error": None}},
    }


@pytest.mark.asyncio
async def test_async_experiment_run_evaluators_mixed(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_evaluators with mixed sync and async evaluators."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [dummy_evaluator, async_dummy_evaluator],  # mixed
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert "dummy_evaluator" in eval_results[0]["evaluations"]
    assert "async_dummy_evaluator" in eval_results[0]["evaluations"]
    assert eval_results[0]["evaluations"]["dummy_evaluator"]["value"] == 0
    assert eval_results[0]["evaluations"]["async_dummy_evaluator"]["value"] == 0


@pytest.mark.asyncio
async def test_async_experiment_run_evaluators_error(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_evaluators with async faulty evaluator."""
    exp = llmobs.async_experiment(
        "test_async_experiment", async_dummy_task, test_dataset_one_record, [async_faulty_evaluator]
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {
        "idx": 0,
        "evaluations": {"async_faulty_evaluator": {"value": None, "error": mock.ANY}},
    }
    err = eval_results[0]["evaluations"]["async_faulty_evaluator"]["error"]
    assert err["message"] == "This is an async test error in evaluator"
    assert err["type"] == "ValueError"
    assert err["stack"] is not None


@pytest.mark.asyncio
async def test_async_experiment_run_evaluators_error_raises(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_evaluators with raise_errors=True."""
    exp = llmobs.async_experiment(
        "test_async_experiment", async_dummy_task, test_dataset_one_record, [async_faulty_evaluator]
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    with pytest.raises(RuntimeError, match="Evaluator async_faulty_evaluator failed on row 0"):
        await exp._run_evaluators(task_results, raise_errors=True)


# --- AsyncExperiment _run_summary_evaluators tests ---


@pytest.mark.asyncio
async def test_async_experiment_run_summary_evaluators_async(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_summary_evaluators with async summary evaluator."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [async_dummy_evaluator],
        summary_evaluators=[async_dummy_summary_evaluator],
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    summary_eval_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    assert len(summary_eval_results) == 1
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {"async_dummy_summary_evaluator": {"value": 4, "error": None}},
    }


@pytest.mark.asyncio
async def test_async_experiment_run_summary_evaluators_sync(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_summary_evaluators with sync summary evaluator."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],  # sync evaluator
        summary_evaluators=[dummy_summary_evaluator],  # sync summary evaluator
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    summary_eval_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    assert len(summary_eval_results) == 1
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {"dummy_summary_evaluator": {"value": 4, "error": None}},
    }


@pytest.mark.asyncio
async def test_async_experiment_run_summary_evaluators_error(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_summary_evaluators with async faulty summary evaluator."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [async_dummy_evaluator],
        summary_evaluators=[async_faulty_summary_evaluator],
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    assert len(task_results) == 1
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    summary_eval_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)
    assert summary_eval_results[0] == {
        "idx": 0,
        "evaluations": {"async_faulty_summary_evaluator": {"value": None, "error": mock.ANY}},
    }
    err = summary_eval_results[0]["evaluations"]["async_faulty_summary_evaluator"]["error"]
    assert err["message"] == "This is an async test error in a summary evaluator"
    assert err["type"] == "ValueError"
    assert err["stack"] is not None


@pytest.mark.asyncio
async def test_async_experiment_run_summary_evaluators_error_raises(llmobs, test_dataset_one_record):
    """Test AsyncExperiment._run_summary_evaluators with raise_errors=True."""
    exp = llmobs.async_experiment(
        "test_async_experiment",
        async_dummy_task,
        test_dataset_one_record,
        [async_dummy_evaluator],
        summary_evaluators=[async_faulty_summary_evaluator],
    )
    task_results = await exp._run_task(10, run=run_info_with_stable_id(0), raise_errors=False)
    eval_results = await exp._run_evaluators(task_results, raise_errors=False)
    with pytest.raises(RuntimeError, match="Summary evaluator async_faulty_summary_evaluator failed"):
        await exp._run_summary_evaluators(task_results, eval_results, raise_errors=True)


# --- AsyncExperiment.run() integration tests ---


@pytest.mark.asyncio
async def test_async_experiment_run(llmobs, test_dataset_one_record):
    """Test AsyncExperiment.run() integration."""
    with mock_async_process_record():
        exp = llmobs.async_experiment(
            "test_async_experiment",
            async_dummy_task,
            test_dataset_one_record,
            [async_dummy_evaluator],
        )
        exp._tags = {"ddtrace.version": "1.2.3"}
        exp_results = await exp.run()

    assert len(exp_results.get("summary_evaluations")) == 0
    assert len(exp_results.get("rows")) == 1
    assert len(exp_results.get("runs")) == 1
    assert len(exp_results.get("runs")[0].summary_evaluations) == 0
    assert len(exp_results.get("runs")[0].rows) == 1
    exp_result = exp_results.get("rows")[0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp.url == f"https://app.datadoghq.com/llm/experiments/{exp._id}"


@pytest.mark.asyncio
async def test_async_experiment_run_with_summary(llmobs, test_dataset_one_record):
    """Test AsyncExperiment.run() with summary evaluators."""
    with mock_async_process_record():
        exp = llmobs.async_experiment(
            "test_async_experiment",
            async_dummy_task,
            test_dataset_one_record,
            [async_dummy_evaluator],
            summary_evaluators=[async_dummy_summary_evaluator],
        )
        exp._tags = {"ddtrace.version": "1.2.3"}
        exp_results = await exp.run()

    assert len(exp_results["summary_evaluations"]) == 1
    summary_eval = exp_results["summary_evaluations"]["async_dummy_summary_evaluator"]
    assert summary_eval["value"] == 4
    assert summary_eval["error"] is None
    assert len(exp_results["rows"]) == 1
    exp_result = exp_results["rows"][0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}


@pytest.mark.asyncio
async def test_async_experiment_run_with_mixed_evaluators(llmobs, test_dataset_one_record):
    """Test AsyncExperiment.run() with mixed sync and async evaluators."""
    with mock_async_process_record():
        exp = llmobs.async_experiment(
            "test_async_experiment",
            async_dummy_task,
            test_dataset_one_record,
            [dummy_evaluator, async_dummy_evaluator],  # mixed
            summary_evaluators=[dummy_summary_evaluator, async_dummy_summary_evaluator],  # mixed
        )
        exp._tags = {"ddtrace.version": "1.2.3"}
        exp_results = await exp.run()

    assert len(exp_results["rows"]) == 1
    exp_result = exp_results["rows"][0]
    # Both sync and async evaluators should have run
    assert "dummy_evaluator" in exp_result["evaluations"]
    assert "async_dummy_evaluator" in exp_result["evaluations"]
    # Both sync and async summary evaluators should have run
    assert "dummy_summary_evaluator" in exp_results["summary_evaluations"]
    assert "async_dummy_summary_evaluator" in exp_results["summary_evaluations"]
