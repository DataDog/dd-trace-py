import os

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
def test_fork_notifies_runtime_id_subscribers():
    import os

    from ddtrace.internal import runtime

    seen = []

    def on_change(new_id):
        seen.append(new_id)

    runtime.on_runtime_id_change(on_change)

    child = os.fork()
    if child == 0:
        assert seen == [runtime.get_runtime_id()]
        os._exit(42)

    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 42


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_fork_does_not_notify_runtime_identity_refresh_subscribers():
    import os

    from ddtrace.internal import runtime

    seen = []

    def on_refresh(new_id):
        seen.append(new_id)

    runtime.on_runtime_identity_refresh(on_refresh)

    child = os.fork()
    if child == 0:
        assert seen == []
        runtime.refresh_identity()
        assert seen == [runtime.get_runtime_id()]
        os._exit(42)

    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 42


@pytest.mark.subprocess(
    env={"PYTHONWARNINGS": "ignore::DeprecationWarning"},
    err=lambda s: "Exception ignored in runtime ID callback" in s,
)
def test_runtime_id_callback_failure_does_not_block_other_callbacks():
    from ddtrace.internal import runtime

    seen = []

    def on_change_raises(new_id):
        seen.append(("raises", new_id))
        raise RuntimeError("callback failed")

    def on_change(new_id):
        seen.append(("change", new_id))

    def on_refresh(new_id):
        seen.append(("refresh", new_id))

    runtime.on_runtime_id_change(on_change_raises)
    runtime.on_runtime_id_change(on_change)
    runtime.on_runtime_identity_refresh(on_refresh)

    runtime.refresh_identity()

    runtime_id = runtime.get_runtime_id()
    assert ("raises", runtime_id) in seen
    assert [entry for entry in seen if entry[0] != "raises"] == [
        ("change", runtime_id),
        ("refresh", runtime_id),
    ]


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


def test_refresh_identity_changes_runtime_id(run_python_code_in_subprocess):
    """refresh_identity() is the non-fork trigger for a new logical process instance."""
    code = """
from ddtrace.internal import runtime

runtime_id = runtime.get_runtime_id()
runtime.refresh_identity()
new_runtime_id = runtime.get_runtime_id()

assert isinstance(new_runtime_id, str)
assert new_runtime_id != runtime_id
assert new_runtime_id == runtime.get_runtime_id()
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err


def test_refresh_identity_does_not_record_fork_lineage(run_python_code_in_subprocess):
    """Unlike a fork, refresh_identity() must not make get_process_role() report a fake worker.

    The previous runtime ID was not a real parent process, so recording it as one would
    corrupt process-lineage telemetry.
    """
    import os

    env = os.environ.copy()
    env.update(
        {
            "_DD_ROOT_PY_SESSION_ID": None,
            "_DD_PARENT_PY_SESSION_ID": None,
            "DD_TRACE_SUBPROCESS_ENABLED": "false",
        }
    )
    code = """
from ddtrace.internal import runtime

assert runtime.get_process_role() is None
assert runtime.get_parent_runtime_id() is None
assert runtime.get_ancestor_runtime_id() is None

runtime.refresh_identity()

assert runtime.get_process_role() is None
assert runtime.get_parent_runtime_id() is None
assert runtime.get_ancestor_runtime_id() is None
"""
    _, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err


def test_refresh_identity_preserves_spawned_lineage(run_python_code_in_subprocess):
    import os

    env = os.environ.copy()
    env.update(
        {
            "_DD_ROOT_PY_SESSION_ID": "ancestor-session-id",
            "_DD_PARENT_PY_SESSION_ID": "parent-session-id",
            "DD_TRACE_SUBPROCESS_ENABLED": "false",
        }
    )
    code = """
from ddtrace.internal import runtime

assert runtime.get_ancestor_runtime_id() == "ancestor-session-id"
assert runtime.get_parent_runtime_id() == "parent-session-id"
assert runtime.get_process_role() == "worker"

runtime.refresh_identity()

assert runtime.get_ancestor_runtime_id() == "ancestor-session-id"
assert runtime.get_parent_runtime_id() == "parent-session-id"
assert runtime.get_process_role() == "worker"
"""
    _, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err


def test_refresh_identity_preserves_fork_lineage(run_python_code_in_subprocess):
    import os

    env = os.environ.copy()
    env.update(
        {
            "_DD_ROOT_PY_SESSION_ID": None,
            "_DD_PARENT_PY_SESSION_ID": None,
            "DD_TRACE_SUBPROCESS_ENABLED": "false",
        }
    )
    code = """
import os

from ddtrace.internal import runtime

root_id = runtime.get_runtime_id()
child = os.fork()

if child == 0:
    parent_id = runtime.get_parent_runtime_id()
    ancestor_id = runtime.get_ancestor_runtime_id()

    assert parent_id == root_id
    assert ancestor_id == root_id
    assert runtime.get_process_role() == "worker"

    runtime.refresh_identity()

    assert runtime.get_parent_runtime_id() == parent_id
    assert runtime.get_ancestor_runtime_id() == ancestor_id
    assert runtime.get_process_role() == "worker"
    os._exit(42)

_, status = os.waitpid(child, 0)
assert os.WEXITSTATUS(status) == 42
"""
    _, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err


def test_refresh_identity_notifies_subscribers(run_python_code_in_subprocess):
    code = """
from ddtrace.internal import runtime

seen = []


class _Subscriber:
    def on_change(self, new_id):
        seen.append(new_id)


subscriber = _Subscriber()
runtime.on_runtime_id_change(subscriber.on_change)

runtime.refresh_identity()

assert seen == [runtime.get_runtime_id()]
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_import_ddtrace_in_microvm_environment():
    """MicroVM hook registration must not import contrib before ddtrace.config exists."""
    import ddtrace

    assert ddtrace.config is not None


@pytest.mark.subprocess(
    env={
        "AWS_LAMBDA_MICROVM_IMAGE_ARN": None,
        "_DD_GLOBAL_TRACER_INIT": "false",
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
    },
    err=None,
)
def test_import_ddtrace_outside_microvm_does_not_import_core():
    """Normal ddtrace imports do not load the event core needed only by MicroVM hooks."""
    import sys

    import ddtrace  # noqa: F401

    assert "ddtrace.internal.core" not in sys.modules


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_maybe_refresh_identity_matches_microvm_run_hook():
    """Only the exact AWS Lambda MicroVM /run hook request triggers a refresh."""
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    runtime_id = runtime.get_runtime_id()

    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)

    refreshed_runtime_id = runtime.get_runtime_id()
    assert refreshed_runtime_id != runtime_id

    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)

    assert runtime.get_runtime_id() == refreshed_runtime_id


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_identity_refresh_hook_runs_before_root_span_creation():
    """The pre-request hook must refresh runtime-id before a web root span reads it."""
    from ddtrace import tracer
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)
    runtime_id = runtime.get_runtime_id()
    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH))

    refreshed_runtime_id = runtime.get_runtime_id()
    assert refreshed_runtime_id != runtime_id

    with tracer.trace("web.request") as span:
        assert span.get_tag("runtime-id") == refreshed_runtime_id

    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH))

    assert runtime.get_runtime_id() == refreshed_runtime_id


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_maybe_refresh_identity_is_thread_safe():
    """Concurrent observations of the same /run hook refresh identity once."""
    import threading
    import time

    import ddtrace.internal._runtime_id as runtime_impl
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    calls = []

    def refresh_identity(*, raise_on_error=False):
        calls.append(1)
        time.sleep(0.01)

    runtime_impl._refresh_runtime_id = refresh_identity

    workers = 16
    barrier = threading.Barrier(workers)
    errors = []
    threads = []

    def refresh_from_request_layer():
        try:
            barrier.wait()
            runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)
        except Exception as e:
            errors.append(e)

    for _ in range(workers):
        thread = threading.Thread(target=refresh_from_request_layer)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    assert errors == []
    assert len(calls) == 1


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"},
    err=lambda s: "Exception ignored in runtime ID callback" in s,
)
def test_identity_refresh_callback_failure_is_logged_and_not_retried():
    """/run reaches a process once: a failing callback is logged, the request goes on, nothing retries."""
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.internal import _runtime_id as runtime_impl
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    calls = []

    def on_refresh(new_id):
        calls.append(new_id)
        raise RuntimeError("identity refresh callback failed")

    def run_hook_environ():
        return {"REQUEST_METHOD": "POST", "SCRIPT_NAME": "", "PATH_INFO": MICROVM_RUN_HOOK_PATH}

    runtime.on_runtime_identity_refresh(on_refresh)
    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)
    runtime_id = runtime.get_runtime_id()

    trace_utils.dispatch_wsgi_web_request_starting(run_hook_environ())

    refreshed_runtime_id = runtime.get_runtime_id()
    assert refreshed_runtime_id != runtime_id
    assert calls == [refreshed_runtime_id]
    assert runtime_impl._IDENTITY_REFRESH_HOOK_REFRESHED.is_set()

    trace_utils.dispatch_wsgi_web_request_starting(run_hook_environ())

    assert runtime.get_runtime_id() == refreshed_runtime_id
    assert calls == [refreshed_runtime_id]


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_microvm_refresh_guard_resets_after_fork():
    """A forked child starts with a fresh guard, even if another thread held its lock at fork time.

    The parent never sees the child's /run: refresh is process-local, so sibling workers are not
    refreshed. Multi-worker app servers are out of scope for the MicroVM MVP.
    """
    import os
    import threading

    import ddtrace.internal._runtime_id as runtime_impl
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)
    parent_runtime_id = runtime.get_runtime_id()
    assert runtime_impl._IDENTITY_REFRESH_HOOK_REFRESHED.is_set()

    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_guard_lock():
        with runtime_impl._IDENTITY_REFRESH_HOOK_REFRESH_LOCK:
            lock_held.set()
            release_lock.wait()

    holder = threading.Thread(target=hold_guard_lock)
    holder.start()
    assert lock_held.wait(timeout=5)

    pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            assert runtime_impl._IDENTITY_REFRESH_HOOK_REFRESH_LOCK.acquire(False)
            runtime_impl._IDENTITY_REFRESH_HOOK_REFRESH_LOCK.release()
            assert not runtime_impl._IDENTITY_REFRESH_HOOK_REFRESHED.is_set()
            child_runtime_id = runtime.get_runtime_id()
            assert child_runtime_id != parent_runtime_id

            runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)
            refreshed_child_runtime_id = runtime.get_runtime_id()
            assert refreshed_child_runtime_id != child_runtime_id

            runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)
            assert runtime.get_runtime_id() == refreshed_child_runtime_id
            exit_code = 0
        finally:
            os._exit(exit_code)

    release_lock.set()
    holder.join()
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    assert runtime_impl._IDENTITY_REFRESH_HOOK_REFRESHED.is_set()
    assert runtime.get_runtime_id() == parent_runtime_id


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_maybe_refresh_identity_ignores_other_requests():
    """A different method/path, or the /resume hook, must not trigger a refresh."""
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    runtime_id = runtime.get_runtime_id()

    runtime.maybe_refresh_identity("GET", MICROVM_RUN_HOOK_PATH)
    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, "/aws/lambda-microvms/runtime/v1/resume")
    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, "/some/other/path")
    runtime.maybe_refresh_identity(None, MICROVM_RUN_HOOK_PATH)
    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, None)

    assert runtime.get_runtime_id() == runtime_id


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None}, err=None)
def test_maybe_refresh_identity_noop_outside_microvm():
    """Direct identity refresh calls are ignored outside a MicroVM."""
    from ddtrace.internal import _runtime_id as runtime_impl
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    runtime_id = runtime.get_runtime_id()
    runtime.maybe_refresh_identity(MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)

    assert runtime.get_runtime_id() == runtime_id
    assert not runtime_impl._IDENTITY_REFRESH_HOOK_REFRESHED.is_set()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None}, err=None)
def test_identity_refresh_listener_noop_outside_microvm():
    """Outside a MicroVM, the request-event listener does not refresh identity."""
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    core.reset_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value)
    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)

    runtime_id = runtime.get_runtime_id()

    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH))

    assert runtime.get_runtime_id() == runtime_id


@pytest.mark.parametrize("microvm_image_arn", ["", "   "])
def test_identity_refresh_listener_noop_for_blank_microvm_env(run_python_code_in_subprocess, microvm_image_arn):
    """Blank MicroVM image ARN env values must not enable identity refresh."""
    code = """
from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core
import ddtrace.internal.runtime as runtime
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

core.reset_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value)
core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)

runtime_id = runtime.get_runtime_id()
core.dispatch(
    WebFrameworkEvents.WEB_REQUEST_STARTING.value, (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH)
)

assert runtime.get_runtime_id() == runtime_id
"""
    env = os.environ.copy()
    env["AWS_LAMBDA_MICROVM_IMAGE_ARN"] = microvm_image_arn
    _, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err


def test_refresh_identity_notifies_refresh_subscribers(run_python_code_in_subprocess):
    code = """
from ddtrace.internal import runtime

seen = []


class _Subscriber:
    def on_refresh(self, new_id):
        seen.append(new_id)


subscriber = _Subscriber()
runtime.on_runtime_identity_refresh(subscriber.on_refresh)

runtime.refresh_identity()

assert seen == [runtime.get_runtime_id()]
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err


def test_refresh_identity_callback_failure_does_not_block_other_refresh_callbacks(
    run_python_code_in_subprocess,
):
    code = """
from ddtrace.internal import runtime

seen = []


def on_refresh_raises(new_id):
    seen.append(("raises", new_id))
    raise RuntimeError("refresh callback failed")


def on_refresh(new_id):
    seen.append(("refresh", new_id))


runtime.on_runtime_identity_refresh(on_refresh_raises)
runtime.on_runtime_identity_refresh(on_refresh)
runtime.refresh_identity()

runtime_id = runtime.get_runtime_id()
assert ("raises", runtime_id) in seen
assert ("refresh", runtime_id) in seen
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err


def test_refresh_identity_raise_on_error_notifies_successful_callback(run_python_code_in_subprocess):
    code = """
from ddtrace.internal import runtime

seen = []


def on_refresh(new_id):
    seen.append(new_id)


runtime.on_runtime_identity_refresh(on_refresh)
runtime.refresh_identity(raise_on_error=True)

assert seen == [runtime.get_runtime_id()]
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err


def test_refresh_identity_propagates_refresh_callback_failure(run_python_code_in_subprocess):
    code = """
from ddtrace.internal import runtime

old_runtime_id = runtime.get_runtime_id()
ordinary_callbacks = []


def on_change(new_id):
    ordinary_callbacks.append(new_id)


def on_refresh(new_id):
    raise RuntimeError("refresh callback failed")


runtime.on_runtime_id_change(on_change)
runtime.on_runtime_identity_refresh(on_refresh)

try:
    runtime.refresh_identity(raise_on_error=True)
except RuntimeError as exc:
    assert str(exc) == "refresh callback failed"
else:
    raise AssertionError("refresh_identity() did not propagate the callback failure")

new_runtime_id = runtime.get_runtime_id()
assert new_runtime_id != old_runtime_id
assert ordinary_callbacks == [new_runtime_id]
"""
    _, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err
