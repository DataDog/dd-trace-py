import pytest

from ddtrace.debugging import Debugger


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_DEBUGGER_ENABLED="true"), err=None)
def test_debugger_enabled_ddtrace_run():
    from ddtrace.debugging import Debugger

    assert Debugger._instance is not None


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_debugger_disabled_ddtrace_run():
    from ddtrace.debugging import Debugger

    assert Debugger._instance is None


def test_debugger_enabled_programmatically():
    Debugger.enable()
    assert Debugger._instance is not None

    Debugger.disable()
    assert Debugger._instance is None


@pytest.mark.subprocess(err=None)
def test_debugger_fork():
    import os
    import sys

    from ddtrace.debugging import Debugger
    from ddtrace.internal.service import ServiceStatus

    Debugger.enable()
    assert Debugger._instance is not None
    assert Debugger._instance.status == ServiceStatus.RUNNING

    parent_instance = Debugger._instance

    child_pid = os.fork()
    if child_pid == 0:
        assert Debugger._instance is not None
        assert Debugger._instance.status == ServiceStatus.RUNNING

        # Proof that the debugger was actually restarted in the child process
        assert Debugger._instance is not parent_instance

        exit(0)

    _, status = os.waitpid(child_pid, 0)
    assert Debugger._instance is not None
    assert Debugger._instance.status == ServiceStatus.RUNNING
    sys.exit(os.WEXITSTATUS(status))
