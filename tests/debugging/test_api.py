import pytest


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_DYNAMIC_INSTRUMENTATION_ENABLED="true"), err=None)
def test_debugger_enabled_ddtrace_run():
    from ddtrace.debugging import DynamicInstrumentation

    assert DynamicInstrumentation._instance is not None


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_debugger_disabled_ddtrace_run():
    from ddtrace.debugging import DynamicInstrumentation

    assert DynamicInstrumentation._instance is None


@pytest.mark.subprocess
def test_debugger_enabled_programmatically():
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    assert "LIVE_DEBUGGING" not in remoteconfig_poller._client._products
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.enable()
    assert DynamicInstrumentation._instance is not None
    assert "LIVE_DEBUGGING" in remoteconfig_poller._client._products

    DynamicInstrumentation.disable()
    assert DynamicInstrumentation._instance is None
    assert "LIVE_DEBUGGING" not in remoteconfig_poller._client._products


@pytest.mark.subprocess(err=None)
def test_debugger_fork():
    import os
    import sys

    from ddtrace.debugging import DynamicInstrumentation
    from ddtrace.internal.service import ServiceStatus

    DynamicInstrumentation.enable()
    assert DynamicInstrumentation._instance is not None
    assert DynamicInstrumentation._instance.status == ServiceStatus.RUNNING

    parent_instance = DynamicInstrumentation._instance
    parent_queue = parent_instance.__uploader__._instance._queue

    child_pid = os.fork()
    if child_pid == 0:
        assert DynamicInstrumentation._instance is not None
        assert DynamicInstrumentation._instance.status == ServiceStatus.RUNNING

        # Proof that the debugger was actually restarted in the child process
        assert DynamicInstrumentation._instance is parent_instance
        assert DynamicInstrumentation._instance.__uploader__._instance._queue is not parent_queue

        exit(0)

    _, status = os.waitpid(child_pid, 0)
    assert DynamicInstrumentation._instance is not None
    assert DynamicInstrumentation._instance.status == ServiceStatus.RUNNING
    sys.exit(os.WEXITSTATUS(status))
