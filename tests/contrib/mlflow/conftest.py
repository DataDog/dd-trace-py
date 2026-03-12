import pytest

from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.contrib.internal.mlflow.patch import patch as mlflow_patch
from ddtrace.contrib.internal.mlflow.patch import unpatch as mlflow_unpatch


@pytest.fixture(autouse=True)
def patch_mlflow():
    mlflow_patch()
    try:
        yield
    finally:
        mlflow_unpatch()


@pytest.fixture
def assert_run_id_on_all_spans():
    def _assert(test_spans):
        # Every emitted span should be correlated to an MLflow run.
        for span in (s for t in test_spans.pop_traces() for s in t):
            assert span.get_tag(MLFLOW_RUN_ID_TAG) is not None

    return _assert
