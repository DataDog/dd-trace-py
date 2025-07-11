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

import pytest


def dummy_task(input_data):
    return input_data


def dummy_evaluator(input_data, output_data, expected_output):
    return output_data == expected_output


@pytest.fixture
def test_dataset(llmobs):
    ds = llmobs.create_dataset(name="test-dataset", description="A test dataset")

    # When recording the requests, we need to wait for the dataset to be queryable.
    if os.environ.get("RECORD_REQUESTS"):
        import time

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


def test_dataset_pull(llmobs, test_dataset):
    dataset = llmobs.pull_dataset(name=test_dataset.name)
    assert dataset._id is not None
    assert dataset.name == test_dataset.name
    assert dataset.description == test_dataset.description
    assert dataset._version == test_dataset._version


def test_experiment_invalid_task_type_raises(llmobs, test_dataset):
    with pytest.raises(TypeError, match="task must be a callable function."):
        llmobs.experiment("test_experiment", 123, test_dataset, [dummy_evaluator])


def test_experiment_invalid_task_signature_raises(llmobs, test_dataset):
    with pytest.raises(TypeError, match="Task function must have an 'input_data' parameter."):

        def my_task(not_input):
            pass

        llmobs.experiment("test_experiment", my_task, test_dataset, [dummy_evaluator])


def test_experiment_invalid_dataset_raises(llmobs):
    with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset object."):
        llmobs.experiment("test_experiment", dummy_task, 123, [dummy_evaluator])


def test_experiment_invalid_evaluators_type_raises(llmobs, test_dataset):
    with pytest.raises(TypeError, match="Evaluators must be a list of callable functions"):
        llmobs.experiment("test_experiment", dummy_task, test_dataset, [])
    with pytest.raises(TypeError, match="Evaluators must be a list of callable functions"):
        llmobs.experiment("test_experiment", dummy_task, test_dataset, [123])


def test_experiment_invalid_evaluator_signature_raises(llmobs, test_dataset):
    expected_err = "Evaluator function must have parameters ('input_data', 'output_data', 'expected_output')."
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_expected_output(input_data, output_data):
            pass

        llmobs.experiment("test_experiment", dummy_task, test_dataset, [my_evaluator_missing_expected_output])
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_input(output_data, expected_output):
            pass

        llmobs.experiment("test_experiment", dummy_task, test_dataset, [my_evaluator_missing_input])
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        def my_evaluator_missing_output(input_data, expected_output):
            pass

        llmobs.experiment("test_experiment", dummy_task, test_dataset, [my_evaluator_missing_output])


def test_experiment_create(llmobs, test_dataset):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset, [dummy_evaluator], description="lorem ipsum")
    assert exp.name == "test_experiment"
    assert exp._task == dummy_task
    assert exp._dataset == test_dataset
    assert exp._evaluators == [dummy_evaluator]
