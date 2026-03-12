import mlflow
import pytest

from ddtrace import tracer
from tests.utils import override_config


SNAPSHOT_IGNORES = ["meta.mlflow.run_id"]


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_manual_run(test_spans, assert_run_id_on_all_spans):
    """Tests explictly started and ended mlfow run"""
    mlflow.start_run(run_name="manual_run")
    with tracer.trace("mlflow.manual.work"):
        pass
    mlflow.end_run()
    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_context_manager_run(test_spans, assert_run_id_on_all_spans):
    """Test lifecycle managed mlflow run"""
    with mlflow.start_run(run_name="context_run", tags={"custom": "value"}):
        with tracer.trace("mlflow.manual.work"):
            pass
    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multiple_steps(test_spans, assert_run_id_on_all_spans):
    """Tests that logged metric and param are attached to the right step span"""
    with mlflow.start_run():
        for epoch in range(2):
            loss = epoch
            mlflow.log_param("my_param", "value")
            mlflow.log_metric("loss", loss, step=epoch)
    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_nested_run(test_spans, assert_run_id_on_all_spans):
    """Tests nested mlflow runs"""
    with mlflow.start_run(run_name="parent"):
        mlflow.log_param("p", 1)

        with mlflow.start_run(run_name="child_1", nested=True):
            mlflow.log_param("c1", 10)

        with mlflow.start_run(run_name="child_2", nested=True):
            mlflow.log_param("c2", 20)
    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.error.stack"])
def test_mlflow_error_in_run(test_spans, assert_run_id_on_all_spans):
    """Tests that an error raised during a run is properly attached"""
    with pytest.raises(ValueError):
        with mlflow.start_run():
            raise ValueError("this is an error")
    assert_run_id_on_all_spans(test_spans)


def test_mlflow_log_correlation_context_includes_run_id():
    """Log correlation context includes the MLflow run id only while active."""
    import logging

    import mlflow

    # Silence noisy MLflow DB initialization logs for this assertion-only test.
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


def test_mlflow_log_correlation_context_can_disable_run_id_injection():
    """Disabling MLflow log injection removes the MLflow run id key."""
    from ddtrace.contrib.internal.mlflow.patch import LOG_ATTR_MLFLOW_RUN_ID

    with override_config("mlflow", dict(log_injection=False)):
        context = tracer.get_log_correlation_context()
        assert LOG_ATTR_MLFLOW_RUN_ID not in context
