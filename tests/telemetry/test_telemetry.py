import os

import pytest


def _assert_ddtrace_stack_trace(stack_trace, *expected_fragments):
    """Assert a telemetry-log stack trace points at the ddtrace code path, across install layouts.

    Telemetry redacts frames that ``ddtrace.internal.packages.is_user_code`` classifies as user code
    (replacing them with ``<REDACTED>``); ddtrace's own frames are kept only when ddtrace is
    recognized as a third-party package. That recognition depends on the install layout:

    * pip-installed wheel (e.g. CI): ddtrace lives under ``site-packages`` and ``filename_to_package``
      maps its files to the ``ddtrace`` distribution, so it is third-party and the frames are kept --
      the concrete ddtrace function/source-line fragments (``expected_fragments``) appear verbatim.
    * editable / repo-checkout install (e.g. local dev): the source-tree path is not in any
      distribution's recorded file list, so ``filename_to_package`` returns ``None``, ddtrace looks
      like user code, and its frames are redacted to ``<REDACTED>``.

    Both are correct telemetry behavior, so accept either: the concrete fragments when frames are
    kept, or a properly-structured redacted traceback when they are not.
    """
    assert stack_trace, "expected a stack_trace in the telemetry error log"
    assert stack_trace.startswith("Traceback (most recent call last):"), stack_trace

    kept = all(fragment in stack_trace for fragment in expected_fragments)
    redacted = "<REDACTED>" in stack_trace
    assert kept or redacted, (
        "stack_trace neither contained the expected ddtrace frames %r (pip-installed layout) nor the "
        "redaction marker '<REDACTED>' (repo-checkout layout): %r" % (expected_fragments, stack_trace)
    )


@pytest.fixture(autouse=True)
def _no_inherited_api_key(monkeypatch):
    """Keep subprocess telemetry writers in non-agentless mode.

    A ``DD_API_KEY`` present in the test environment is inherited by the subprocesses these tests
    spawn (``os.environ.copy()``) and flips their telemetry writer into agentless mode, diverting
    requests to the Datadog intake instead of the local test agent. Tests that genuinely need an
    api key set it explicitly via the subprocess marker env, which overrides this removal.
    """
    monkeypatch.delenv("DD_API_KEY", raising=False)


def test_enable(test_agent_session, run_python_code_in_subprocess):
    code = """
import ddtrace # enables telemetry

from ddtrace.internal.telemetry import telemetry_writer
# The native telemetry worker is built once the writer is enabled; a non-None worker
# is the new "is the writer active" signal (PeriodicService.status is gone).
assert telemetry_writer._worker is not None
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    assert stdout == b"", stderr
    assert stderr == b""


def test_enable_with_short_heartbeat_does_not_race_imports(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "0.00001"

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)

    assert status == 0, stderr


def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing events are only sent in parent process"""
    from ddtrace.internal.telemetry.writer import get_runtime_id

    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import os

import ddtrace # enables telemetry
from ddtrace.internal.runtime import get_runtime_id


if os.fork() == 0:
    # Force the child to (re)build and start its worker. With emit_app_lifecycle=False it must
    # still not emit app-started (or app-closing), no matter what it does.
    from ddtrace.internal.telemetry import telemetry_writer

    telemetry_writer.enable()
else:
    # Print the parent process runtime id for validation
    print(get_runtime_id())
    """
    env = os.environ.copy()

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b"", stderr

    runtime_id = stdout.strip().decode("utf-8")

    # app-started/app-closing must come only from the parent process (and the in-process pytest
    # telemetry_writer), never from the forked child, which rebuilds with emit_app_lifecycle=False.
    # The child may still re-report its own dependencies (app-dependencies-loaded) after fork — that
    # matches the pre-native behaviour — so restrict the assertion to the lifecycle events this test
    # is about (mirroring the original master assertion).
    child_lifecycle = [
        e
        for e in test_agent_session.get_events("app-started") + test_agent_session.get_events("app-closing")
        if e["runtime_id"] not in (runtime_id, get_runtime_id())
    ]
    assert child_lifecycle == [], child_lifecycle

    app_closing = [e for e in test_agent_session.get_events("app-closing") if e["runtime_id"] == runtime_id]
    assert len(app_closing) == 1

    app_started = [e for e in test_agent_session.get_events("app-started") if e["runtime_id"] == runtime_id]
    assert len(app_started) == 1


@pytest.mark.skipif(os.name != "posix", reason="requires os.fork")
def test_metric_collection_after_fork(test_agent_session, run_python_code_in_subprocess):
    code = """
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import os

import ddtrace  # enables telemetry
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


pid = os.fork()
if pid == 0:
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "fork_child_metric", 1)
    telemetry_writer.periodic(force_flush=True)
    print(get_runtime_id(), flush=True)
    os._exit(0)

os.waitpid(pid, 0)
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    assert stderr == b"", stderr

    child_runtime_id = stdout.strip().decode("utf-8")
    child_metric_events = [
        event for event in test_agent_session.get_events("generate-metrics") if event["runtime_id"] == child_runtime_id
    ]
    child_metrics = [
        metric
        for event in child_metric_events
        for metric in event["payload"]["series"]
        if metric["metric"] == "fork_child_metric"
    ]

    assert len(child_metrics) == 1, child_metrics
    assert child_metrics[0]["type"] == "count"
    assert child_metrics[0]["points"][0][1] == 1


@pytest.mark.skipif(os.name != "posix", reason="requires a native fork")
def test_metric_collection_after_native_fork(test_agent_session, run_python_code_in_subprocess):
    """Native forks that bypass Python hooks restart metric collection in the child."""
    code = """
import ctypes
import os
import signal
import sys
import time
import types

# Reproduce ddtrace-run loading ddtrace before uWSGI has populated uwsgi.opt.
sys.modules["uwsgi"] = types.SimpleNamespace()
import ddtrace
from ddtrace.internal.native import MetricNamespace
from ddtrace.internal.native import MetricType
from ddtrace.internal.telemetry import telemetry_writer


worker = telemetry_writer._worker
assert worker is not None
context = worker.register_metric_context(
    MetricNamespace.tracers,
    "native_fork",
    MetricType.count,
    [],
    True,
)

libc = ctypes.CDLL(None)
libc.fork.restype = ctypes.c_int
pid = libc.fork()
if pid == 0:
    for _ in range(4096):
        worker.add_point(context, 1)
        worker.add_point_with_tags(context, 1, ["fork:child"])
    telemetry_writer.periodic(force_flush=True)
    os._exit(0)

deadline = time.monotonic() + 5
while True:
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited:
        break
    if time.monotonic() >= deadline:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise AssertionError("metric producer blocked on the inherited ring buffer")
    time.sleep(0.01)

assert os.waitstatus_to_exitcode(status) == 0
"""

    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    child_metrics = [
        metric
        for event in test_agent_session.get_events("generate-metrics")
        for metric in event["payload"]["series"]
        if metric["metric"] == "native_fork" and metric["tags"] == ["fork:child"]
    ]
    assert len(child_metrics) == 1, child_metrics
    assert child_metrics[0]["type"] == "count"
    assert child_metrics[0]["points"][0][1] == 4096


@pytest.mark.skipif(os.name != "posix", reason="requires native atfork handlers")
def test_native_atfork_does_not_start_runtime_before_exec(run_python_code_in_subprocess):
    """Fork-exec children must not start Tokio before exec closes inherited descriptors."""
    code = """
import subprocess
import sys

import ddtrace  # enables telemetry and registers native atfork handlers
from ddtrace.internal.telemetry import telemetry_writer


assert telemetry_writer._worker is not None
for _ in range(64):
    result = subprocess.run(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert result.stderr == b"", result.stderr
"""

    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    assert stderr == b"", stderr


def _subprocess_lineage(test_agent_session):
    """Return {runtime_id: dd-parent-session-id} from the recorded request headers.

    The test agent lowercases header names; a process emits ``dd-parent-session-id`` only when it
    is an exec'd/forked child (the root reports no parent). Used to single out the exec'd grandchild
    from the in-process telemetry_writer fixture and the rootless ddtrace-run launcher/script
    processes.
    """
    lineage = {}
    for r in test_agent_session.get_requests(filter_heartbeats=False):
        h = {k.lower(): v for k, v in r["headers"].items()}
        lineage.setdefault(r["body"]["runtime_id"], h.get("dd-parent-session-id"))
    return lineage


def test_enable_subprocess_exec_suppresses_app_started(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """A child process spawned via subprocess.run must not re-emit app-started.

    ``subprocess.Popen.__init__`` is patched to inject the session-lineage env vars
    (``get_runtime_propagation_envs``) into the child, so the exec'd grandchild sees a non-None
    ``get_parent_runtime_id()`` and builds its native worker with ``emit_app_lifecycle=False``.
    """
    code = """
import subprocess
import sys

subprocess.run([sys.executable, "-c", "import ddtrace.auto"], check=True)
"""
    env = os.environ.copy()

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # The session also contains the in-process telemetry_writer fixture's app-started and the rootless
    # ddtrace-run launcher/script processes' app-started. The exec'd grandchild is the only process
    # that reports a dd-parent-session-id; assert it emitted NO app-started.
    lineage = _subprocess_lineage(test_agent_session)
    child_runtime_ids = {rt for rt, parent in lineage.items() if parent}
    assert child_runtime_ids, "expected an exec'd child process with a parent session id"
    child_app_started = [
        e for e in test_agent_session.get_events("app-started") if e["runtime_id"] in child_runtime_ids
    ]
    assert child_app_started == [], child_app_started


def test_enable_subprocess_exec_suppresses_app_closing(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """A child process spawned via subprocess.run must not emit app-closing (only the root does).

    The native worker is built with emit_app_lifecycle=False in non-root processes, so even the
    implicit Stop dispatched at runtime shutdown does not emit an app-closing payload.
    """
    code = """
import subprocess
import sys

subprocess.run([sys.executable, "-c", "import ddtrace.auto"], check=True)
"""
    env = os.environ.copy()

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    lineage = _subprocess_lineage(test_agent_session)
    child_runtime_ids = {rt for rt, parent in lineage.items() if parent}
    assert child_runtime_ids, "expected an exec'd child process with a parent session id"
    child_app_closing = [
        e for e in test_agent_session.get_events("app-closing") if e["runtime_id"] in child_runtime_ids
    ]
    assert child_app_closing == [], child_app_closing


def test_enable_fork_heartbeat(test_agent_session, run_python_code_in_subprocess):
    """
    assert app-heartbeat events are also sent in forked processes since otherwise the dependency collection
    would be lost in pre-fork models after one hour.
    """
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import os
import time

import ddtrace # enables telemetry

# The native worker self-schedules heartbeats off DD_TELEMETRY_HEARTBEAT_INTERVAL (set to a small
# value below). Fork, then in both parent and child sleep long enough for several auto-heartbeats so
# we verify the forked child keeps heartbeating (otherwise pre-fork dependency collection would be
# lost after one hour).

pid = os.fork()
# Sleep well past several heartbeat intervals so both processes emit multiple heartbeats.
time.sleep(2)
if pid == 0:
    os._exit(0)
else:
    os.waitpid(pid, 0)
    """
    env = os.environ.copy()
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = "false"
    # Prevents dependencies loaded event from being generated
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "0.2"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b"", stderr

    # Allow test agent session to capture all heartbeat events. Both the parent subprocess and its
    # forked child keep heartbeating, so well more than one heartbeat lands in the session.
    app_heartbeats = test_agent_session.get_events("app-heartbeat", filter_heartbeats=False)
    assert len(app_heartbeats) > 1, app_heartbeats


def test_heartbeat_interval_configuration(run_python_code_in_subprocess):
    """assert that DD_TELEMETRY_HEARTBEAT_INTERVAL config sets the telemetry writer interval"""
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

from ddtrace import config
assert config._telemetry_heartbeat_interval == 61

# The native worker self-schedules heartbeats from the configured interval; the old
# Python-side gating counters (_is_periodic / interval / _periodic_threshold) are gone.
from ddtrace.internal.telemetry import telemetry_writer
assert telemetry_writer._worker is not None
    """

    env = os.environ.copy()
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "61"
    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b""


def test_logs_after_fork(run_python_code_in_subprocess):
    # Regression test: telemetry writer should not log an error when a process forks
    _, err, status, _ = run_python_code_in_subprocess(
        """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ddtrace # enables telemetry
import logging
import os

os.fork()
"""
    )

    assert status == 0, err
    assert err == b"", err


def test_app_started_error_handled_exception(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

from ddtrace.trace import tracer
from ddtrace.trace import TraceFilter

class FailingFilture(TraceFilter):
    def process_trace(self, trace):
       raise Exception("Exception raised in trace filter")

tracer.configure(trace_processors=[FailingFilture()])

# generate and encode span to trigger sampling failure
tracer.trace("hello").finish()

# force app_started event (instead of waiting for 10 seconds)
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.periodic(force_flush=True)
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert b"Exception raised in trace filter" in stderr

    # The in-process telemetry_writer fixture also emits an app-started into the session, so the
    # subprocess's app-started is not the only one; assert at least one was emitted.
    events = test_agent_session.get_events("app-started")
    assert len(events) >= 1

    logs_event = test_agent_session.get_events("logs")
    error_log = logs_event[0]["payload"]["logs"][0]
    assert error_log["message"] == "error applying processor %r to trace %d"
    assert error_log["level"] == "ERROR"
    _assert_ddtrace_stack_trace(error_log["stack_trace"], "in on_span_finish", "spans = tp.process_trace(spans) or []")


def test_register_telemetry_excepthook_after_another_hook(test_agent_session, run_python_code_in_subprocess):
    out, stderr, status, _ = run_python_code_in_subprocess(
        """
import sys

old_exc_hook = sys.excepthook
def pre_ddtrace_exc_hook(exctype, value, traceback):
    print("pre_ddtrace_exc_hook called")
    return old_exc_hook(exctype, value, traceback)

sys.excepthook = pre_ddtrace_exc_hook

import ddtrace
raise Exception('bad_code')
"""
    )
    assert b"pre_ddtrace_exc_hook called" in out
    assert status == 1, stderr
    assert b"bad_code" in stderr
    # Regression test for python3.12 support
    assert b"RuntimeError: can't create new thread at interpreter shutdown" not in stderr
    # Regression test for invalid number of arguments in wrapped exception hook
    assert b"3 positional arguments but 4 were given" not in stderr

    # The in-process telemetry_writer fixture emits its own app-started, so the subprocess's is not
    # the only one; assert at least one app-started was emitted.
    app_starteds = test_agent_session.get_events("app-started")
    assert len(app_starteds) >= 1

    # the tracer does not capture non ddtrace related errors
    logs_event = test_agent_session.get_events("logs")
    assert len(logs_event) == 0


def test_handled_integration_error(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

import sqlite3
# patch() of the sqlite integration assumes this attribute is there
# removing it should cause patching to fail.
del sqlite3.connect

from ddtrace import patch, tracer
patch(raise_errors=False, sqlite3=True)
"""

    env = os.environ.copy()
    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)

    assert status == 0, stderr
    assert b"failed to enable ddtrace support for sqlite3" in stderr

    integrations_events = test_agent_session.get_events("app-integrations-change")
    assert len(integrations_events) == 1
    # The native Integration payload has no ``error`` field; a failed patch surfaces as
    # ``compatible: False`` plus the ``integration_errors`` metric below.
    sqlite_integration = integrations_events[0]["payload"]["integrations"][0]
    assert sqlite_integration["name"] == "sqlite3"
    assert sqlite_integration["compatible"] is False

    # Get metric containing the integration error
    integration_error = test_agent_session.get_metrics("integration_errors")
    # assert the integration metric has the correct type, count, and tags
    assert len(integration_error) == 1
    assert integration_error[0]["type"] == "count"
    assert integration_error[0]["points"][0][1] == 1
    # wrapt 2.4.0 now throws PathResolutionError instead of AttributeError.
    assert integration_error[0]["tags"] in (
        ["integration_name:sqlite3", "error_type:attributeerror"],
        ["integration_name:sqlite3", "error_type:pathresolutionerror"],
    )


def test_unhandled_integration_error(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
import flask
f = flask.Flask("hi")

# Call flask.wsgi_app with an incorrect number of args
f.wsgi_app()
"""

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)

    assert status == 1, stderr

    assert b"not enough values to unpack (expected 2, got 0)" in stderr, stderr

    # The in-process telemetry_writer fixture and the ddtrace-run subprocess each emit app-started,
    # so assert at least one was emitted rather than exactly one.
    app_started_event = test_agent_session.get_events("app-started")
    assert len(app_started_event) >= 1

    logs_event = test_agent_session.get_events("logs")
    # Filter out crashtracker logs (contain "is_crash:true" in the message)
    telemetry_logs = [
        event
        for event in logs_event
        if not any("is_crash:true" in log.get("message", "") for log in event["payload"]["logs"])
    ]
    error_log = telemetry_logs[0]["payload"]["logs"][0]
    assert error_log["message"] == "Unhandled exception from ddtrace code"
    assert error_log["level"] == "ERROR"
    _assert_ddtrace_stack_trace(error_log["stack_trace"], "patched_wsgi_app")

    integration_events = test_agent_session.get_events("app-integrations-change")
    integrations = [
        integration
        for e in integration_events
        for integration in e["payload"]["integrations"]
        if integration["name"] == "flask"
    ]
    # The native worker reports flask twice here: once for the successful auto-patch at startup
    # (compatible: True) and once for the failing runtime path (compatible: False). The native
    # Integration payload has no ``error`` field; the failure surfaces as ``compatible: False`` plus
    # the ``integration_errors`` metric below.
    flask_failures = [i for i in integrations if i["compatible"] is False]
    assert len(flask_failures) == 1, integrations
    flask_integration = flask_failures[0]
    assert flask_integration["enabled"] is True
    assert flask_integration["compatible"] is False

    error_metrics = test_agent_session.get_metrics("integration_errors")
    assert len(error_metrics) == 1
    error_metric = error_metrics[0]
    assert error_metric["type"] == "count"
    assert len(error_metric["points"]) == 1
    assert error_metric["points"][0][1] == 1
    assert error_metric["tags"] == ["integration_name:flask", "error_type:valueerror"]


def test_app_started_with_install_metrics(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_INSTRUMENTATION_INSTALL_ID": "68e75c48-57ca-4a12-adfc-575c4b05fcbe",
            "DD_INSTRUMENTATION_INSTALL_TYPE": "k8s_single_step",
            "DD_INSTRUMENTATION_INSTALL_TIME": "1703188212",
        }
    )
    # Generate a trace to trigger app-started event
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)
    assert status == 0, stderr

    # The in-process telemetry_writer fixture also emits an app-started into the session, but only
    # the subprocess sets the DD_INSTRUMENTATION_INSTALL_* env, so its app-started is the only one
    # carrying an install_signature. Filter to that one.
    app_started_with_install = [
        e for e in test_agent_session.get_events("app-started") if e["payload"].get("install_signature")
    ]
    assert len(app_started_with_install) == 1
    assert app_started_with_install[0]["payload"]["install_signature"] == {
        "install_id": "68e75c48-57ca-4a12-adfc-575c4b05fcbe",
        "install_type": "k8s_single_step",
        "install_time": "1703188212",
    }


def test_instrumentation_telemetry_disabled(test_agent_session, run_python_code_in_subprocess):
    """Ensure no telemetry events are sent by a subprocess when telemetry is disabled.

    The in-process ``telemetry_writer`` fixture (a dependency of ``test_agent_session``) is itself
    enabled and emits its own app-started/app-closing into the session, so we can't assert the
    session is empty. Instead we assert that the *subprocess* contributed nothing: no event carries
    a runtime_id other than the in-process writer's.
    """
    from ddtrace.internal.telemetry.writer import get_runtime_id

    env = os.environ.copy()
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"

    code = """
from ddtrace.trace import tracer

# We want to import the telemetry module even when telemetry is disabled.
import sys
assert "ddtrace.internal.telemetry" in sys.modules
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)

    assert status == 0, stderr
    assert stderr == b""

    in_process_runtime_id = get_runtime_id()
    foreign_events = [e for e in test_agent_session.get_events() if e["runtime_id"] != in_process_runtime_id]
    assert foreign_events == [], foreign_events


# Disable agentless to ensure telemetry is enabled (agentless needs dd-api-key to be set)
@pytest.mark.subprocess(env={"DD_CIVISIBILITY_AGENTLESS_ENABLED": "0"})
def test_installed_excepthook():
    import sys

    # importing ddtrace initializes the telemetry writer and installs the excepthook
    import ddtrace  # noqa: F401

    # ddtrace installs a single dispatching hook that fans out to registered callbacks
    from ddtrace.internal import excepthook

    assert sys.excepthook.__name__ == "_ddtrace_excepthook"

    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled is True
    assert telemetry_writer._telemetry_excepthook in excepthook._hooks
    telemetry_writer.uninstall_excepthook()
    assert telemetry_writer._telemetry_excepthook not in excepthook._hooks
    # The dispatcher stays installed even after a component unregisters
    assert sys.excepthook.__name__ == "_ddtrace_excepthook"


def test_telemetry_multiple_sources(test_agent_session, run_python_code_in_subprocess):
    """Test that a config is submitted for multiple sources with increasing seq_id"""

    env = os.environ.copy()
    env["OTEL_TRACES_EXPORTER"] = "none"
    env["DD_TRACE_ENABLED"] = "false"

    _, err, status, _ = run_python_code_in_subprocess(
        "from ddtrace import config; config._tracing_enabled = True", env=env
    )
    assert status == 0, err

    configs = test_agent_session.get_configurations(name="DD_TRACE_ENABLED", remove_seq_id=False, effective=False)
    assert len(configs) == 4, configs

    # The native worker serializes every configuration value as a string and owns the seq_id, so
    # assert relative ordering and the stringified values.
    sorted_configs = sorted(configs, key=lambda x: x["seq_id"])
    seq_ids = [c["seq_id"] for c in sorted_configs]
    assert seq_ids == sorted(seq_ids) and len(set(seq_ids)) == 4, seq_ids

    # Booleans serialize to lowercase "true"/"false" (telemetry wire format; see _config_value_to_str).
    assert sorted_configs[0]["value"] == "true"
    assert sorted_configs[0]["origin"] == "default"

    assert sorted_configs[1]["value"] == "none"
    assert sorted_configs[1]["origin"] == "otel_env_var"

    assert sorted_configs[2]["value"] == "false"
    assert sorted_configs[2]["origin"] == "env_var"

    assert sorted_configs[3]["value"] == "true"
    assert sorted_configs[3]["origin"] == "code"


def test_session_id_headers_across_forks(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Verify session ID headers are correct across a parent -> child -> grandchild fork tree."""
    code = """
import os
import sys

pid1 = os.fork()
if pid1 == 0:
    pid2 = os.fork()
    if pid2 == 0:
        sys.exit(0)
    else:
        os.waitpid(pid2, 0)
        sys.exit(0)
else:
    os.waitpid(pid1, 0)
"""
    env = os.environ.copy()

    from ddtrace.internal.telemetry.writer import get_runtime_id

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # One representative request per process. The test agent records header names lowercased, so
    # normalize each request's headers to lowercase keys before reading the session-lineage headers.
    # Exclude the in-process telemetry_writer fixture (another rootless process in this same pytest
    # process) so only the subprocess fork tree (root + 2 forked children) is considered.
    in_process_runtime_id = get_runtime_id()
    seen = {}
    for req in test_agent_session.get_requests(filter_heartbeats=False):
        req["headers"] = {k.lower(): v for k, v in req["headers"].items()}
        if req["body"]["runtime_id"] == in_process_runtime_id:
            continue
        seen.setdefault(req["body"]["runtime_id"], req)
    unique_requests = list(seen.values())

    # dd-session-id always matches the runtime_id in the payload
    for req in unique_requests:
        assert req["headers"].get("dd-session-id") == req["body"]["runtime_id"]

    # The two forked children carry dd-root-session-id / dd-parent-session-id; the script's root
    # process carries neither. (The ddtrace-run launcher bootstraps telemetry once before it execs
    # into the script, producing an extra orphan rootless process with no children; identify the
    # true root as the one the forked children point at via dd-root-session-id.)
    child_reqs = [r for r in unique_requests if "dd-root-session-id" in r["headers"]]
    assert len(child_reqs) == 2, child_reqs

    root_ids = {r["headers"]["dd-root-session-id"] for r in child_reqs}
    assert len(root_ids) == 1, root_ids
    parent_id = next(iter(root_ids))

    # The true root reported with no lineage headers.
    root_reqs = [r for r in unique_requests if r["headers"]["dd-session-id"] == parent_id]
    assert len(root_reqs) == 1
    assert "dd-root-session-id" not in root_reqs[0]["headers"]
    assert "dd-parent-session-id" not in root_reqs[0]["headers"]

    # Lineage: child's parent is root; grandchild's parent is child
    children_by_parent = {}
    for req in child_reqs:
        children_by_parent.setdefault(req["headers"]["dd-parent-session-id"], []).append(
            req["headers"]["dd-session-id"]
        )

    assert len(children_by_parent[parent_id]) == 1
    child1_id = children_by_parent[parent_id][0]
    assert len(children_by_parent[child1_id]) == 1


@pytest.mark.parametrize("collect_dependencies", [True, False])
def test_extended_heartbeat_sent(collect_dependencies, ddtrace_run_python_code_in_subprocess, test_agent_session):
    """Assert at least one extended heartbeat is sent when the extended heartbeat interval has elapsed."""

    env = os.environ.copy()
    env["_DD_TELEMETRY_EXTENDED_HEARTBEAT_INTERVAL"] = "1"
    env["DD_TELEMETRY_LOG_COLLECTION_ENABLED"] = "0.1"
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = str(collect_dependencies)

    # The native worker self-schedules the extended heartbeat on libdatadog's own timer (1s here),
    # independently of the Python-side dependency discovery that runs in ``periodic()``. To make the
    # dependency snapshot deterministic, import a 3rd-party dependency and drive ``periodic()`` so the
    # dependency is registered in the worker's store BEFORE the extended heartbeat fires; libdatadog's
    # ExtendedHeartbeat re-includes all stored dependencies (it calls ``unflush_stored()``). Without
    # this ordering the extended heartbeat can fire before any dependency is discovered and carry an
    # empty list.
    code = """
import time
import xmltodict  # a non-stdlib dependency to be discovered
from ddtrace.internal.telemetry import telemetry_writer
# Discover & register dependencies in the worker store before the extended heartbeat fires.
telemetry_writer.periodic(force_flush=True)
# Sleep past the 1s extended-heartbeat interval so the heartbeat fires with deps already stored.
time.sleep(1.5)
"""

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    # Dynamic Instrumentation (unrelated to telemetry) prints an "Unsupported Datadog agent ...
    # upgrade to version 7.49.0 or later" warning to stderr when the agent is older than 7.49.0
    # (as the local/test agent often is). Tolerate only that specific line; this test asserts on the
    # extended-heartbeat telemetry below.
    stderr_lines = [
        ln
        for ln in stderr.split(b"\n")
        if ln.strip() and b"debugger::unsupported_agent" not in ln and b"Unsupported Datadog agent" not in ln
    ]
    assert stderr_lines == [], stderr

    extended_events = test_agent_session.get_events("app-extended-heartbeat")
    assert len(extended_events) >= 1

    extended_event = extended_events[0]
    extended_config = extended_event["payload"]["configuration"]
    assert extended_config is not None
    # The extended heartbeat re-sends the full cumulative configuration snapshot of its own process.
    # Filter session events to that same process by runtime_id so the in-process telemetry_writer
    # fixture and the ddtrace-run launcher process don't pollute the comparison.
    #
    # Note: app-started goes out as soon as products load, before most
    # configuration values have been registered, so its configuration list is a small subset. The
    # remaining values register slightly later and are surfaced only in the (later) extended-heartbeat
    # snapshot rather than being re-emitted as app-client-configuration-change events. So the faithful
    # invariant is that the extended heartbeat is a non-empty superset of everything its own process
    # reported via app-started / app-client-configuration-change.
    runtime_id = extended_event["runtime_id"]

    def _key(cfg):
        return (cfg["name"], cfg["value"], cfg["origin"], cfg["seq_id"])

    own_config = set()
    for ev in test_agent_session.get_events("app-started") + test_agent_session.get_events(
        "app-client-configuration-change"
    ):
        if ev["runtime_id"] == runtime_id:
            own_config.update(_key(c) for c in ev["payload"]["configuration"])

    extended_keys = {_key(c) for c in extended_config}
    assert own_config, "expected the process to report at least some configuration at app-started"
    # The extended heartbeat re-sends exactly the cumulative configuration the process reported via
    # its app-started / app-client-configuration-change events.
    assert extended_keys == own_config, extended_keys ^ own_config
    # Sanity: an env var this test set on the subprocess is reflected in the snapshot.
    assert any(c["name"] == "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED" for c in extended_config), (
        "expected DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED in the extended-heartbeat configuration"
    )

    # The native (libdatadog) worker always serializes a ``dependencies`` field on the extended
    # heartbeat. When dependency collection is enabled, ``periodic()`` above registered xmltodict in
    # the worker store, and the extended heartbeat re-includes all stored dependencies; when disabled,
    # the Python side never registers any, so the list is empty.
    extended_deps = extended_event["payload"].get("dependencies", [])
    if collect_dependencies:
        assert any(d["name"] == "xmltodict" for d in extended_deps), extended_deps
    else:
        assert extended_deps == [], extended_deps
