"""
To run these tests, you need to set the following environment variables:

- RECORD_REQUESTS=1  # used to delay tests until data is ready from the backend
- DD_APP_KEY=...  # your datadog application key
- DD_API_KEY=...  # your datadog api key

and must have the test agent (>=1.27.0) running locally and configured to use the vcr cassette directory

eg. VCR_CASSETTES_DIRECTORY=tests/cassettes ddapm-test-agent ...
"""

import os
import re
import time
from typing import Generator
from typing import List

import pytest

from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord


def dummy_task(input_data):
    return input_data


def dummy_evaluator(input_data, output_data, expected_output):
    return output_data == expected_output


@pytest.fixture
def test_dataset_records() -> List[DatasetRecord]:
    return []


@pytest.fixture
def test_dataset_name(request) -> str:
    return f"test-dataset-{request.node.name}"


@pytest.fixture
def test_dataset(llmobs, test_dataset_records, test_dataset_name) -> Generator[Dataset, None, None]:
    ds = llmobs.create_dataset(name=test_dataset_name, description="A test dataset", records=test_dataset_records)

    # When recording the requests, we need to wait for the dataset to be queryable.
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(2)

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record(llmobs):
    records = [
        DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})
    ]
    ds = llmobs.create_dataset(name="test-dataset-123", description="A test dataset", records=records)
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(2)

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


def test_dataset_create_delete(llmobs):
    dataset = llmobs.create_dataset(name="test-dataset-2", description="A second test dataset")
    assert dataset._id is not None

    llmobs._delete_dataset(dataset_id=dataset._id)


def test_dataset_pull_non_existent(llmobs):
    with pytest.raises(ValueError):
        llmobs.pull_dataset(name="test-dataset-non-existent")


@pytest.mark.parametrize("test_dataset_records", [[]])
def test_dataset_pull_exists_but_no_records(llmobs, test_dataset, test_dataset_records, test_dataset_name):
    dataset = llmobs.pull_dataset(name=test_dataset.name)
    assert dataset._id is not None
    assert len(dataset) == 0


def test_dataset_pull_exists_with_record(llmobs, test_dataset_one_record):
    dataset = llmobs.pull_dataset(name=test_dataset_one_record.name)
    assert len(dataset) == 1
    assert dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert dataset[0]["expected_output"] == {"answer": "Paris"}
    assert dataset.name == test_dataset_one_record.name
    assert dataset.description == test_dataset_one_record.description
    assert dataset._version == test_dataset_one_record._version == 1


def test_project_create(llmobs):
    project_id = llmobs._instance._dne_client.project_create(name="test-project")
    assert project_id == "dc4158e7-c60f-446e-bcf1-540aa68ffa0f"


def test_project_get(llmobs):
    project_id = llmobs._instance._dne_client.project_get(name="test-project")
    assert project_id == "dc4158e7-c60f-446e-bcf1-540aa68ffa0f"


def test_experiment_invalid_task_type_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="task must be a callable function."):
        llmobs.experiment("test_experiment", 123, test_dataset_one_record, [dummy_evaluator])


def test_experiment_invalid_task_signature_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="Task function must have an 'input_data' parameter."):

        def my_task(not_input):
            pass

        llmobs.experiment("test_experiment", my_task, test_dataset_one_record, [dummy_evaluator])


def test_experiment_invalid_dataset_raises(llmobs):
    with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset object."):
        llmobs.experiment("test_experiment", dummy_task, 123, [dummy_evaluator])


def test_experiment_invalid_evaluators_type_raises(llmobs, test_dataset_one_record):
    with pytest.raises(TypeError, match="Evaluators must be a list of callable functions"):
        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [])
    with pytest.raises(TypeError, match="Evaluators must be a list of callable functions"):
        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [123])


def test_experiment_invalid_evaluator_signature_raises(llmobs, test_dataset_one_record):
    expected_err = "Evaluator function must have parameters ('input_data', 'output_data', 'expected_output')."
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_expected_output(input_data, output_data):
            pass

        llmobs.experiment(
            "test_experiment", dummy_task, test_dataset_one_record, [my_evaluator_missing_expected_output]
        )
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_input(output_data, expected_output):
            pass

        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [my_evaluator_missing_input])
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_output(input_data, expected_output):
            pass

        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [my_evaluator_missing_output])


def test_experiment_init(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        description="lorem ipsum",
        project_name="test-project",
    )
    assert exp.name == "test_experiment"
    assert exp._task == dummy_task
    assert exp._dataset == test_dataset_one_record
    assert exp._evaluators == [dummy_evaluator]
    assert exp._project_name == "test-project"
    assert exp._description == "lorem ipsum"
    assert exp._project_id is None
    assert exp._run_name is None
    assert exp._id is None


def test_experiment_create_no_project_name_raises(llmobs, test_dataset_one_record):
    project_name = llmobs._project_name
    llmobs._project_name = None
    with pytest.raises(ValueError, match="project_name must be provided for the experiment"):
        llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator], project_name=None)
    llmobs._project_name = project_name  # reset to original value for other tests


def test_experiment_create(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset_one_record,
        [dummy_evaluator],
        project_name="test-project",
    )
    exp_id, exp_run_name = llmobs._instance._create_experiment(
        exp.name, exp._dataset._id, "test-project", exp._dataset._version, exp._config
    )
    assert exp_id is not None
    assert exp_run_name.startswith("test_experiment")
