"""Fork-child controls on the native telemetry worker.

Each test drives a standalone worker and runtime in a subprocess and simulates the fork-child
sequence with the runtime's fork hooks, so the parent and child identities share one process.
"""

import os

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires the native fork hooks")

WORKER_SETUP = """
import sys

from ddtrace.internal.native._native import ConfigurationOrigin
from ddtrace.internal.native._native import SharedRuntime
from ddtrace.internal.native._native import TelemetryWorker
from ddtrace.internal.settings._agent import config as agent_config

runtime = SharedRuntime()
worker = TelemetryWorker(
    runtime,
    service="fork-controls",
    env=None,
    app_version=None,
    language_name="python",
    language_version="3",
    tracer_version="1.0.0",
    runtime_id="parent-rid",
    runtime_name=None,
    runtime_version=None,
    process_tags=None,
    hostname="host",
    os=None,
    os_version=None,
    architecture=None,
    kernel_name=None,
    kernel_release=None,
    kernel_version=None,
    container_id=None,
    endpoint_url=agent_config.trace_agent_url,
    api_key=None,
    session_id="parent-rid",
    parent_session_id=None,
    root_session_id=None,
    heartbeat_interval_secs=3600.0,
    extended_heartbeat_interval_secs=3600.0,
    debug_enabled=False,
    test_session_token=sys.argv[1],
)


def child_controls():
    worker.set_identity("child-rid", "child-rid", "parent-rid", "parent-rid")
    worker.set_app_lifecycle(app_started=False, app_closing=False)
    worker.stage_fork_replay(
        [("fork.replayed", "1", ConfigurationOrigin.env_var, None, 1)],
        [("flask", "3.0", True, None, None, None)],
        [("appsec", True, None)],
    )
"""


def _run(run_python_code_in_subprocess, test_agent_session, code):
    env = os.environ.copy()
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"
    script = WORKER_SETUP + code
    stdout, stderr, status, _ = run_python_code_in_subprocess(
        "import sys; sys.argv.append(%r)\n" % test_agent_session.token + script, env=env
    )
    assert status == 0, (stdout, stderr)


def _by_runtime(test_agent_session, runtime_id):
    return [
        r for r in test_agent_session.get_requests(filter_heartbeats=False) if r["body"]["runtime_id"] == runtime_id
    ]


def _events(test_agent_session, runtime_id, request_type):
    return [
        e
        for e in test_agent_session.get_events(filter_heartbeats=False)
        if e["runtime_id"] == runtime_id and e["request_type"] == request_type
    ]


def test_started_parent_child_keeps_worker_under_new_identity(test_agent_session, run_python_code_in_subprocess):
    _run(
        run_python_code_in_subprocess,
        test_agent_session,
        """
worker.start()
worker.add_configuration("parent.only", "1", ConfigurationOrigin.default, None, 1)
worker.add_dependency("requests", "2.0", None)
worker.flush()

runtime.before_fork()
child_controls()
runtime.after_fork_child()
# The fork reset clears the dependency dedupe history, so the child reports it again.
worker.add_dependency("requests", "2.0", None)
worker.flush()
worker.stop(True)
""",
    )

    assert len(_events(test_agent_session, "parent-rid", "app-started")) == 1

    child_requests = _by_runtime(test_agent_session, "child-rid")
    assert child_requests, "the child sent nothing"
    assert min(r["body"]["seq_id"] for r in child_requests) == 1
    headers = {k.lower(): v for k, v in child_requests[0]["headers"].items()}
    assert headers["dd-session-id"] == "child-rid"
    assert headers["dd-parent-session-id"] == "parent-rid"

    assert _events(test_agent_session, "child-rid", "app-started") == []
    assert _events(test_agent_session, "child-rid", "app-closing") == []
    assert _events(test_agent_session, "child-rid", "app-heartbeat")

    child_configs = [
        c["name"]
        for e in _events(test_agent_session, "child-rid", "app-client-configuration-change")
        for c in e["payload"]["configuration"]
    ]
    assert child_configs == ["fork.replayed"]

    child_deps = [
        d["name"]
        for e in _events(test_agent_session, "child-rid", "app-dependencies-loaded")
        for d in e["payload"]["dependencies"]
    ]
    assert child_deps == ["requests"]


def test_unstarted_parent_child_start_skips_app_started(test_agent_session, run_python_code_in_subprocess):
    _run(
        run_python_code_in_subprocess,
        test_agent_session,
        """
runtime.before_fork()
child_controls()
runtime.after_fork_child()
worker.start()
worker.flush()
worker.stop(True)
""",
    )

    assert _events(test_agent_session, "parent-rid", "app-started") == []
    assert _events(test_agent_session, "child-rid", "app-started") == []
    assert _events(test_agent_session, "child-rid", "app-closing") == []
    assert _events(test_agent_session, "child-rid", "app-heartbeat")


def test_controls_work_while_child_restart_is_deferred(test_agent_session, run_python_code_in_subprocess):
    _run(
        run_python_code_in_subprocess,
        test_agent_session,
        """
worker.start()
worker.flush()

runtime.before_fork()
runtime.defer_after_fork_child()
child_controls()
runtime.allow_after_fork_child()
runtime.after_fork_child()
worker.flush()
worker.stop(True)
""",
    )

    assert _events(test_agent_session, "child-rid", "app-closing") == []
    child_configs = [
        c["name"]
        for e in _events(test_agent_session, "child-rid", "app-client-configuration-change")
        for c in e["payload"]["configuration"]
    ]
    assert child_configs == ["fork.replayed"]
