from contextlib import contextmanager
import json
import os
import subprocess
import sys

from ddtrace.internal.utils.retry import RetryError
from tests.webclient import Client


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env():
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    return environ


@contextmanager
def gunicorn_server(telemetry_metrics_enabled="true", token=None):
    cmd = ["ddtrace-run", "gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "tests.telemetry.app:app"]
    env = _build_env()
    env["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:{}".format(token)
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")
    env["DD_TRACE_DEBUG"] = "true"
    # do not patch flask because we will end up with confusing metrics
    # now that we generate metrics for spans
    env["DD_PATCH_MODULES"] = "flask:false"
    server_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://0.0.0.0:8000")
        try:
            print("Waiting for server to start")
            client.wait(max_tries=100, delay=0.1)
            print("Server started")
        except RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )

        yield server_process, client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            raise AssertionError(
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
    finally:
        server_process.terminate()
        server_process.wait()


def parse_payload(data):
    return json.loads(data)


def test_telemetry_metrics_enabled_on_gunicorn_child_process(test_agent_session):
    token = "tests.telemetry.test_telemetry_metrics_e2e.test_telemetry_metrics_enabled_on_gunicorn_child_process"
    initial_event_count = len(test_agent_session.get_events("generate-metrics"))
    with gunicorn_server(telemetry_metrics_enabled="true", token=token) as context:
        _, gunicorn_client = context

        gunicorn_client.get("/count_metric")
        gunicorn_client.get("/count_metric")
        response = gunicorn_client.get("/count_metric")
        assert response.status_code == 200
        gunicorn_client.get("/count_metric")
        response = gunicorn_client.get("/count_metric")
        assert response.status_code == 200

    metrics = test_agent_session.get_events("generate-metrics")
    assert len(metrics) > initial_event_count
    assert len(metrics) == 1
    assert metrics[0]["payload"]["series"][0]["metric"] == "test_metric"
    assert metrics[0]["payload"]["series"][0]["points"][0][1] == 5


def test_span_creation_and_finished_metrics_datadog(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
from ddtrace import tracer
for _ in range(10):
    with tracer.trace('span1'):
        pass
"""
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, stderr
    metrics_events = test_agent_session.get_events("generate-metrics")
    metrics_sc = get_metrics_from_events("spans_created", metrics_events)
    assert len(metrics_sc) == 1
    assert metrics_sc[0]["metric"] == "spans_created"
    assert metrics_sc[0]["tags"] == ["integration_name:datadog"]
    assert metrics_sc[0]["points"][0][1] == 10

    metrics_sf = get_metrics_from_events("spans_finished", metrics_events)
    assert len(metrics_sf) == 1
    assert metrics_sf[0]["metric"] == "spans_finished"
    assert metrics_sf[0]["tags"] == ["integration_name:datadog"]
    assert metrics_sf[0]["points"][0][1] == 10


def test_span_creation_and_finished_metrics_otel(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
import opentelemetry.trace

ot = opentelemetry.trace.get_tracer(__name__)
for _ in range(9):
    with ot.start_span('span'):
        pass
"""
    env = os.environ.copy()
    env["DD_TRACE_OTEL_ENABLED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    metrics_events = test_agent_session.get_events("generate-metrics")

    metrics_sc = get_metrics_from_events("spans_created", metrics_events)
    assert len(metrics_sc) == 1
    assert metrics_sc[0]["metric"] == "spans_created"
    assert metrics_sc[0]["tags"] == ["integration_name:otel"]
    assert metrics_sc[0]["points"][0][1] == 9

    metrics_sf = get_metrics_from_events("spans_finished", metrics_events)
    assert len(metrics_sf) == 1
    assert metrics_sf[0]["metric"] == "spans_finished"
    assert metrics_sf[0]["tags"] == ["integration_name:otel"]
    assert metrics_sf[0]["points"][0][1] == 9


def test_span_creation_and_finished_metrics_opentracing(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
from ddtrace.opentracer import Tracer

ot = Tracer()
for _ in range(2):
    with ot.start_span('span'):
        pass
"""
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, stderr

    metrics_events = test_agent_session.get_events("generate-metrics")

    metrics_sc = get_metrics_from_events("spans_created", metrics_events)
    assert len(metrics_sc) == 1
    assert metrics_sc[0]["metric"] == "spans_created"
    assert metrics_sc[0]["tags"] == ["integration_name:opentracing"]
    assert metrics_sc[0]["points"][0][1] == 2

    metrics_sf = get_metrics_from_events("spans_finished", metrics_events)
    assert len(metrics_sf) == 1
    assert metrics_sf[0]["metric"] == "spans_finished"
    assert metrics_sf[0]["tags"] == ["integration_name:opentracing"]
    assert metrics_sf[0]["points"][0][1] == 2


def test_span_creation_no_finish(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
import ddtrace
import opentelemetry.trace
from ddtrace import opentracer

ddtracer = ddtrace.tracer
otel = opentelemetry.trace.get_tracer(__name__)
ot = opentracer.Tracer()

# we must finish at least one span to enable sending telemetry to the agent
ddtracer.trace("first_span").finish()

for _ in range(4):
    ot.start_span('ot_span')
    otel.start_span('otel_span')
    ddtracer.trace("ddspan")
"""
    env = os.environ.copy()
    env["DD_TRACE_OTEL_ENABLED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    metrics_events = test_agent_session.get_events("generate-metrics")
    metrics = get_metrics_from_events("spans_created", metrics_events)
    assert len(metrics) == 3

    assert metrics[0]["metric"] == "spans_created"
    assert metrics[0]["tags"] == ["integration_name:datadog"]
    assert metrics[0]["points"][0][1] == 5
    assert metrics[1]["metric"] == "spans_created"
    assert metrics[1]["tags"] == ["integration_name:opentracing"]
    assert metrics[1]["points"][0][1] == 4
    assert metrics[2]["metric"] == "spans_created"
    assert metrics[2]["tags"] == ["integration_name:otel"]
    assert metrics[2]["points"][0][1] == 4


def get_metrics_from_events(name, events):
    metrics = []
    for event in events:
        for series in event["payload"]["series"]:
            if series["metric"] == name:
                metrics.append(series)
    metrics.sort(key=lambda x: (x["metric"], x["tags"]), reverse=False)
    return metrics
