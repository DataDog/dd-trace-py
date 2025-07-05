import os

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
