import pytest


@pytest.mark.subprocess
def test_get_runtime_id():
    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()
    assert isinstance(runtime_id, str)
    assert runtime_id == runtime.get_runtime_id()
    assert runtime_id == runtime.get_runtime_id()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_get_runtime_id_fork():
    import os

    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()
    assert isinstance(runtime_id, str)
    assert runtime_id == runtime.get_runtime_id()
    assert runtime_id == runtime.get_runtime_id()

    child = os.fork()

    if child == 0:
        runtime_id_child = runtime.get_runtime_id()
        assert isinstance(runtime_id_child, str)
        assert runtime_id != runtime_id_child
        assert runtime_id != runtime.get_runtime_id()
        assert runtime_id_child == runtime.get_runtime_id()
        assert runtime_id_child == runtime.get_runtime_id()
        os._exit(42)

    pid, status = os.waitpid(child, 0)

    exit_code = os.WEXITSTATUS(status)

    assert exit_code == 42


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_get_runtime_id_double_fork():
    import os

    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()

    child = os.fork()

    if child == 0:
        runtime_id_child = runtime.get_runtime_id()
        assert runtime_id != runtime_id_child

        child2 = os.fork()

        if child2 == 0:
            runtime_id_child2 = runtime.get_runtime_id()
            assert runtime_id != runtime_id_child
            assert runtime_id_child != runtime_id_child2
            os._exit(42)

        pid, status = os.waitpid(child2, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42

        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


@pytest.mark.subprocess(
    env={
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        "_DD_ROOT_PY_SESSION_ID": None,
        "_DD_PARENT_PY_SESSION_ID": None,
        "DD_TRACE_SUBPROCESS_ENABLED": "false",
    }
)
def test_ancestor_runtime_id():
    """
    Check that the ancestor runtime ID is set after a fork, and that it remains
    the same in nested forks.
    """
    import os

    from ddtrace.internal import runtime

    ancestor_runtime_id = runtime.get_runtime_id()

    assert ancestor_runtime_id is not None
    assert runtime.get_ancestor_runtime_id() is None
    child = os.fork()

    if child == 0:
        assert ancestor_runtime_id != runtime.get_runtime_id()
        assert ancestor_runtime_id == runtime.get_ancestor_runtime_id()

        child = os.fork()

        if child == 0:
            assert ancestor_runtime_id != runtime.get_runtime_id()
            assert ancestor_runtime_id == runtime.get_ancestor_runtime_id()
            os._exit(42)

        _, status = os.waitpid(child, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42

        os._exit(42)

    _, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert runtime.get_ancestor_runtime_id() is None


@pytest.mark.subprocess(
    env={
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        "_DD_ROOT_PY_SESSION_ID": None,
        "_DD_PARENT_PY_SESSION_ID": None,
        "DD_TRACE_SUBPROCESS_ENABLED": "false",
    },
    err=None,
)
def test_parent_runtime_id():
    """get_parent_runtime_id() tracks the immediate parent process, not the root."""
    import os

    from ddtrace.internal import runtime

    root_id = runtime.get_runtime_id()
    assert runtime.get_parent_runtime_id() is None

    child = os.fork()
    if child == 0:
        child_id = runtime.get_runtime_id()
        assert runtime.get_parent_runtime_id() == root_id

        grandchild = os.fork()
        if grandchild == 0:
            assert runtime.get_parent_runtime_id() == child_id
            os._exit(42)

        _, status = os.waitpid(grandchild, 0)
        assert os.WEXITSTATUS(status) == 42
        os._exit(42)

    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 42
