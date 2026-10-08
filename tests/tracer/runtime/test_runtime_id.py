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
    runtime_id = runtime.get_runtime_id()

    trace_utils.dispatch_wsgi_web_request_starting(
        {"REQUEST_METHOD": "POST", "SCRIPT_NAME": "", "PATH_INFO": MICROVM_RUN_HOOK_PATH}
    )

    refreshed_runtime_id = runtime.get_runtime_id()
    assert refreshed_runtime_id != runtime_id
    assert tracer._span_aggregator._runtime_identity[1] == refreshed_runtime_id
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


def test_span_aggregator_lock_resets_after_fork():
    """A MicroVM child must not inherit a permanently locked aggregator transition lock."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import forksafe

    with (
        mock.patch("ddtrace._trace.tracer.store_metadata"),
        mock.patch("ddtrace._trace.processor.in_aws_lambda_microvm", return_value=True),
    ):
        tracer = Tracer()

    try:
        lock = tracer._span_aggregator._lock
        assert isinstance(lock, forksafe.ResetObject)

        holder = threading.Thread(target=lock.acquire)
        holder.start()
        holder.join(timeout=2)
        assert not holder.is_alive()

        lock._reset_object()
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        tracer.shutdown()


def test_span_aggregator_outside_microvm_keeps_plain_lock_and_no_identity():
    """Outside a MicroVM the aggregator keeps its plain lock and does no identity bookkeeping."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import forksafe

    with (
        mock.patch("ddtrace._trace.tracer.store_metadata"),
        mock.patch("ddtrace._trace.processor.in_aws_lambda_microvm", return_value=False),
    ):
        tracer = Tracer()

    try:
        assert not isinstance(tracer._span_aggregator._lock, forksafe.ResetObject)
        assert tracer._span_aggregator._runtime_identity is None
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_root_span_runtime_id_matches_writer_identity_before_tracer_refresh():
    """A span started after the ID rotated but before the tracer refreshed keeps the writer's identity.

    Without a shared refresh lock, the tag comes from the same published value as the generation, so
    the span is never tagged with the new ID while it is headed for the old writer.
    """
    from unittest import mock

    from ddtrace import tracer
    import ddtrace.internal._runtime_id as runtime_impl
    import ddtrace.internal.runtime as runtime

    aggregator = tracer._span_aggregator
    generation, writer_runtime_id = aggregator._runtime_identity
    written = []

    # Rotate the ID without running refresh callbacks, as if the tracer callback had not run yet.
    runtime_impl._refresh_runtime_id()
    assert runtime.get_runtime_id() != writer_runtime_id

    with mock.patch.object(aggregator, "_write_if_identity_generation_is_current") as write:
        span = tracer.trace("before.tracer.refresh")
        assert span.get_tag("runtime-id") == writer_runtime_id

        tracer._refresh_runtime_identity(runtime.get_runtime_id())
        assert aggregator._runtime_identity == (generation + 1, runtime.get_runtime_id())

        span.finish()
        written.extend(write.call_args_list)

    assert written == []

    with tracer.trace("after.tracer.refresh") as span:
        assert span.get_tag("runtime-id") == runtime.get_runtime_id()


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

        recreate.assert_called_once_with(reset_buffer=True, drop_buffered_traces=True)
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
def test_tracer_microvm_identity_refresh_serializes_writer_recreation_with_configure():
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    recreate_started = threading.Event()
    release_recreate = threading.Event()
    writers = []

    class Writer:
        def __init__(self, block_recreate=False):
            self.runtime_id = runtime.get_runtime_id()
            self.block_recreate = block_recreate
            writers.append(self)

        def flush_queue(self):
            pass

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            writer = Writer()
            if self.block_recreate:
                recreate_started.set()
                assert release_recreate.wait(timeout=5)
            return writer

        def stop(self, timeout=None):
            pass

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=lambda **kwargs: Writer(True)),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    try:
        configure_thread = threading.Thread(target=lambda: tracer.configure(appsec_enabled=True))
        configure_thread.start()
        assert recreate_started.wait(timeout=5)

        runtime_impl._refresh_runtime_id()
        refresh_thread = threading.Thread(target=lambda: tracer._refresh_runtime_identity(runtime.get_runtime_id()))
        refresh_thread.start()

        release_recreate.set()
        configure_thread.join(timeout=5)
        refresh_thread.join(timeout=5)

        assert not configure_thread.is_alive()
        assert not refresh_thread.is_alive()
        assert tracer._span_aggregator.writer.runtime_id == runtime.get_runtime_id()
        assert tracer._span_aggregator.writer is writers[-1]
    finally:
        release_recreate.set()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_send_stuck_in_retries_does_not_block_spans_or_refresh():
    """A send stuck in retries must not block tracing on other threads or the /run refresh.

    Span finishes write under the span aggregator lock, and span starts and the refresh take that
    same lock, so the writer must never hold a lock they wait on across send().
    """
    import threading
    import time
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    import ddtrace.internal._runtime_id as runtime_impl
    from ddtrace.internal.writer import NativeWriter
    from ddtrace.trace import Span

    old_writer = NativeWriter("http://dne:1234", processing_interval=0.01)
    old_writer._clients[0].encoder.put([Span("in-flight")])
    send_started = threading.Event()
    release_send = threading.Event()

    def stuck_send(payload):
        send_started.set()
        assert release_send.wait(timeout=10)
        return "{}"

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=old_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    runtime_impl.on_runtime_identity_refresh(tracer._refresh_runtime_identity)

    # Stub the exporter itself so the stuck send holds the exporter lock, as a real one does.
    real_exporter = old_writer._exporter
    stuck_exporter = old_writer._exporter = mock.Mock(send=mock.Mock(side_effect=stuck_send))
    try:
        old_writer.start()
        assert send_started.wait(timeout=5)

        background = threading.Thread(target=lambda: tracer.trace("background").finish())
        background.start()
        background.join(timeout=2)
        assert not background.is_alive()

        started = time.monotonic()
        tracer.trace("unrelated").finish()
        assert time.monotonic() - started < 1

        started = time.monotonic()
        runtime_impl.refresh_identity()
        assert time.monotonic() - started < 1
        assert tracer._span_aggregator.writer is not old_writer
        assert old_writer._accepting_writes is False
        # The old writer is still inside its send, so its own thread finishes the discard.
        assert old_writer._exporter_dropped is False

        release_send.set()
        old_writer.join(timeout=5)

        assert old_writer._exporter_dropped is True
        stuck_exporter.drop.assert_called_once_with()
        # The spans buffered during the stall were discarded with the old writer.
        assert stuck_exporter.send.call_count == 1
    finally:
        release_send.set()
        real_exporter.drop()
        tracer.shutdown()


@pytest.mark.subprocess(
    env={
        "AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12",
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true",
    },
    err=None,
)
def test_tracer_microvm_refresh_with_telemetry_does_not_wait_for_stuck_send():
    """The telemetry refresh notifies the trace writer of its rebuilt worker on the /run thread.

    Re-pointing the exporter takes the exporter lock, which a send holds across its retries, so it
    must not make the refresh wait for that send.
    """
    import threading
    import time
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    import ddtrace.internal._runtime_id as runtime_impl
    from ddtrace.internal.writer import NativeWriter
    from ddtrace.trace import Span

    old_writer = NativeWriter("http://dne:1234", processing_interval=0.01)
    assert old_writer._telemetry_worker_subscribed
    old_writer._clients[0].encoder.put([Span("in-flight")])
    send_started = threading.Event()
    release_send = threading.Event()

    def stuck_send(payload):
        send_started.set()
        assert release_send.wait(timeout=10)
        return "{}"

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=old_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    runtime_impl.on_runtime_identity_refresh(tracer._refresh_runtime_identity)

    real_exporter = old_writer._exporter
    stuck_exporter = old_writer._exporter = mock.Mock(send=mock.Mock(side_effect=stuck_send))
    try:
        old_writer.start()
        assert send_started.wait(timeout=5)

        started = time.monotonic()
        runtime_impl.refresh_identity()
        assert time.monotonic() - started < 1
        assert tracer._span_aggregator.writer is not old_writer
        assert old_writer._accepting_writes is False

        release_send.set()
        old_writer.join(timeout=5)

        assert old_writer._exporter_dropped is True
        stuck_exporter.drop.assert_called_once_with()
        assert stuck_exporter.send.call_count == 1
    finally:
        release_send.set()
        real_exporter.drop()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_sync_send_stuck_in_retries_does_not_block_spans_or_refresh():
    """A sync-mode span finish stuck in send() must not block span starts or the /run refresh.

    The sync writer sends on the finishing thread and never starts its service, so the refresh's
    recreate() must not wait on the exporter lock, and the finishing thread drops the exporter.
    """
    import threading
    import time
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    import ddtrace.internal._runtime_id as runtime_impl
    from ddtrace.internal.writer import NativeWriter

    old_writer = NativeWriter("http://dne:1234", sync_mode=True)
    send_started = threading.Event()
    release_send = threading.Event()

    def stuck_send(payload):
        send_started.set()
        assert release_send.wait(timeout=10)
        return "{}"

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=old_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    runtime_impl.on_runtime_identity_refresh(tracer._refresh_runtime_identity)

    real_exporter = old_writer._exporter
    stuck_exporter = old_writer._exporter = mock.Mock(send=mock.Mock(side_effect=stuck_send))
    finishing = threading.Thread(target=lambda: tracer.trace("in-flight").finish())
    try:
        finishing.start()
        assert send_started.wait(timeout=5)

        started = time.monotonic()
        span = tracer.trace("unrelated")
        assert time.monotonic() - started < 1

        started = time.monotonic()
        runtime_impl.refresh_identity()
        assert time.monotonic() - started < 1
        assert tracer._span_aggregator.writer is not old_writer
        assert old_writer._accepting_writes is False
        assert old_writer._exporter_dropped is False
        span.finish()

        release_send.set()
        finishing.join(timeout=5)

        assert not finishing.is_alive()
        assert old_writer._exporter_dropped is True
        stuck_exporter.drop.assert_called_once_with()
        stuck_exporter.shutdown.assert_not_called()
        assert stuck_exporter.send.call_count == 1
    finally:
        release_send.set()
        finishing.join(timeout=5)
        real_exporter.drop()
        tracer.shutdown()


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

        recreate.assert_called_once_with(reset_buffer=True, drop_buffered_traces=True)
        store_metadata.assert_not_called()
        assert tracer._post_fork_writer_pending is True
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

    recreate.assert_called_once_with(reset_buffer=True, drop_buffered_traces=True)
    store_metadata.assert_called_once_with()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_asgi_run_hook_keeps_distributed_parent_after_tracer_refresh():
    """The tracer refresh detaches the active context, so ASGI must dispatch the /run hook before
    activating the request's distributed headers: the request span keeps its upstream parent, and
    the request context is activated once rather than activated, detached, and activated again.
    """
    import asyncio
    from unittest import mock

    from ddtrace import tracer
    from ddtrace.contrib._events.web_framework import WebFrameworkEvents
    from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
    from ddtrace.internal import core
    import ddtrace.internal.runtime as runtime
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
    from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH

    request_spans = []
    activated_contexts = []

    async def app(scope, receive, send):
        request_spans.append(tracer.current_root_span())
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    scope = {
        "type": "http",
        "method": MICROVM_RUN_HOOK_METHOD,
        "path": MICROVM_RUN_HOOK_PATH,
        "headers": [(b"x-datadog-trace-id", b"1234"), (b"x-datadog-parent-id", b"5678")],
        "query_string": b"",
        "scheme": "http",
        "client": ("127.0.0.1", 32767),
        "server": ("127.0.0.1", 80),
    }

    # The /run listener is registered at import once the full refresh stack is in place.
    core.on(WebFrameworkEvents.WEB_REQUEST_STARTING.value, runtime.maybe_refresh_identity)
    core.on("distributed_context.activated", activated_contexts.append)
    runtime_id = runtime.get_runtime_id()
    with mock.patch.object(tracer, "_recreate"), mock.patch.object(tracer, "_store_metadata"):
        asyncio.run(TraceMiddleware(app)(scope, receive, send))

    assert runtime.get_runtime_id() != runtime_id
    (span,) = request_spans
    assert span.trace_id == 1234
    assert span.parent_id == 5678
    assert len(activated_contexts) == 1


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

    recreate.assert_called_once_with(reset_buffer=True, drop_buffered_traces=True)
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


def test_tracer_microvm_identity_refresh_drops_direct_and_indirect_trace_buffers():
    """Identity refresh drops queued spans and active traces carrying the old runtime ID."""
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def __init__(self):
            self.traces = []
            self.dropped = False

        def write(self, spans):
            self.traces.append(spans)

        def drop_buffered_traces(self):
            self.traces.clear()
            self.dropped = True

        def recreate(self, **kwargs):
            self.recreate_kwargs = kwargs
            return BufferedWriter()

        def flush_queue(self):
            raise AssertionError("identity refresh must drop buffered traces, not flush them")

        def stop(self, timeout=None):
            pass

    writers = []

    def create_writer(**kwargs):
        writer = BufferedWriter()
        writers.append(writer)
        return writer

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=create_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    try:
        old_writer = writers[-1]
        queued_root = tracer.start_span("queued-root")
        old_runtime_id = queued_root.get_tag("runtime-id")
        queued_root.finish()

        active_root = tracer.start_span("active-root")
        active_child = tracer.start_span("active-child", child_of=active_root)
        active_child.finish()
        assert active_root.trace_id in tracer._span_aggregator._traces
        assert tracer._span_aggregator._traces[active_root.trace_id].spans == [active_root, active_child]
        assert old_writer.traces

        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())

        assert old_writer.dropped is True
        assert old_writer.traces == []
        assert tracer._span_aggregator._traces == {}

        refreshed_root = tracer.start_span("refreshed-root")
        assert refreshed_root.get_tag("runtime-id") != old_runtime_id
        refreshed_root.finish()
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_drops_inflight_old_trace():
    """A trace finishing across refresh must not enter the replacement writer."""
    import threading
    from unittest import mock

    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def __init__(self):
            self.traces = []
            self.dropped = False

        def write(self, spans):
            self.traces.append(spans)

        def drop_buffered_traces(self):
            self.traces.clear()
            self.dropped = True

        def recreate(self, **kwargs):
            self.recreate_kwargs = kwargs
            return BufferedWriter()

        def flush_queue(self):
            raise AssertionError("identity refresh must drop buffered traces, not flush them")

        def stop(self, timeout=None):
            pass

    writers = []

    def create_writer(**kwargs):
        writer = BufferedWriter()
        writers.append(writer)
        return writer

    processing_started = threading.Event()
    release_processing = threading.Event()
    side_effects = []

    class BlockingProcessor:
        def process_trace(self, spans):
            processing_started.set()
            assert release_processing.wait(timeout=5)
            return spans

    class SideEffectProcessor:
        def process_trace(self, spans):
            side_effects.append(spans)
            return spans

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=create_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    old_writer = writers[0]

    tracer._span_aggregator.dd_processors.extend([BlockingProcessor(), SideEffectProcessor()])
    try:
        span = tracer.start_span("old-runtime")
        finish_thread = threading.Thread(target=span.finish)
        finish_thread.start()
        assert processing_started.wait(timeout=5)

        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())

        new_writer = tracer._span_aggregator.writer
        release_processing.set()
        finish_thread.join(timeout=5)
        assert not finish_thread.is_alive()
        assert side_effects == []
        assert old_writer.traces == []
        assert new_writer.traces == []
    finally:
        release_processing.set()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_rejects_stale_completion_for_reused_trace_id():
    """A stale span must not complete a same-ID trace from the refreshed generation."""
    from unittest import mock

    from ddtrace._trace.span import Span
    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def __init__(self):
            self.traces = []

        def write(self, spans):
            self.traces.append(spans)

        def drop_buffered_traces(self):
            self.traces.clear()

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            raise AssertionError("identity refresh must drop buffered traces, not flush them")

        def stop(self, timeout=None):
            pass

    writers = []

    def create_writer(**kwargs):
        writer = BufferedWriter()
        writers.append(writer)
        return writer

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", side_effect=create_writer),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    try:
        stale_span = tracer.start_span("stale")
        trace_id = stale_span.trace_id

        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())
        new_writer = tracer._span_aggregator.writer

        current_span = Span("current", trace_id=trace_id, on_finish=[tracer._on_span_finish])
        tracer._span_aggregator.on_span_start(current_span)
        stale_span.finish()

        assert tracer._span_aggregator._traces[trace_id].spans == [current_span]
        assert new_writer.traces == []

        current_span.finish()
        assert new_writer.traces == [[current_span]]
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_rejects_stale_span_before_finish_callbacks():
    from unittest import mock

    from ddtrace._trace.processor import SpanProcessor
    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import core
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def write(self, spans):
            pass

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    finished_spans = []

    class RecordingSpanProcessor(SpanProcessor):
        def on_span_start(self, span):
            pass

        def on_span_finish(self, span):
            finished_spans.append(span)

    def on_span_finish(span):
        finished_spans.append(span)

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=BufferedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    span_processor = RecordingSpanProcessor()
    span_processor.register()
    core.on("trace.span_finish", on_span_finish)

    try:
        stale_span = tracer.start_span("stale")
        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())

        stale_span.finish()

        assert finished_spans == []
    finally:
        core.reset_listeners("trace.span_finish", on_span_finish)
        span_processor.unregister()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_records_generation_of_span_rejected_at_start():
    from unittest import mock

    from ddtrace._trace.context import _get_runtime_identity_generation
    from ddtrace._trace.processor import SpanProcessor
    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    written = []

    class BufferedWriter:
        def write(self, spans):
            written.extend(span.name for span in spans)

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=BufferedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    class RefreshOnStart(SpanProcessor):
        # Span processors run after start_span()'s generation check and before the aggregator's
        # on_span_start(), so refreshing here lands in the window between the two checks.
        def on_span_start(self, span):
            if span.name == "stale-root":
                runtime_impl._refresh_runtime_id()
                tracer._refresh_runtime_identity(runtime.get_runtime_id())

        def on_span_finish(self, span):
            pass

    stale_generation = tracer._span_aggregator._runtime_identity_generation
    span_processor = RefreshOnStart()
    span_processor.register()
    try:
        stale_root = tracer.start_span("stale-root")
        assert tracer._span_aggregator._runtime_identity_generation != stale_generation
        assert _get_runtime_identity_generation(stale_root.context) == stale_generation

        # A retained context of the rejected root must not parent a span into the new runtime.
        stale_child = tracer.start_span("stale-child", child_of=stale_root.context)
        stale_child.finish()
        stale_root.finish()
        fresh = tracer.start_span("fresh")
        fresh.finish()

        assert written == ["fresh"]
        assert tracer._span_aggregator._traces == {}
    finally:
        span_processor.unregister()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_records_generation_of_root_rejected_by_start_span():
    from unittest import mock

    from ddtrace._trace.context import _get_runtime_identity_generation
    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    written = []

    class BufferedWriter:
        def write(self, spans):
            written.extend(span.name for span in spans)

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=BufferedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    aggregator = tracer._span_aggregator
    is_current = aggregator._identity_generation_is_current
    refreshed = []

    def refresh_before_check(identity_generation):
        # Refresh between start_span()'s identity read and its generation check.
        if not refreshed:
            refreshed.append(True)
            runtime_impl._refresh_runtime_id()
            tracer._refresh_runtime_identity(runtime.get_runtime_id())
        return is_current(identity_generation)

    stale_generation = aggregator._runtime_identity_generation
    try:
        with mock.patch.object(aggregator, "_identity_generation_is_current", side_effect=refresh_before_check):
            stale_root = tracer.start_span("stale-root")
        assert aggregator._runtime_identity_generation != stale_generation
        assert _get_runtime_identity_generation(stale_root.context) == stale_generation

        # A retained context of the rejected root must not parent a span into the new runtime.
        stale_child = tracer.start_span("stale-child", child_of=stale_root.context)
        stale_child.finish()
        stale_root.finish()
        fresh = tracer.start_span("fresh")
        fresh.finish()

        assert written == ["fresh"]
    finally:
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_rejects_stale_context_before_start_callbacks():
    from unittest import mock

    from ddtrace._trace.processor import SpanProcessor
    from ddtrace._trace.tracer import Tracer
    from ddtrace.internal import core
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def write(self, spans):
            raise AssertionError("stale context spans must not be written")

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    started_spans = []

    class RecordingSpanProcessor(SpanProcessor):
        def on_span_start(self, span):
            started_spans.append(span)

        def on_span_finish(self, span):
            pass

    def on_span_start(span):
        started_spans.append(span)

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=BufferedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()
    span_processor = RecordingSpanProcessor()
    span_processor.register()
    core.on("trace.span_start", on_span_start)

    try:
        local_span = tracer.start_span("local")
        copied_local_context = local_span.context.copy(local_span.trace_id, local_span.span_id)
        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())
        started_spans.clear()

        stale_span = tracer.start_span("stale-child", child_of=copied_local_context, activate=True)

        assert started_spans == []
        assert tracer.context_provider.active() is None
        stale_span.finish()
        assert tracer._span_aggregator._traces == {}
    finally:
        core.reset_listeners("trace.span_start", on_span_start)
        span_processor.unregister()
        tracer.shutdown()


@pytest.mark.subprocess(env={"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1::runtime:python3.12"}, err=None)
def test_tracer_microvm_identity_refresh_detaches_inherited_contexts():
    from unittest import mock

    from ddtrace._trace.context import Context
    from ddtrace._trace.tracer import Tracer
    from ddtrace.contrib import trace_utils
    from ddtrace.internal import runtime
    import ddtrace.internal._runtime_id as runtime_impl

    class BufferedWriter:
        def __init__(self):
            self.traces = []

        def write(self, spans):
            self.traces.append(spans)

        def drop_buffered_traces(self):
            pass

        def recreate(self, **kwargs):
            return BufferedWriter()

        def flush_queue(self):
            pass

        def stop(self, timeout=None):
            pass

    with (
        mock.patch("ddtrace._trace.processor.create_trace_writer", return_value=BufferedWriter()),
        mock.patch("ddtrace._trace.tracer.store_metadata"),
    ):
        tracer = Tracer()

    try:
        local_span = tracer.start_span("local", activate=True)
        local_context = local_span.context
        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())
        assert tracer.context_provider.active() is None
        local_span.finish()

        copied_local_context = local_context.copy(local_context.trace_id, local_context.span_id)
        stale_child = tracer.start_span("stale-child", child_of=copied_local_context)
        stale_child.finish()
        assert tracer._span_aggregator._traces == {}

        remote_context = Context(trace_id=1, span_id=2)
        remote_context._is_remote = True
        tracer.context_provider.activate(remote_context)
        runtime_impl._refresh_runtime_id()
        tracer._refresh_runtime_identity(runtime.get_runtime_id())
        assert tracer.context_provider.active() is None

        stale_remote_span = tracer.start_span("stale-remote", child_of=remote_context)
        stale_remote_span.finish()
        assert tracer._span_aggregator._traces == {}

        trace_utils.activate_distributed_headers(
            tracer,
            request_headers={"x-datadog-trace-id": "3", "x-datadog-parent-id": "4"},
            override=True,
        )
        incoming_context = tracer.context_provider.active()
        assert incoming_context is not None
        assert incoming_context.trace_id == 3
        assert incoming_context.span_id == 4

        remote_span = tracer.trace("remote")
        assert remote_span.trace_id == 3
        assert remote_span.parent_id == 4
        remote_span.finish()

        tracer.context_provider.activate(None)
        root_span = tracer.start_span("new-root")
        assert root_span.trace_id != remote_context.trace_id
        root_span.finish()
    finally:
        tracer.shutdown()
