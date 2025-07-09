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


def test_experiment_task_wrapper_on_invalid_function_raises(llmobs):
    with pytest.raises(TypeError, match="Task function must have an 'input_data' parameter."):

        @llmobs.experiment_task
        def my_task(not_input):
            pass


def test_experiment_task_wrapper_on_valid_function(llmobs):
    @llmobs.experiment_task
    def my_task(input_data):
        return input_data

    assert my_task("test input") == "test input"


def test_experiment_evaluator_wrapper_on_invalid_function_raises(llmobs):
    expected_err = "Evaluator function must have parameters ('input_data', 'output_data', 'expected_output')."
    with pytest.raises(TypeError, match=re.escape(expected_err)):

        @llmobs.experiment_evaluator
        def my_evaluator_missing_expected_output(input_data, output_data):
            pass

    with pytest.raises(TypeError, match=re.escape(expected_err)):

        @llmobs.experiment_evaluator
        def my_evaluator_missing_input(output_data, expected_output):
            pass

    with pytest.raises(TypeError, match=re.escape(expected_err)):

        @llmobs.experiment_evaluator
        def my_evaluator_missing_output(input_data, expected_output):
            pass


def test_experiment_evaluator_wrapper_on_valid_function(llmobs):
    @llmobs.experiment_evaluator
    def my_evaluator(input_data, output_data, expected_output):
        return output_data == expected_output

    assert my_evaluator("test input", "test output", "test output") is True


@pytest.fixture
def dummy_task(llmobs):
    @llmobs.experiment_task
    def my_task(input_data):
        return input_data

    yield my_task


@pytest.fixture
def dummy_evaluator(llmobs):
    @llmobs.experiment_evaluator
    def my_evaluator(input_data, output_data, expected_output):
        return output_data == expected_output

    yield my_evaluator


def test_experiment_create(llmobs, test_dataset, dummy_task, dummy_evaluator):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset, [dummy_evaluator], description="lorem ipsum")
    assert exp.name == "test_experiment"
    assert exp._task == dummy_task
    assert exp._dataset == test_dataset
    assert exp._evaluators == [dummy_evaluator]
