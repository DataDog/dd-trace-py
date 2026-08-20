import pytest


@pytest.mark.subprocess
def test_get_runtime_id():
    import ddtrace.internal.runtime as runtime

    runtime_id = runtime.get_runtime_id()
    assert isinstance(runtime_id, str)
    assert runtime_id == runtime.get_runtime_id()
    assert runtime_id == runtime.get_runtime_id()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_get_runtime_id_fork():
    import os

    import ddtrace.internal.runtime as runtime

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

    import ddtrace.internal.runtime as runtime

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

    import ddtrace.internal.runtime as runtime

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

    import ddtrace.internal.runtime as runtime

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


@pytest.mark.subprocess
def test_get_process_role_single_process() -> None:
    """Single-process application: get_process_role() returns None."""
    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_get_process_role_fork_child() -> None:
    """Forked child process: get_process_role() returns 'worker'."""
    import os

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None

    child = os.fork()
    if child == 0:
        assert get_process_role() == "worker", get_process_role()
        os._exit(0)

    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 0


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_get_process_role_fork_parent() -> None:
    """Parent process after forking a child: get_process_role() returns 'main'."""
    import os

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None

    child = os.fork()
    if child == 0:
        os._exit(0)

    os.waitpid(child, 0)
    assert get_process_role() == "main", get_process_role()


@pytest.mark.subprocess(
    env={
        "_DD_PARENT_PY_SESSION_ID": "some-parent-session-id",
        "DD_TRACE_SUBPROCESS_ENABLED": "false",
    }
)
def test_get_process_role_spawn_child() -> None:
    """Multiprocessing spawn/forkserver child (env-var seeded): returns 'worker'."""
    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() == "worker", get_process_role()


@pytest.mark.subprocess
def test_refresh_identity_changes_runtime_id():
    """refresh_identity() is the non-fork trigger for a new logical process instance."""
    import ddtrace.internal.runtime as runtime

    runtime_id = runtime.get_runtime_id()
    runtime.refresh_identity()
    new_runtime_id = runtime.get_runtime_id()

    assert isinstance(new_runtime_id, str)
    assert new_runtime_id != runtime_id
    assert new_runtime_id == runtime.get_runtime_id()


@pytest.mark.subprocess(
    env={
        "_DD_ROOT_PY_SESSION_ID": None,
        "_DD_PARENT_PY_SESSION_ID": None,
        "DD_TRACE_SUBPROCESS_ENABLED": "false",
    }
)
def test_refresh_identity_does_not_record_fork_lineage():
    """Unlike a fork, refresh_identity() must not make get_process_role() report a fake worker.

    The previous runtime ID was not a real parent process, so recording it as one would
    corrupt process-lineage telemetry.
    """
    import ddtrace.internal.runtime as runtime

    assert runtime.get_process_role() is None
    assert runtime.get_parent_runtime_id() is None
    assert runtime.get_ancestor_runtime_id() is None

    runtime.refresh_identity()

    assert runtime.get_process_role() is None
    assert runtime.get_parent_runtime_id() is None
    assert runtime.get_ancestor_runtime_id() is None


@pytest.mark.subprocess
def test_refresh_identity_notifies_subscribers():
    import ddtrace.internal.runtime as runtime

    seen = []

    class _Subscriber:
        def on_change(self, new_id):
            seen.append(new_id)

    subscriber = _Subscriber()
    runtime.on_runtime_id_change(subscriber.on_change)

    runtime.refresh_identity()

    assert seen == [runtime.get_runtime_id()]


@pytest.mark.subprocess
def test_on_runtime_id_change_falls_back_to_plain_weakref_for_c_callables():
    import ddtrace.internal.runtime as runtime

    seen = []
    cb = seen.append
    assert hasattr(cb, "__self__")

    runtime.on_runtime_id_change(cb)
    runtime.refresh_identity()

    assert seen == [runtime.get_runtime_id()]


@pytest.mark.subprocess
def test_refresh_identity_isolates_subscriber_exceptions():
    """One subscriber raising must not stop refresh_identity() or block other subscribers."""
    import ddtrace.internal.runtime as runtime

    seen = []

    class _BadSubscriber:
        def on_change(self, new_id):
            raise ValueError("boom")

    class _GoodSubscriber:
        def on_change(self, new_id):
            seen.append(new_id)

    bad = _BadSubscriber()
    good = _GoodSubscriber()
    runtime.on_runtime_id_change(bad.on_change)
    runtime.on_runtime_id_change(good.on_change)

    runtime.refresh_identity()

    assert seen == [runtime.get_runtime_id()]


@pytest.mark.subprocess
def test_refresh_identity_serializes_concurrent_notifications():
    import threading

    import ddtrace.internal.runtime as runtime

    seen = []
    seen_lock = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def on_change(new_id):
        with seen_lock:
            seen.append(new_id)
            count = len(seen)

        if count == 1:
            first_entered.set()
            assert release_first.wait(5)
        elif count == 2:
            second_entered.set()

    runtime.on_runtime_id_change(on_change)

    first = threading.Thread(target=runtime.refresh_identity)
    first.start()
    assert first_entered.wait(5)

    second = threading.Thread(target=runtime.refresh_identity)
    second.start()
    assert not second_entered.wait(0.2)

    release_first.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()
    assert seen[-1] == runtime.get_runtime_id()


@pytest.mark.subprocess
def test_on_runtime_id_change_does_not_leak_dead_subscribers():
    """Subscribers are held weakly: once garbage collected they are pruned.

    Subscribers are typically objects constructed many times over a process's life (e.g. a
    trace writer instance per Tracer()); a strong reference here would keep every one of
    them alive for the life of the process.
    """
    import gc

    import ddtrace.internal.runtime as runtime

    class _Subscriber:
        def on_change(self, new_id):
            pass

    # Baseline, not 0: injection/auto-instrumentation may have already constructed a
    # RemoteConfigClient/Writer/TelemetryWriter in this process, each of which subscribes.
    baseline = len(runtime._ON_RUNTIME_ID_CHANGE)

    subscriber = _Subscriber()
    runtime.on_runtime_id_change(subscriber.on_change)
    assert len(runtime._ON_RUNTIME_ID_CHANGE) == baseline + 1

    del subscriber
    gc.collect()

    assert len(runtime._ON_RUNTIME_ID_CHANGE) == baseline
