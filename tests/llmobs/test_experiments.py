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

import mock
import pytest

from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord


def wait_for_backend():
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(2)


def dummy_task(input_data, config):
    return input_data


def faulty_task(input_data, config):
    raise ValueError("This is a test error")


def dummy_evaluator(input_data, output_data, expected_output):
    return int(output_data == expected_output)


def faulty_evaluator(input_data, output_data, expected_output):
    raise ValueError("This is a test error in evaluator")


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
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def test_dataset_one_record(llmobs):
    records = [
        DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})
    ]
    ds = llmobs.create_dataset(name="test-dataset-123", description="A test dataset", records=records)
    wait_for_backend()

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


@pytest.mark.parametrize(
    "test_dataset_records",
    [[DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})]],
)
def test_dataset_modify_single_record(llmobs, test_dataset, test_dataset_records):
    assert test_dataset._version == 1

    test_dataset.update(
        0,
        DatasetRecord(input_data={"prompt": "What is the capital of Germany?"}, expected_output={"answer": "Berlin"}),
    )
    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset._version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert ds[0]["expected_output"] == {"answer": "Berlin"}
    assert len(ds) == 1
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds._version == 2


@pytest.mark.parametrize(
    "test_dataset_records",
    [[DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})]],
)
def test_dataset_append(llmobs, test_dataset):
    test_dataset.append(
        DatasetRecord(input_data={"prompt": "What is the capital of Italy?"}, expected_output={"answer": "Rome"})
    )
    assert len(test_dataset) == 2
    assert test_dataset._version == 1

    wait_for_backend()
    test_dataset.push()
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description
    assert test_dataset._version == 2

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
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
            DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"}),
            DatasetRecord(input_data={"prompt": "What is the capital of Italy?"}, expected_output={"answer": "Rome"}),
        ],
    ],
)
def test_dataset_delete(llmobs, test_dataset):
    test_dataset.delete(0)
    assert len(test_dataset) == 1
    assert test_dataset._version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset._version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}


def test_project_create_new_project(llmobs):
    project_id = llmobs._instance._dne_client.project_create_or_get(name="test-project-dne-sdk")
    assert project_id == "29892594-46a2-4539-893d-5dc75f5968b7"


def test_project_get_existing_project(llmobs):
    project_id = llmobs._instance._dne_client.project_create_or_get(name="test-project")
    assert project_id == "dc4158e7-c60f-446e-bcf1-540aa68ffa0f"


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
    project_id = llmobs._instance._dne_client.project_create_or_get("test-project")
    exp_id, exp_run_name = llmobs._instance._dne_client.experiment_create(
        exp.name, exp._dataset._id, project_id, exp._dataset._version, exp._config
    )
    assert exp_id is not None
    assert exp_run_name.startswith("test_experiment")


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"}),
            DatasetRecord(
                input_data={"prompt": "What is the capital of Canada?"}, expected_output={"answer": "Ottawa"}
            ),
        ]
    ],
)
def test_experiment_run_task(llmobs, test_dataset, test_dataset_records):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset, [dummy_evaluator], project_name="test-project")
    task_results = exp._run_task(1, raise_errors=False)
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
    exp = llmobs.experiment(
        "test_experiment", faulty_task, test_dataset_one_record, [dummy_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
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


def test_experiment_run_evaluators(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
    assert len(task_results) == 1
    eval_results = exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {"idx": 0, "evaluations": {"dummy_evaluator": {"value": False, "error": None}}}


def test_experiment_run_evaluators_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
    assert len(task_results) == 1
    eval_results = exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {"idx": 0, "evaluations": {"faulty_evaluator": {"value": None, "error": mock.ANY}}}
    err = eval_results[0]["evaluations"]["faulty_evaluator"]["error"]
    assert err["message"] == "This is a test error in evaluator"
    assert err["type"] == "ValueError"
    assert err["stack"] is not None


def test_experiment_run_evaluators_error_raises(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
    assert len(task_results) == 1
    with pytest.raises(RuntimeError, match="Evaluator faulty_evaluator failed on row 0"):
        exp._run_evaluators(task_results, raise_errors=True)


def test_experiment_merge_results(llmobs, test_dataset_one_record):
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
    eval_results = exp._run_evaluators(task_results, raise_errors=False)
    merged_results = exp._merge_results(task_results, eval_results)

    assert len(merged_results) == 1
    exp_result = merged_results[0]
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
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator], project_name="test-project"
    )
    task_results = exp._run_task(1, raise_errors=False)
    eval_results = exp._run_evaluators(task_results, raise_errors=False)
    merged_results = exp._merge_results(task_results, eval_results)

    assert len(merged_results) == 1
    exp_result = merged_results[0]
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
            "timestamp": 1234567890,
            "output": {"prompt": "What is the capital of France?"},
            "metadata": {
                "dataset_record_index": 0,
                "experiment_name": "test_experiment",
                "dataset_name": "test-dataset-123",
            },
            "error": {"message": None, "type": None, "stack": None},
        }
        exp = llmobs.experiment(
            "test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator], project_name="test-project"
        )
        exp._tags = ["ddtrace.version:1.2.3"]  # FIXME: this is a hack to set the tags for the experiment
        exp_results = exp.run()
    assert len(exp_results) == 1
    exp_result = exp_results[0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}


def test_experiment_span_written_to_experiment_scope(llmobs, llmobs_events, test_dataset_one_record):
    """Assert that the experiment span includes expected output field and includes the experiment scope."""
    exp = llmobs.experiment(
        "test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator], project_name="test-project"
    )
    exp._id = "1234567890"
    exp._run_task(1, raise_errors=False)
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["name"] == "dummy_task"
    for key in ("span_id", "trace_id", "parent_id", "start_ns", "duration", "metrics"):
        assert event[key] == mock.ANY
    assert event["status"] == "ok"
    assert event["meta"]["input"] == {"value": '{"prompt": "What is the capital of France?"}'}
    assert event["meta"]["output"] == {"value": '{"prompt": "What is the capital of France?"}'}
    assert event["meta"]["expected_output"] == {"answer": "Paris"}
    assert "dataset_id:{}".format(test_dataset_one_record._id) in event["tags"]
    assert "dataset_record_id:{}".format(test_dataset_one_record._records[0]["record_id"]) in event["tags"]
    assert "experiment_id:1234567890" in event["tags"]
    assert event["_dd"]["scope"] == "experiments"
