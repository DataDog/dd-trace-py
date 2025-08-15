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
from tests.utils import override_global_config


def wait_for_backend(sleep_dur=2):
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(sleep_dur)


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
    assert dataset.url == f"https://app.datadoghq.com/llm/datasets/{dataset._id}"

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


def test_csv_dataset_as_dataframe(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None
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
    with pytest.raises(ValueError, match=re.escape("Input columns not found in CSV header: ['in998', 'in999']")):
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
    with pytest.raises(ValueError, match=re.escape("Expected output columns not found in CSV header: ['out999']")):
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
    with pytest.raises(ValueError, match=re.escape("CSV file appears to be empty or header is missing.")):
        llmobs.create_dataset_from_csv(
            csv_path=csv_path,
            dataset_name="test-dataset-bad-csv",
            description="not a real csv dataset",
            input_data_columns=["in0", "in1", "in2"],
            expected_output_columns=["out0"],
        )


def test_dataset_csv_no_expected_output(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None
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
        ds = llmobs.pull_dataset(name=dataset.name)

        assert len(ds) == len(dataset)
        assert ds.name == dataset.name
        assert ds.description == dataset.description
        assert ds._version == 1
    finally:
        if dataset_id:
            llmobs._delete_dataset(dataset_id=dataset_id)


def test_dataset_csv(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset.csv")
    dataset_id = None
    try:
        dataset = llmobs.create_dataset_from_csv(
            csv_path=csv_path,
            dataset_name="test-dataset-good-csv",
            description="A good csv dataset",
            input_data_columns=["in0", "in1", "in2"],
            expected_output_columns=["out0", "out1"],
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

        assert len(dataset[0]["expected_output"]) == 2
        assert dataset[0]["expected_output"]["out0"] == "r0v4"
        assert dataset[0]["expected_output"]["out1"] == "r0v5"
        assert dataset[1]["expected_output"]["out0"] == "r1v4"
        assert dataset[1]["expected_output"]["out1"] == "r1v5"

        assert dataset.description == "A good csv dataset"

        assert dataset._id is not None

        wait_for_backend()
        ds = llmobs.pull_dataset(name=dataset.name)

        assert len(ds) == len(dataset)
        assert ds.name == dataset.name
        assert ds.description == dataset.description
        assert ds._version == 1
    finally:
        if dataset_id:
            llmobs._delete_dataset(dataset_id=dataset_id)


def test_dataset_csv_pipe_separated(llmobs):
    test_path = os.path.dirname(__file__)
    csv_path = os.path.join(test_path, "static_files/good_dataset_pipe_separated.csv")
    dataset_id = None
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
        ds = llmobs.pull_dataset(name=dataset.name)

        assert len(ds) == len(dataset)
        assert ds.name == dataset.name
        assert ds.description == dataset.description
        assert ds._version == 1
    finally:
        if dataset_id:
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
    [
        [
            DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"}),
            DatasetRecord(
                input_data={"prompt": "What is the capital of China?"}, expected_output={"answer": "Beijing"}
            ),
        ]
    ],
)
def test_dataset_modify_records_multiple_times(llmobs, test_dataset, test_dataset_records):
    assert test_dataset._version == 1

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
        1, {"input_data": {"prompt": "What is the capital of Mexico?"}, "metadata": {"difficulty": "easy"}}
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
    assert test_dataset._version == 2

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
    ds = llmobs.pull_dataset(name=test_dataset.name)
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
    assert ds._version == 2


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
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset._version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Germany?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Berlin"}
    assert "metadata" not in test_dataset[0]
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
def test_dataset_modify_single_record_empty_record(llmobs, test_dataset, test_dataset_records):
    assert test_dataset._version == 1

    with pytest.raises(
        ValueError,
        match="invalid update, record should contain at least one of "
        "input_data, expected_output, or metadata to update",
    ):
        test_dataset.update(0, {})


@pytest.mark.parametrize(
    "test_dataset_records",
    [[DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})]],
)
def test_dataset_modify_single_record_on_optional_field(llmobs, test_dataset, test_dataset_records):
    assert test_dataset._version == 1

    test_dataset.update(0, {"expected_output": None})
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] is None
    assert "metadata" not in test_dataset[0]

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset._version == 2

    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] is None
    assert "metadata" not in test_dataset[0]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert ds[0]["expected_output"] == ""
    assert len(ds) == 1
    assert ds.name == test_dataset.name
    assert ds.description == test_dataset.description
    assert ds._version == 2


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
def test_dataset_modify_single_record_on_input_should_not_effect_others(llmobs, test_dataset, test_dataset_records):
    assert test_dataset._version == 1

    test_dataset.update(0, {"input_data": "A"})
    assert test_dataset[0]["input_data"] == "A"
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[0]["metadata"] == {"difficulty": "easy"}

    test_dataset.push()
    assert len(test_dataset) == 1
    assert test_dataset._version == 2

    assert test_dataset[0]["input_data"] == "A"
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[0]["metadata"] == {"difficulty": "easy"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # assert that the version is consistent with a new pull

    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds[0]["input_data"] == "A"
    assert ds[0]["expected_output"] == {"answer": "Paris"}
    assert ds[0]["metadata"] == {"difficulty": "easy"}
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
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[1]["expected_output"] == {"answer": "Rome"}
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
    [[DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"})]],
)
def test_dataset_append_no_expected_output(llmobs, test_dataset):
    test_dataset.append(DatasetRecord(input_data={"prompt": "What is the capital of Sealand?"}))
    assert len(test_dataset) == 2
    assert test_dataset._version == 1

    wait_for_backend()
    test_dataset.push()
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of France?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Paris"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Sealand?"}
    assert "expected_output" not in test_dataset[1]
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
    assert ds[1]["expected_output"] == {"answer": "Paris"}
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Sealand?"}
    assert ds[0]["expected_output"] is None
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
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
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
    assert test_dataset._version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset._version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Nauru?"}
    assert "expected_output" not in test_dataset[0]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Nauru?"}
    assert ds[0]["expected_output"] is None


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"}),
            DatasetRecord(input_data={"prompt": "What is the capital of Italy?"}, expected_output={"answer": "Rome"}),
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
    assert test_dataset._version == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset._version == 2
    assert len(test_dataset) == 1
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
    assert len(ds) == 1
    assert ds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert ds[0]["expected_output"] == {"answer": "Rome"}


@pytest.mark.parametrize(
    "test_dataset_records",
    [
        [
            DatasetRecord(input_data={"prompt": "What is the capital of France?"}, expected_output={"answer": "Paris"}),
            DatasetRecord(input_data={"prompt": "What is the capital of Italy?"}, expected_output={"answer": "Rome"}),
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
    assert test_dataset._version == 1

    assert len(test_dataset._new_records_by_record_id) == 1
    assert len(test_dataset._deleted_record_ids) == 1

    wait_for_backend()
    test_dataset.push()
    assert test_dataset._version == 2
    assert len(test_dataset) == 2
    assert test_dataset[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert test_dataset[0]["expected_output"] == {"answer": "Rome"}
    assert test_dataset[1]["input_data"] == {"prompt": "What is the capital of Sweden?"}
    assert "expected_output" not in test_dataset[1]
    assert test_dataset.name == test_dataset.name
    assert test_dataset.description == test_dataset.description

    # check that a pulled dataset matches the pushed dataset
    wait_for_backend()
    ds = llmobs.pull_dataset(name=test_dataset.name)
    assert ds._version == 2
    assert len(ds) == 2
    sds = sorted(ds, key=lambda r: r["input_data"]["prompt"])
    assert sds[0]["input_data"] == {"prompt": "What is the capital of Italy?"}
    assert sds[0]["expected_output"] == {"answer": "Rome"}
    assert sds[1]["input_data"] == {"prompt": "What is the capital of Sweden?"}
    assert sds[1]["expected_output"] is None


def test_project_create_new_project(llmobs):
    project_id = llmobs._instance._dne_client.project_create_or_get(name="test-project-dne-sdk")
    assert project_id == "905824bc-ccec-4444-a48d-401931d5065b"


def test_project_get_existing_project(llmobs):
    project_id = llmobs._instance._dne_client.project_create_or_get(name="test-project")
    assert project_id == "f0a6723e-a7e8-4efd-a94a-b892b7b6fbf9"


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
    env.update({"PYTHONPATH": ":".join(pypath), "DD_TRACE_ENABLED": "0", "DD_LLMOBS_ENABLED": "1"})
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
    assert exp.name == "test_experiment"
    assert exp._task == dummy_task
    assert exp._dataset == test_dataset_one_record
    assert exp._evaluators == [dummy_evaluator]
    assert exp._project_name == "test-project"
    assert exp._description == "lorem ipsum"
    assert exp._project_id is None
    assert exp._run_name is None
    assert exp._id is None


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
    exp = llmobs.experiment(
        "test_experiment",
        dummy_task,
        test_dataset,
        [dummy_evaluator],
        config={"models": ["gpt-4.1"]},
    )
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
    exp = llmobs.experiment("test_experiment", faulty_task, test_dataset_one_record, [dummy_evaluator])
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
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
    task_results = exp._run_task(1, raise_errors=False)
    assert len(task_results) == 1
    eval_results = exp._run_evaluators(task_results, raise_errors=False)
    assert len(eval_results) == 1
    assert eval_results[0] == {"idx": 0, "evaluations": {"dummy_evaluator": {"value": False, "error": None}}}


def test_experiment_run_evaluators_error(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
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
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
    task_results = exp._run_task(1, raise_errors=False)
    assert len(task_results) == 1
    with pytest.raises(RuntimeError, match="Evaluator faulty_evaluator failed on row 0"):
        exp._run_evaluators(task_results, raise_errors=True)


def test_experiment_merge_results(llmobs, test_dataset_one_record):
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
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
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [faulty_evaluator])
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
        exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
        exp._tags = {"ddtrace.version": "1.2.3"}  # FIXME: this is a hack to set the tags for the experiment
        exp_results = exp.run()
    assert len(exp_results) == 1
    exp_result = exp_results[0]
    assert exp_result["idx"] == 0
    assert exp_result["input"] == {"prompt": "What is the capital of France?"}
    assert exp_result["output"] == {"prompt": "What is the capital of France?"}
    assert exp_result["expected_output"] == {"answer": "Paris"}
    assert exp.url == f"https://app.datadoghq.com/llm/experiments/{exp._id}"


def test_experiment_span_written_to_experiment_scope(llmobs, llmobs_events, test_dataset_one_record):
    """Assert that the experiment span includes expected output field and includes the experiment scope."""
    exp = llmobs.experiment("test_experiment", dummy_task, test_dataset_one_record, [dummy_evaluator])
    exp._id = "1234567890"
    exp._run_task(1, raise_errors=False)
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["name"] == "dummy_task"
    for key in ("span_id", "trace_id", "parent_id", "start_ns", "duration", "metrics"):
        assert event[key] == mock.ANY
    assert event["status"] == "ok"
    assert event["meta"]["input"] == '{"prompt": "What is the capital of France?"}'
    assert event["meta"]["output"] == '{"prompt": "What is the capital of France?"}'
    assert event["meta"]["expected_output"] == '{"answer": "Paris"}'
    assert "dataset_id:{}".format(test_dataset_one_record._id) in event["tags"]
    assert "dataset_record_id:{}".format(test_dataset_one_record._records[0]["record_id"]) in event["tags"]
    assert "experiment_id:1234567890" in event["tags"]
    assert event["_dd"]["scope"] == "experiments"
