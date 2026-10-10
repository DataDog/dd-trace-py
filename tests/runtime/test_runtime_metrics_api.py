import os

import pytest

from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.internal.service import ServiceStatus
from ddtrace.runtime import RuntimeMetrics


def test_runtime_metrics_api():
    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.status == ServiceStatus.RUNNING

    RuntimeMetrics.disable()
    assert RuntimeWorker._instance is None


def test_runtime_metrics_api_idempotency():
    RuntimeMetrics.enable()
    instance = RuntimeWorker._instance
    assert instance is not None
    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is instance

    RuntimeMetrics.disable()
    assert RuntimeWorker._instance is None
    RuntimeMetrics.disable()


@pytest.mark.subprocess
def test_manually_start_runtime_metrics():
    """
    When importing and manually starting runtime metrics
        Runtime metrics worker starts and there are no errors
    """
    from ddtrace.runtime import RuntimeMetrics

    RuntimeMetrics.enable()
    assert RuntimeMetrics._enabled

    RuntimeMetrics.disable()
    assert not RuntimeMetrics._enabled


def test_manually_start_runtime_metrics_telemetry(test_agent_session, run_python_code_in_subprocess):
    """
    When importing and manually starting runtime metrics
        Runtime metrics worker starts and we report that it is enabled via telemetry
    """
    code = """
from ddtrace.internal.telemetry import telemetry_writer

from ddtrace.runtime import RuntimeMetrics

assert not RuntimeMetrics._enabled
RuntimeMetrics.enable()
assert RuntimeMetrics._enabled
telemetry_writer.periodic(force_flush=True)
    """

    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr

    runtimemetrics_enabled = test_agent_session.get_configurations("DD_RUNTIME_METRICS_ENABLED")
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"]
    assert runtimemetrics_enabled[0]["origin"] == "code"


def test_manually_stop_runtime_metrics_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """
    When importing and manually stopping runtime metrics
        Runtime metrics worker stops and we report that it is enabled via telemetry
    """
    code = """
from ddtrace.internal.telemetry import telemetry_writer

from ddtrace.runtime import RuntimeMetrics

assert RuntimeMetrics._enabled
RuntimeMetrics.disable()
assert not RuntimeMetrics._enabled
telemetry_writer.periodic(force_flush=True)
    """

    env = os.environ.copy()
    env["DD_RUNTIME_METRICS_ENABLED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    runtimemetrics_enabled = test_agent_session.get_configurations("DD_RUNTIME_METRICS_ENABLED")
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"] is False
    assert runtimemetrics_enabled[0]["origin"] == "code"


def test_start_runtime_metrics_via_env_var(monkeypatch, ddtrace_run_python_code_in_subprocess):
    """
    When running with ddtrace-run and DD_RUNTIME_METRICS_ENABLED is set
        Runtime metrics worker starts and there are no errors
    """

    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert not RuntimeMetrics._enabled
"""
    )
    assert status == 0

    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert RuntimeMetrics._enabled
""",
    )
    assert status == 0


def test_runtime_metrics_via_env_var_manual_start(monkeypatch, ddtrace_run_python_code_in_subprocess):
    """
    When running with ddtrace-run and DD_RUNTIME_METRICS_ENABLED is set and trying to start RuntimeMetrics manually
        Runtime metrics worker starts and there are no errors
    """

    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert RuntimeMetrics._enabled
RuntimeMetrics.enable()
assert RuntimeMetrics._enabled
""",
    )
    assert status == 0


@pytest.mark.parametrize(
    "enable_kwargs",
    (
        dict(),
        dict(dogstatsd_url="udp://agent:8125"),
        dict(dogstatsd_url="udp://agent:8125", flush_interval=100.0),
        dict(flush_interval=0),
    ),
)
def test_runtime_metrics_enable(enable_kwargs):
    try:
        RuntimeMetrics.enable(**enable_kwargs)

        assert RuntimeWorker._instance is not None
        assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
        assert (
            RuntimeWorker._instance.dogstatsd_url == enable_kwargs["dogstatsd_url"]
            if "dogstatsd_url" in enable_kwargs
            else RuntimeWorker._instance.dogstatsd_url is None
        )
        assert (
            RuntimeWorker._instance.interval == enable_kwargs["flush_interval"]
            if "flush_interval" in enable_kwargs
            else RuntimeWorker._instance.interval == 10.0
        )
    finally:
        RuntimeMetrics.disable()


@pytest.mark.parametrize(
    "environ",
    (
        dict(),
        dict(DD_RUNTIME_METRICS_INTERVAL="0.0"),
        dict(DD_RUNTIME_METRICS_INTERVAL="100.0"),
    ),
)
def test_runtime_metrics_enable_environ(monkeypatch, environ):
    try:
        for k, v in environ.items():
            monkeypatch.setenv(k, v)

        RuntimeMetrics.enable()

        assert RuntimeWorker._instance is not None
        assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
        assert (
            RuntimeWorker._instance.interval == float(environ["DD_RUNTIME_METRICS_INTERVAL"])
            if "DD_RUNTIME_METRICS_INTERVAL" in environ
            else RuntimeWorker._instance.interval == 10.0
        )
    finally:
        RuntimeMetrics.disable()


@pytest.mark.subprocess(
    parametrize={
        "DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED": ["true", "false"],
        "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": ["true", "false"],
    }
)
def test_runtime_metrics_experimental_runtime_tag():
    """
    When runtime metrics is enabled and DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED=DD_RUNTIME_METRICS_ENABLED
        Runtime metrics worker starts and submits gauge metrics instead of distribution metrics
    """
    import os

    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.internal.service import ServiceStatus

    RuntimeWorker.enable()
    assert RuntimeWorker._instance is not None

    worker_instance = RuntimeWorker._instance
    assert worker_instance.status == ServiceStatus.RUNNING

    runtime_id_tag = f"runtime-id:{get_runtime_id()}"
    if (
        os.environ["DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED"] == "true"
        or os.environ["DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED"] == "true"
    ):
        assert runtime_id_tag in worker_instance._platform_tags, worker_instance._platform_tags
    elif (
        os.environ["DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED"] == "false"
        or os.environ["DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED"] == "false"
    ):
        assert runtime_id_tag not in worker_instance._platform_tags, worker_instance._platform_tags
    else:
        raise pytest.fail(
            "Invalid value for DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED or DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED"
        )


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_refresh_identity_updates_runtime_id_tag():
    """Runtime metrics must not keep reporting the pre-refresh runtime-id tag."""
    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None

        old_runtime_id = runtime.get_runtime_id()
        assert f"runtime-id:{old_runtime_id}" in worker._platform_tags

        runtime.refresh_identity()
        worker.flush()

        new_runtime_id = runtime.get_runtime_id()
        assert f"runtime-id:{new_runtime_id}" in worker._platform_tags
        assert f"runtime-id:{old_runtime_id}" not in worker._platform_tags
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_refresh_reset_waits_for_in_progress_flush():
    """A refresh can rotate the ID during a send, but its collector reset waits for that send.

    The worker's own lock makes the reset atomic with flush(), so baselines are never reset in the
    middle of a send.
    """
    import threading

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    flush_entered = threading.Event()
    release_flush = threading.Event()
    reset_calls = []
    old_flush_unlocked = worker._flush_unlocked
    old_reset = worker._runtime_metrics.reset

    def flush_unlocked(runtime_metrics):
        flush_entered.set()
        assert release_flush.wait(5)
        old_flush_unlocked(runtime_metrics)

    def reset():
        reset_calls.append(release_flush.is_set())
        old_reset()

    worker._flush_unlocked = flush_unlocked
    worker._runtime_metrics.reset = reset
    flush_thread = threading.Thread(target=worker.flush)
    refresh_thread = None
    try:
        flush_thread.start()
        assert flush_entered.wait(5)

        refresh_thread = threading.Thread(target=runtime.refresh_identity)
        refresh_thread.start()
        refresh_thread.join(0.2)
        assert refresh_thread.is_alive()
        assert reset_calls == []
    finally:
        release_flush.set()
        flush_thread.join(5)
        if refresh_thread is not None:
            refresh_thread.join(5)
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)

    assert not flush_thread.is_alive()
    assert refresh_thread is not None and not refresh_thread.is_alive()
    assert reset_calls == [True]
    assert worker._collectors_runtime_id == runtime.get_runtime_id()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_flush_skips_when_id_rotates_during_tag_collection():
    """An ID rotation between the mismatch check and tag collection must not pair new tags with old baselines."""
    from ddtrace.internal import _runtime_id
    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    sent = []
    old_collect_platform_tags = worker._collect_platform_tags

    def collect_platform_tags_then_rotate():
        tags = old_collect_platform_tags()
        # Rotate without running refresh callbacks, as if /run landed during tag collection.
        _runtime_id._refresh_runtime_id()
        return tags

    worker._collect_platform_tags = collect_platform_tags_then_rotate
    worker._flush_unlocked = sent.append
    try:
        worker.flush()
        assert sent == []

        worker._collect_platform_tags = old_collect_platform_tags
        worker.flush()
        assert sent == []
        assert worker._collectors_runtime_id == runtime.get_runtime_id()

        worker.flush()
        assert len(sent) == 1
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.subprocess(env={"DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "false"}, err=None)
def test_runtime_metrics_flush_keeps_platform_tags_cached_without_runtime_id():
    """Runtime metrics must not rebuild static platform tags on every flush."""
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None

        platform_tags = worker._platform_tags
        worker._runtime_metrics._collectors = ()

        def fail_collect_platform_tags():
            raise AssertionError("platform tags should stay cached when runtime-id tagging is disabled")

        worker._collect_platform_tags = fail_collect_platform_tags
        worker.flush()

        assert worker._platform_tags is platform_tags
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(env={"DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"}, err=None)
def test_runtime_metrics_non_microvm_flush_keeps_platform_tags_cached():
    """Non-MicroVM workers must keep cached platform tags and avoid the refresh lock."""
    from unittest import mock

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None
        assert not worker._identity_refresh_enabled
        assert worker._identity_refresh_lock is None

        platform_tags = worker._platform_tags
        worker._runtime_metrics._collectors = ()
        with mock.patch.object(worker, "_collect_platform_tags") as collect_platform_tags:
            worker.flush()

        collect_platform_tags.assert_not_called()
        assert worker._platform_tags is platform_tags
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_identity_refresh_resets_collectors():
    """A MicroVM /run identity refresh must discard the in-flight metric window so the next
    sample reports only post-refresh activity, not a window mixing old and new identities.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None
        assert worker._identity_refresh_enabled

        with mock.patch.object(worker._runtime_metrics, "reset") as reset:
            runtime.refresh_identity()
        reset.assert_called_once()
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_flush_resets_after_failed_refresh_reset():
    """If the refresh callback's collector reset fails, flushes must retry the reset without raising
    or sending pre-refresh baselines under the new runtime-id, and a retried callback must not
    reset again once a flush has.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    try:
        old_runtime_id = runtime.get_runtime_id()
        with mock.patch.object(worker._runtime_metrics, "reset", side_effect=RuntimeError("boom")):
            runtime.refresh_identity()
            assert worker._collectors_runtime_id == old_runtime_id

            # Raising here would stop the periodic thread.
            with mock.patch.object(worker, "_flush_unlocked") as flush_unlocked:
                worker.flush()
            flush_unlocked.assert_not_called()
        assert worker._collectors_runtime_id == old_runtime_id

        calls = []
        with (
            mock.patch.object(worker._runtime_metrics, "reset", side_effect=lambda: calls.append("reset")),
            mock.patch.object(worker, "_flush_unlocked", side_effect=lambda _metrics: calls.append("flush")),
        ):
            worker.flush()
            worker.flush()
            worker._on_identity_refresh(runtime.get_runtime_id())

        # The resetting flush sends nothing, and the retried callback is a no-op.
        assert calls == ["reset", "flush"]
        assert worker._collectors_runtime_id == runtime.get_runtime_id()
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_flush_resets_after_refresh_during_construction():
    """A refresh that lands after the collectors seed their baselines but before the worker
    registers its refresh callback must still be caught by the first flush.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime import runtime_metrics

    real_runtime_metrics = runtime_metrics.RuntimeMetrics

    def seed_then_refresh():
        collectors = real_runtime_metrics()
        runtime.refresh_identity()
        return collectors

    with mock.patch.object(runtime_metrics, "RuntimeMetrics", side_effect=seed_then_refresh):
        worker = runtime_metrics.RuntimeWorker()
    try:
        assert worker._collectors_runtime_id != runtime.get_runtime_id()

        calls = []
        with (
            mock.patch.object(worker._runtime_metrics, "reset", side_effect=lambda: calls.append("reset")),
            mock.patch.object(worker, "_flush_unlocked", side_effect=lambda _metrics: calls.append("flush")),
        ):
            worker.flush()

        assert calls == ["reset"]
        assert worker._collectors_runtime_id == runtime.get_runtime_id()
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork()")
@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_forked_child_flush_does_not_reset():
    """A fork rotates the runtime-id without an identity refresh, and the collectors already reseed
    in their own fork hooks, so the child's first flush must not reset them again.
    """
    import os
    import traceback
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    try:
        parent_runtime_id = runtime.get_runtime_id()
        pid = os.fork()
        if pid == 0:
            try:
                assert runtime.get_runtime_id() != parent_runtime_id
                assert worker._collectors_runtime_id == runtime.get_runtime_id()

                with (
                    mock.patch.object(worker._runtime_metrics, "reset") as reset,
                    mock.patch.object(worker, "_flush_unlocked") as flush_unlocked,
                ):
                    worker.flush()

                reset.assert_not_called()
                flush_unlocked.assert_called_once_with(worker._runtime_metrics)
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)

        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork()")
@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_fork_resets_collectors_for_child_id():
    """The worker's fork hook must reset the collectors itself instead of trusting their own fork
    hooks, which forksafe skips past when they raise, before marking baselines as the child's.
    """
    import os
    import traceback
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    try:
        parent_runtime_id = runtime.get_runtime_id()
        with mock.patch.object(worker._runtime_metrics, "reset") as reset:
            pid = os.fork()
            if pid == 0:
                try:
                    assert runtime.get_runtime_id() != parent_runtime_id
                    reset.assert_called_once_with()
                    assert worker._collectors_runtime_id == runtime.get_runtime_id()
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)

        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        # The fork hook only runs in the child.
        reset.assert_not_called()
        assert worker._collectors_runtime_id == parent_runtime_id
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork()")
@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_failed_fork_reset_keeps_mismatch():
    """If the collector reset fails in the child, the baselines may still be the parent's, so the
    child must keep the mismatch and have its first flush reset instead of sending them under the
    child's runtime-id.
    """
    import os
    import traceback
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    try:
        parent_runtime_id = runtime.get_runtime_id()
        with mock.patch.object(worker._runtime_metrics, "reset", side_effect=RuntimeError("boom")):
            pid = os.fork()
            if pid == 0:
                try:
                    assert runtime.get_runtime_id() != parent_runtime_id
                    assert worker._collectors_runtime_id == parent_runtime_id

                    calls = []
                    with (
                        mock.patch.object(worker._runtime_metrics, "reset", side_effect=lambda: calls.append("reset")),
                        mock.patch.object(
                            worker, "_flush_unlocked", side_effect=lambda _metrics: calls.append("flush")
                        ),
                    ):
                        worker.flush()
                        worker.flush()

                    # The resetting flush sends nothing; the next one sends under the child's ID.
                    assert calls == ["reset", "flush"]
                    assert worker._collectors_runtime_id == runtime.get_runtime_id()
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)

        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        worker._runtime_metrics.stop()
        runtime.remove_runtime_identity_refresh(worker._on_identity_refresh)


@pytest.mark.subprocess(env={"DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"}, err=None)
def test_runtime_metrics_non_microvm_flush_ignores_runtime_id_change():
    """Outside a MicroVM, a runtime-id change must not make flush() reset collectors: the
    identity check is MicroVM-only, and fork resets stay with the collectors' forksafe hooks.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker()
    try:
        runtime.refresh_identity()
        assert worker._collectors_runtime_id != runtime.get_runtime_id()

        with (
            mock.patch.object(worker._runtime_metrics, "reset") as reset,
            mock.patch.object(worker, "_flush_unlocked") as flush_unlocked,
        ):
            worker.flush()

        reset.assert_not_called()
        flush_unlocked.assert_called_once_with(worker._runtime_metrics)
    finally:
        worker._runtime_metrics.stop()


@pytest.mark.subprocess(env={"DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"}, err=None)
def test_runtime_metrics_non_microvm_identity_refresh_does_not_reset_collectors():
    """Outside a MicroVM, an identity refresh must not touch collector interval state: those
    processes keep one runtime identity for their lifetime and must not pay for this hook.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None
        assert not worker._identity_refresh_enabled

        with mock.patch.object(worker._runtime_metrics, "reset") as reset:
            runtime.refresh_identity()
        reset.assert_not_called()
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "false"},
    err=None,
)
def test_runtime_metrics_microvm_without_runtime_id_does_not_register_refresh():
    """A MicroVM without runtime-id tagging enabled must not register the refresh hook either:
    the reset path is limited to the runtime-id-enabled metrics path, not every MicroVM.
    """
    from unittest import mock

    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    try:
        worker = RuntimeWorker._instance
        assert worker is not None
        assert not worker._identity_refresh_enabled

        with mock.patch.object(worker._runtime_metrics, "reset") as reset:
            runtime.refresh_identity()
        reset.assert_not_called()
    finally:
        RuntimeWorker.disable()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:test", "DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED": "true"},
    err=None,
)
def test_runtime_metrics_microvm_stop_unregisters_refresh_hook():
    """disable() must unregister the refresh callback and fork hook; a later identity refresh (e.g.
    reused by another component after this worker stopped) must not touch a torn-down worker's
    collectors.
    """
    from unittest import mock

    from ddtrace.internal import forksafe
    from ddtrace.internal import runtime
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()
    worker = RuntimeWorker._instance
    assert worker is not None
    RuntimeWorker.disable()

    with mock.patch.object(worker._runtime_metrics, "reset") as reset:
        runtime.refresh_identity()
    reset.assert_not_called()
    assert worker._on_fork not in forksafe._registry


@pytest.mark.subprocess(
    parametrize={"DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED": ["DD_RUNTIME_METRICS_ENABLED,someotherfeature", ""]},
    err=None,
)
def test_runtime_metrics_experimental_metric_type():
    """
    When runtime metrics is enabled and DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED=DD_RUNTIME_METRICS_ENABLED
        Runtime metrics worker starts and submits gauge metrics instead of distribution metrics
    """
    import os

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.internal.service import ServiceStatus

    RuntimeWorker.enable()
    assert RuntimeWorker._instance is not None

    worker_instance = RuntimeWorker._instance
    assert worker_instance.status == ServiceStatus.RUNNING
    if "DD_RUNTIME_METRICS_ENABLED" in os.environ["DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED"]:
        assert worker_instance.send_metric == worker_instance._dogstatsd_client.gauge, worker_instance.send_metric
    else:
        assert worker_instance.send_metric == worker_instance._dogstatsd_client.distribution, (
            worker_instance.send_metric
        )
