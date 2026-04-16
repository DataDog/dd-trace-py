import pytest

from ddtrace.constants import _HOSTNAME_KEY
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.contrib.internal.mlflow.patch import patch as mlflow_patch
from ddtrace.contrib.internal.mlflow.patch import unpatch as mlflow_unpatch
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.settings import env


@pytest.fixture(autouse=True, scope="session")
def mlflow_isolated_tracking_uri(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("mlflow") / "mlflow.db"
    env["MLFLOW_TRACKING_URI"] = f"sqlite:///{db_path}"
    yield
    del env["MLFLOW_TRACKING_URI"]


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


@pytest.fixture
def assert_hostname_on_all_spans():
    expected_hostname = get_hostname()

    def _assert(test_spans):
        # Every emitted span should include the generic hostname tag.
        for span in (s for t in test_spans.pop_traces() for s in t):
            assert span.get_tag(_HOSTNAME_KEY) == expected_hostname

    return _assert
