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
def test_import_ddtrace_in_microvm_wires_run_hook_to_tracer():
    """Importing ddtrace in a MicroVM registers the /run listener, and /run refreshes the tracer."""
    from ddtrace import tracer
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    assert core.has_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value)
    old_writer = tracer._span_aggregator.writer
    runtime_id = runtime.get_runtime_id()

    trace_utils.dispatch_wsgi_web_request_starting(
        {"REQUEST_METHOD": "POST", "SCRIPT_NAME": "", "PATH_INFO": MICROVM_RUN_HOOK_PATH}
    )

    refreshed_runtime_id = runtime.get_runtime_id()
    assert refreshed_runtime_id != runtime_id
    assert tracer._span_aggregator.writer is not old_writer
    with tracer.trace("web.request") as span:
        assert span.get_tag("runtime-id") == refreshed_runtime_id


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None}, err=None)
def test_import_ddtrace_outside_microvm_does_not_register_run_hook():
    """Outside a MicroVM, importing the tracer leaves no request-start listener installed."""
    from ddtrace import tracer  # noqa: F401
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.internal import core

    assert not core.has_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value)


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


def test_tracer_microvm_identity_refresh_recreates_exporter_without_fork_side_effects():
    """Tracer MicroVM refresh must not reuse fork recreation semantics."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()

    try:
        tracer._post_fork_writer_pending = True
        with (
            mock.patch.object(tracer, "_recreate") as recreate,
            mock.patch.object(tracer, "_store_metadata") as store_metadata,
        ):
            tracer._refresh_runtime_identity(runtime.get_runtime_id())

        recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
        store_metadata.assert_called_once_with()
        assert tracer._post_fork_writer_pending is False
        assert tracer._new_process is False
    finally:
        tracer.shutdown()


def test_tracer_microvm_identity_refresh_after_shutdown_does_not_recreate_writer():
    """A /run after shutdown must not build a writer that nothing will stop."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()
    tracer.shutdown()

    with (
        mock.patch.object(tracer, "_recreate") as recreate,
        mock.patch.object(tracer, "_store_metadata") as store_metadata,
    ):
        tracer._refresh_runtime_identity(runtime.get_runtime_id())

    recreate.assert_not_called()
    store_metadata.assert_not_called()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_keeps_pending_after_recreate_failure():
    from unittest import mock

    import pytest

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()

    try:
        tracer._post_fork_writer_pending = True
        with (
            mock.patch.object(tracer, "_recreate", side_effect=RuntimeError("recreate failed")) as recreate,
            mock.patch.object(tracer, "_store_metadata") as store_metadata,
            pytest.raises(RuntimeError),
        ):
            tracer._refresh_runtime_identity(runtime.get_runtime_id())

        recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
        # The deferred rebuild does not store metadata, so the refresh must have stored it already.
        store_metadata.assert_called_once_with()
        assert tracer._post_fork_writer_pending is True
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_schedules_rebuild_after_recreate_failure():
    """/run is not repeated, so a failed refresh rebuild must leave a rebuild pending for the next span."""
    from unittest import mock

    import pytest

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    class PanicException(BaseException):
        # PyO3 raises Rust panics as a BaseException subclass, which "except Exception" would miss.
        pass

    # is_panic_exception() matches on the module and class name of pyo3_runtime.PanicException.
    PanicException.__module__ = "pyo3_runtime"

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()

    try:
        for error, propagates in (
            (RuntimeError("recreate failed"), True),
            (KeyboardInterrupt(), True),
            # The refresh dispatcher only contains Exception, so a panic must not escape into /run.
            (PanicException("exporter build panicked"), False),
        ):
            tracer._post_fork_writer_pending = False
            with (
                mock.patch.object(tracer, "_recreate", side_effect=error),
                mock.patch.object(tracer, "_store_metadata") as store_metadata,
            ):
                if propagates:
                    with pytest.raises(type(error)):
                        tracer._refresh_runtime_identity(runtime.get_runtime_id())
                else:
                    tracer._refresh_runtime_identity(runtime.get_runtime_id())

            store_metadata.assert_called_once_with()
            assert tracer._post_fork_writer_pending is True
    finally:
        tracer._post_fork_writer_pending = False
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_identity_refresh_contains_native_panic_from_writer_rebuild():
    """A Rust panic while rebuilding the writer must not escape refresh_identity() into the /run request."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    import ddtrace.internal._runtime_id as runtime_impl

    class PanicException(BaseException):
        pass

    PanicException.__module__ = "pyo3_runtime"

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()
    notified = []
    runtime_impl._ON_RUNTIME_IDENTITY_REFRESH.clear()
    runtime_impl.on_runtime_identity_refresh(tracer._refresh_runtime_identity)
    runtime_impl.on_runtime_identity_refresh(notified.append)

    try:
        with (
            mock.patch.object(tracer, "_recreate", side_effect=PanicException("exporter build panicked")),
            mock.patch.object(tracer, "_store_metadata"),
        ):
            runtime_impl.refresh_identity()

        assert notified == [runtime_impl.get_runtime_id()]
        assert tracer._post_fork_writer_pending is True
    finally:
        tracer._post_fork_writer_pending = False
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_shutdown_accepts_negative_timeout():
    """Lock.acquire() rejects negative timeouts, but shutdown() passed them through before the bounded wait."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()

    tracer.shutdown(timeout=-2)

    assert tracer._shutting_down is True
    assert tracer.start_span == tracer._start_span_after_shutdown


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_retries_failed_writer_recreation_on_next_span():
    """A refresh whose writer rebuild fails must not leave the closed writer installed for good."""
    from unittest import mock

    import pytest

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class FlakyWriter:
        recreate_calls = 0

        def __init__(self):
            self.traces = []
            self.closed = False

        def write(self, spans):
            # Mirrors the write gate of a writer closed by refresh.
            if not self.closed:
                self.traces.append(spans)

        def _stop_accepting_writes(self):
            self.closed = True

        def recreate(self, **kwargs):
            FlakyWriter.recreate_calls += 1
            if FlakyWriter.recreate_calls == 1:
                raise RuntimeError("exporter build failed")
            return FlakyWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=lambda **kwargs: FlakyWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    aggregator = tracer._span_aggregator
    old_writer = aggregator.writer

    try:
        runtime_impl._refresh_runtime_id()
        with (
            mock.patch("ddtrace._trace.tracer.PyTracerMetadata") as tracer_metadata,
            mock.patch("ddtrace._trace.tracer.store_metadata") as store_metadata,
        ):
            with pytest.raises(RuntimeError):
                tracer._refresh_runtime_identity(runtime.get_runtime_id())

            assert aggregator.writer is old_writer
            assert old_writer.closed
            assert tracer._post_fork_writer_pending is True

            tracer.start_span("after-failed-refresh").finish()

        new_writer = aggregator.writer
        assert FlakyWriter.recreate_calls == 2
        assert new_writer is not old_writer
        assert tracer._post_fork_writer_pending is False
        assert [[span.name for span in trace] for trace in new_writer.traces] == [["after-failed-refresh"]]
        assert old_writer.traces == []
        # The recovered exporter and the stored metadata must agree on the refreshed runtime ID.
        store_metadata.assert_called_once_with(tracer_metadata.return_value)
        assert tracer_metadata.call_args.kwargs["runtime_id"] == runtime.get_runtime_id()
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_identity_refresh_hook_notifies_global_tracer():
    """The global tracer must rebuild its identity-bound state on the MicroVM /run hook."""
    from unittest import mock

    from ddtrace import tracer
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    # The /run listener is registered at import once the full refresh stack is in place.
    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)
    with (
        mock.patch.object(tracer, "_recreate") as recreate,
        mock.patch.object(tracer, "_store_metadata") as store_metadata,
    ):
        core.dispatch(
            WebFrameworkEvents.WEB_REQUEST_STARTING.value,
            (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH),
        )

    recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
    store_metadata.assert_called_once_with()


@pytest.mark.subprocess(
    env={
        "AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12",
        "_DD_GLOBAL_TRACER_INIT": "false",
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
    },
    err=None,
)
def test_deferred_global_tracer_registers_identity_refresh_callback():
    """An explicitly imported global tracer must refresh after deferred initialization."""
    from unittest import mock

    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH
    from ddtrace.trace import tracer

    # The /run listener is registered at import once the full refresh stack is in place.
    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)
    with (
        mock.patch.object(tracer, "_recreate") as recreate,
        mock.patch.object(tracer, "_store_metadata") as store_metadata,
    ):
        core.dispatch(
            WebFrameworkEvents.WEB_REQUEST_STARTING.value,
            (MICROVM_RUN_HOOK_METHOD, MICROVM_RUN_HOOK_PATH),
        )

    recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
    store_metadata.assert_called_once_with()


@pytest.mark.subprocess(
    env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None, "_DD_GLOBAL_TRACER_INIT": "false"},
    err=None,
)
def test_deferred_global_tracer_does_not_register_identity_refresh_callback_outside_microvm():
    """Deferred global tracer initialization must not change non-MicroVM refresh behavior."""
    from unittest import mock

    import ddtrace.internal.runtime as runtime
    from ddtrace.trace import tracer

    with mock.patch.object(tracer, "_recreate") as recreate:
        runtime.refresh_identity()

    recreate.assert_not_called()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_closes_and_flushes_old_writer():
    """The refresh sends what the old writer buffered, then the old writer rejects later spans."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    import ddtrace.internal._runtime_id as runtime_impl
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.internal.writer import NativeWriter
    from ddtrace.trace import Span

    old_writer = NativeWriter("http://dne:1234", processing_interval=10)
    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=old_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    runtime_impl.on_runtime_identity_refresh(tracer._refresh_runtime_identity)

    real_exporter = old_writer._exporter
    exporter = old_writer._exporter = mock.Mock(send=mock.Mock(return_value="{}"))
    try:
        old_writer._clients[0].encoder.put([Span("buffered")])
        old_writer.start()

        runtime_impl.refresh_identity()

        assert tracer._span_aggregator.writer is not old_writer
        assert old_writer._accepting_writes is False
        # The old writer is stopped, not discarded: it sends its buffer and shuts its exporter down.
        assert exporter.send.call_count == 1
        exporter.shutdown.assert_called_once()

        # A late span neither lands in the old writer's buffer nor restarts the stopped writer.
        old_writer.write([Span("late")])
        assert old_writer.status == ServiceStatus.STOPPED
        assert len(old_writer._clients[0].encoder) == 0
        assert exporter.send.call_count == 1
    finally:
        real_exporter.drop()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_shutdown_waits_for_in_flight_identity_refresh():
    """Shutdown during a refresh must stop the writer the refresh installs, not leak it."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    recreate_entered = threading.Event()
    release_recreate = threading.Event()

    class BlockingWriter:
        def __init__(self, block_recreate=False):
            self.block_recreate = block_recreate
            self.stopped = False

        def write(self, spans):
            pass

        def _stop_accepting_writes(self):
            pass

        def recreate(self, **kwargs):
            if self.block_recreate:
                recreate_entered.set()
                release_recreate.wait()
            return BlockingWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            self.stopped = True

    with (
        mock.patch(
            "ddtrace._trace.processor.create_trace_writer",
            side_effect=lambda **kwargs: BlockingWriter(block_recreate=True),
        ),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    old_writer = tracer._span_aggregator.writer

    refresh = threading.Thread(target=tracer._refresh_runtime_identity, args=(runtime.get_runtime_id(),), daemon=True)
    refresh.start()
    assert recreate_entered.wait(5)

    shutdown = threading.Thread(target=tracer.shutdown, daemon=True)
    shutdown.start()
    shutdown.join(0.2)
    # Stopping the old writer now would leave the replacement the refresh is about to install running.
    assert shutdown.is_alive()
    assert not old_writer.stopped

    release_recreate.set()
    refresh.join(5)
    shutdown.join(5)
    assert not refresh.is_alive()
    assert not shutdown.is_alive()

    new_writer = tracer._span_aggregator.writer
    assert new_writer is not old_writer
    assert new_writer.stopped


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_timed_out_shutdown_stops_writer_from_retried_refresh_rebuild():
    """A shutdown(timeout) that stops waiting while a failed refresh's rebuild is retried must not leak it."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer

    recreate_entered = threading.Event()
    release_recreate = threading.Event()

    class BlockingWriter:
        def __init__(self, block_recreate=False):
            self.block_recreate = block_recreate
            self.stopped = False

        def write(self, spans):
            pass

        def _stop_accepting_writes(self):
            pass

        def recreate(self, **kwargs):
            if self.block_recreate:
                recreate_entered.set()
                release_recreate.wait()
            return BlockingWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            self.stopped = True

    with (
        mock.patch(
            "ddtrace._trace.processor.create_trace_writer",
            side_effect=lambda **kwargs: BlockingWriter(block_recreate=True),
        ),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    old_writer = tracer._span_aggregator.writer
    # What a refresh whose rebuild failed leaves behind: the next span start retries the rebuild.
    tracer._post_fork_writer_pending = True

    retry = threading.Thread(target=tracer._ensure_post_fork_writer, daemon=True)
    retry.start()
    assert recreate_entered.wait(5)

    # In a MicroVM a timed-out wait for the lock lets shutdown finish while the retry is still rebuilding.
    tracer.shutdown(timeout=0)
    assert old_writer.stopped

    release_recreate.set()
    retry.join(5)
    assert not retry.is_alive()

    new_writer = tracer._span_aggregator.writer
    assert new_writer is not old_writer
    assert new_writer.stopped


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None}, err=None)
def test_tracer_post_fork_rebuild_ignores_shutdown_flag_outside_microvm():
    """Outside MicroVMs shutdown() waits for the post-fork rebuild, so the rebuild keeps its behavior there."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()
    tracer._post_fork_writer_pending = True
    tracer._shutting_down = True

    with (
        mock.patch.object(tracer, "_recreate") as recreate,
        mock.patch.object(tracer, "_stop_replacement_writer") as stop_replacement_writer,
    ):
        rebuilt = tracer._ensure_post_fork_writer()

    assert rebuilt
    recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
    stop_replacement_writer.assert_not_called()
    assert not tracer._post_fork_writer_pending


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_failed_rebuild_retry_is_contained_in_start_span():
    """A refresh rebuild that keeps failing must not raise from the app's span start, and stays pending."""
    from unittest import mock

    import pytest

    from ddtrace._trace.tracer import Tracer

    class PanicException(BaseException):
        # PyO3 raises Rust panics as a BaseException subclass, which "except Exception" would miss.
        pass

    # is_panic_exception() matches on the module and class name of pyo3_runtime.PanicException.
    PanicException.__module__ = "pyo3_runtime"

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()

    try:
        for error in (RuntimeError("recreate failed"), PanicException("exporter build panicked")):
            # What a refresh whose rebuild failed leaves behind.
            tracer._post_fork_writer_pending = True
            with mock.patch.object(tracer, "_recreate", side_effect=error) as recreate:
                tracer.start_span("contained").finish()
            recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
            assert tracer._post_fork_writer_pending is True

        # Interrupts are not rebuild failures and still propagate.
        with mock.patch.object(tracer, "_recreate", side_effect=KeyboardInterrupt()):
            with pytest.raises(KeyboardInterrupt):
                tracer.start_span("interrupted")
        assert tracer._post_fork_writer_pending is True

        with mock.patch.object(tracer, "_recreate") as recreate:
            tracer.start_span("recovered").finish()
        recreate.assert_called_once_with(reset_buffer=False, flush_writer=False)
        assert tracer._post_fork_writer_pending is False
    finally:
        tracer._post_fork_writer_pending = False
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": None}, err=None)
def test_tracer_post_fork_rebuild_failure_still_raises_outside_microvm():
    """Outside MicroVMs a failed post-fork rebuild still raises from the span start, as before."""
    from unittest import mock

    import pytest

    from ddtrace._trace.tracer import Tracer

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()
    tracer._post_fork_writer_pending = True

    try:
        with mock.patch.object(tracer, "_recreate", side_effect=RuntimeError("recreate failed")):
            with pytest.raises(RuntimeError):
                tracer.start_span("post-fork")
        assert tracer._post_fork_writer_pending is True
    finally:
        tracer._post_fork_writer_pending = False
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_identity_refresh_during_shutdown_does_not_recreate_writer():
    """A /run that lands while shutdown stops the writer must not build a replacement nothing stops."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    with mock.patch("ddtrace._trace.tracer.store_metadata"):
        tracer = Tracer()
    aggregator = tracer._span_aggregator
    stop_aggregator = aggregator.shutdown

    def shutdown_with_concurrent_refresh(timeout):
        # The refresh lands after shutdown began but before it marks start_span as shut down.
        refresh = threading.Thread(
            target=tracer._refresh_runtime_identity, args=(runtime.get_runtime_id(),), daemon=True
        )
        refresh.start()
        refresh.join(5)
        assert not refresh.is_alive()
        stop_aggregator(timeout)

    with (
        mock.patch.object(tracer, "_recreate") as recreate,
        mock.patch.object(aggregator, "shutdown", side_effect=shutdown_with_concurrent_refresh),
    ):
        tracer.shutdown()

    recreate.assert_not_called()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_shutdown_on_refreshing_thread_stops_replacement_writer():
    """An exit-signal handler can run shutdown() mid-refresh on the same thread and then return."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.internal.service import ServiceStatusError

    tracers = []

    class ObservableWriter:
        def __init__(self, started):
            self.started = started
            self.stopped = False
            self.exporter_shut_down = False

        def write(self, spans):
            pass

        def _stop_accepting_writes(self):
            pass

        def recreate(self, **kwargs):
            if tracers:
                # The handler runs shutdown() before the replacement is installed, then returns.
                tracers[-1].shutdown()
            return ObservableWriter(started=replacement_started)

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            if not self.started:
                # Like a NativeWriter no span has started yet: stop() raises, but the exporter is live.
                raise ServiceStatusError(ObservableWriter, ServiceStatus.STOPPED)
            self.stopped = True

        def shutdown_exporter(self):
            self.exporter_shut_down = True

    for replacement_started in (True, False):
        with (
            mock.patch(
                "ddtrace._trace.processor.create_trace_writer",
                side_effect=lambda **kwargs: ObservableWriter(started=True),
            ),
            mock.patch("ddtrace._trace.tracer.store_metadata"),
        ):
            tracer = Tracer()
        tracers.append(tracer)
        old_writer = tracer._span_aggregator.writer

        refresh = threading.Thread(
            target=tracer._refresh_runtime_identity, args=(runtime.get_runtime_id(),), daemon=True
        )
        refresh.start()
        refresh.join(5)

        assert not refresh.is_alive()
        assert tracer.start_span == tracer._start_span_after_shutdown
        new_writer = tracer._span_aggregator.writer
        assert new_writer is not old_writer
        assert old_writer.stopped
        if replacement_started:
            assert new_writer.stopped
        else:
            assert new_writer.exporter_shut_down


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_signal_shutdown_does_not_deadlock_with_refresh_waiting_on_writer_lock():
    """An exit-signal handler can run shutdown() on a thread holding the writer lock a refresh waits on."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime

    refresh_waiting = threading.Event()

    class LockedWriter:
        def __init__(self):
            self._writer_lock = threading.RLock()
            self.stopped = False

        def write(self, spans):
            pass

        def _stop_accepting_writes(self):
            # The refresh calls this while holding the tracer's _post_fork_lock.
            refresh_waiting.set()
            with self._writer_lock:
                pass

        def recreate(self, **kwargs):
            return LockedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            self.stopped = True

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=lambda **kwargs: LockedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    old_writer = tracer._span_aggregator.writer
    refresh = threading.Thread(target=tracer._refresh_runtime_identity, args=(runtime.get_runtime_id(),), daemon=True)

    def write_interrupted_by_exit_signal():
        # The signal arrives inside write(), so the handler's shutdown() runs before the writer lock is released.
        with old_writer._writer_lock:
            refresh.start()
            assert refresh_waiting.wait(5)
            tracer.shutdown(timeout=0.1)

    writer_thread = threading.Thread(target=write_interrupted_by_exit_signal, daemon=True)
    writer_thread.start()
    writer_thread.join(5)
    assert not writer_thread.is_alive()
    refresh.join(5)
    assert not refresh.is_alive()

    new_writer = tracer._span_aggregator.writer
    assert new_writer is not old_writer
    assert old_writer.stopped
    # The refresh outlived shutdown's bounded wait, so it stops the writer it installed.
    assert new_writer.stopped
