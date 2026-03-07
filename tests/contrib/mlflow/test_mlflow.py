import mlflow
import pytest

from ddtrace import tracer
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.contrib.internal.mlflow.patch import patch
from ddtrace.contrib.internal.mlflow.patch import unpatch


SNAPSHOT_IGNORES = ["meta.mlflow.run_id"]


def _assert_run_id_on_all_spans(test_spans):
    for span in (s for t in test_spans.pop_traces() for s in t):
        assert span.get_tag(MLFLOW_RUN_ID_TAG) is not None


@pytest.fixture(autouse=True)
def patch_mlflow():
    patch()
    try:
        yield
    finally:
        unpatch()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_manual_run(test_spans):
    mlflow.start_run(run_name="manual_run")
    with tracer.trace("mlflow.manual.work"):
        pass
    mlflow.end_run()
    _assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_context_manager_run(test_spans):
    with mlflow.start_run(run_name="context_run", tags={"custom": "value"}):
        with tracer.trace("mlflow.manual.work"):
            pass
    _assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multiple_steps(test_spans):
    with mlflow.start_run():
        for epoch in range(2):
            loss = epoch
            mlflow.log_param("my_param", "value")
            mlflow.log_metric("loss", loss, step=epoch)
    _assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.error.stack"])
def test_mlflow_error_in_run(test_spans):
    with pytest.raises(ValueError):
        with mlflow.start_run():
            raise ValueError("this is an error")
    _assert_run_id_on_all_spans(test_spans)


@pytest.mark.subprocess(
    ddtrace_run=True, err=lambda stderr: not stderr or "FutureWarning: The filesystem tracking backend" in stderr
)
def test_mlflow_log_correlation_context_includes_run_id():
    import logging

    import mlflow

    # mlflow logs to error database initialisation
    logging.getLogger("mlflow.store.db.utils").setLevel(logging.WARNING)

    from ddtrace import tracer
    from ddtrace.contrib.internal.mlflow.patch import LOG_ATTR_MLFLOW_RUN_ID

    before_run = tracer.get_log_correlation_context()
    assert before_run[LOG_ATTR_MLFLOW_RUN_ID] is None

    with mlflow.start_run() as run:
        inside_run = tracer.get_log_correlation_context()
        assert inside_run[LOG_ATTR_MLFLOW_RUN_ID] == run.info.run_id

    after_run = tracer.get_log_correlation_context()
    assert after_run[LOG_ATTR_MLFLOW_RUN_ID] is None
