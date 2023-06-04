from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time

import pytest
import tenacity

from ddtrace import tracer as datadog_tracer
from ddtrace.internal.telemetry import telemetry_metrics_writer
from ddtrace.opentelemetry._trace import Tracer as OtelTracer
from ddtrace.opentracer.tracer import Tracer as OpenTracer
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
    env["DD_TELEMETRY_METRICS_ENABLED"] = telemetry_metrics_enabled
    env["DD_TELEMETRY_METRICS_INTERVAL_SECONDS"] = "1.0"
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
        except tenacity.RetryError:
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
    decoded = data
    if sys.version_info[1] == 5:
        decoded = data.decode("utf-8")
    return json.loads(decoded)


@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_telemetry_metrics_enabled_on_gunicorn_child_process(test_agent_session):
    token = "tests.telemetry.test_telemetry_metrics_e2e.test_telemetry_metrics_enabled_on_gunicorn_child_process"
    assert len(test_agent_session.get_events()) == 0
    with gunicorn_server(telemetry_metrics_enabled="true", token=token) as context:
        _, gunicorn_client = context

        gunicorn_client.get("/metrics")
        gunicorn_client.get("/metrics")
        response = gunicorn_client.get("/metrics")
        response_content = json.loads(response.content)
        assert response_content["telemetry_metrics_writer_running"] is True
        assert response_content["telemetry_metrics_writer_worker"] is True
        assert response_content["telemetry_metrics_writer_queue"][0]["metric"] == "test_metric"
        assert response_content["telemetry_metrics_writer_queue"][0]["points"][0][1] == 3.0
        time.sleep(1)
        gunicorn_client.get("/metrics")
        response = gunicorn_client.get("/metrics")
        response_content = json.loads(response.content)
        assert response_content["telemetry_metrics_writer_queue"][0]["points"][0][1] == 2.0
    events = test_agent_session.get_events()
    # need to figure out why this changed
    assert len(events) == 6
    assert events[1]["payload"]["series"][0]["metric"] == "test_metric"
    assert events[3]["payload"]["series"][0]["metric"] == "test_metric"


def test_span_creation_and_finished_metrics(test_agent_session):
    assert len(test_agent_session.get_events()) == 0

    gen_spans(20, "datadog")
    metrics = get_metrics()
    assert len(metrics) == 2
    assert metrics[0]["metric"] == "datadog.span_created"
    assert metrics[0]["points"][0][1] == 20
    assert metrics[1]["metric"] == "datadog.span_finished"
    assert metrics[1]["points"][0][1] == 20

    gen_spans(13, "datadog")
    metrics = get_metrics()
    assert metrics[0]["metric"] == "datadog.span_created"
    assert metrics[0]["points"][0][1] == 33
    assert metrics[1]["metric"] == "datadog.span_finished"
    assert metrics[1]["points"][0][1] == 33

    gen_spans(20, "datadog")
    gen_spans(15, "datadog")
    metrics = get_metrics()
    assert metrics[0]["metric"] == "datadog.span_created"
    assert metrics[0]["points"][0][1] == 68
    assert metrics[1]["metric"] == "datadog.span_finished"
    assert metrics[1]["points"][0][1] == 68
    # send metrics to agent
    telemetry_metrics_writer.periodic()
    time.sleep(1.0)
    events = test_agent_session.get_events()
    # we only have the metrics event because app-started and app-dependencies-loaded have not sent yet
    assert len(events) == 1
    assert events[0]["payload"]["series"][0]["metric"] == "datadog.span_created"
    assert events[0]["payload"]["series"][0]["points"][0][1] == 68
    assert events[0]["payload"]["series"][1]["metric"] == "datadog.span_finished"
    assert events[0]["payload"]["series"][1]["points"][0][1] == 68

    gen_spans(15, "otel")
    metrics = get_metrics()
    assert len(metrics) == 2
    assert metrics[0]["metric"] == "otel.span_created"
    assert metrics[0]["points"][0][1] == 15

    assert metrics[1]["metric"] == "otel.span_finished"
    assert metrics[1]["points"][0][1] == 15

    telemetry_metrics_writer.periodic()
    time.sleep(1.0)
    events = test_agent_session.get_events()
    assert len(events) == 2
    assert events[0]["payload"]["series"][0]["metric"] == "otel.span_created"
    assert events[0]["payload"]["series"][0]["points"][0][1] == 15
    assert events[0]["payload"]["series"][1]["metric"] == "otel.span_finished"
    assert events[0]["payload"]["series"][1]["points"][0][1] == 15

    gen_spans(20, "opentracing")
    metrics = get_metrics()
    assert len(metrics) == 2
    assert metrics[0]["metric"] == "opentracing.span_created"
    assert metrics[1]["metric"] == "opentracing.span_finished"

    telemetry_metrics_writer.periodic()
    time.sleep(1.0)
    events = test_agent_session.get_events()
    assert len(events) == 3
    assert events[0]["payload"]["series"][0]["metric"] == "opentracing.span_created"
    assert events[0]["payload"]["series"][0]["points"][0][1] == 20
    assert events[0]["payload"]["series"][1]["metric"] == "opentracing.span_finished"
    assert events[0]["payload"]["series"][1]["points"][0][1] == 20
    telemetry_metrics_writer.periodic()

    gen_spans(10, "datadog")
    gen_spans(11, "otel")
    gen_spans(12, "opentracing")
    metrics = get_metrics()
    assert len(metrics) == 6
    assert metrics[0]["metric"] == "datadog.span_created"
    assert metrics[0]["points"][0][1] == 10
    assert metrics[1]["metric"] == "datadog.span_finished"
    assert metrics[1]["points"][0][1] == 10

    assert metrics[2]["metric"] == "otel.span_created"
    assert metrics[2]["points"][0][1] == 11
    assert metrics[3]["metric"] == "otel.span_finished"
    assert metrics[3]["points"][0][1] == 11

    assert metrics[4]["metric"] == "opentracing.span_created"
    assert metrics[4]["points"][0][1] == 12
    assert metrics[5]["metric"] == "opentracing.span_finished"
    assert metrics[5]["points"][0][1] == 12

    telemetry_metrics_writer.periodic()
    time.sleep(1.0)
    events = test_agent_session.get_events()

    assert len(events) == 4
    assert events[0]["payload"]["series"][0]["metric"] == "datadog.span_created"
    assert events[0]["payload"]["series"][0]["points"][0][1] == 10
    assert events[0]["payload"]["series"][1]["metric"] == "datadog.span_finished"
    assert events[0]["payload"]["series"][1]["points"][0][1] == 10

    assert events[0]["payload"]["series"][2]["metric"] == "otel.span_created"
    assert events[0]["payload"]["series"][2]["points"][0][1] == 11
    assert events[0]["payload"]["series"][3]["metric"] == "otel.span_finished"
    assert events[0]["payload"]["series"][3]["points"][0][1] == 11

    assert events[0]["payload"]["series"][4]["metric"] == "opentracing.span_created"
    assert events[0]["payload"]["series"][4]["points"][0][1] == 12
    assert events[0]["payload"]["series"][5]["metric"] == "opentracing.span_finished"
    assert events[0]["payload"]["series"][5]["points"][0][1] == 12


def test_span_creation_no_finish(test_agent_session):
    assert len(test_agent_session.get_events()) == 0

    gen_spans(1, "datadog", finish=False)
    gen_spans(2, "otel", finish=False)
    gen_spans(3, "opentracing", finish=False)

    metrics = get_metrics()
    assert len(metrics) == 3
    assert metrics[0]["metric"] == "datadog.span_created"
    assert metrics[0]["points"][0][1] == 1

    assert metrics[1]["metric"] == "otel.span_created"
    assert metrics[1]["points"][0][1] == 2

    assert metrics[2]["metric"] == "opentracing.span_created"
    assert metrics[2]["points"][0][1] == 3

    telemetry_metrics_writer.periodic()
    time.sleep(1.0)
    events = test_agent_session.get_events()

    assert len(events) == 1
    assert events[0]["payload"]["series"][0]["metric"] == "datadog.span_created"
    assert events[0]["payload"]["series"][0]["points"][0][1] == 1

    assert events[0]["payload"]["series"][1]["metric"] == "otel.span_created"
    assert events[0]["payload"]["series"][1]["points"][0][1] == 2

    assert events[0]["payload"]["series"][2]["metric"] == "opentracing.span_created"
    assert events[0]["payload"]["series"][2]["points"][0][1] == 3


def gen_spans(num, span_type, finish=True):
    tracer = datadog_tracer
    if span_type == "datadog":
        for i in range(num):
            s = tracer.start_span("test." + str(i))
            if finish:
                s.finish()

    if span_type == "otel":
        tracer = OtelTracer(datadog_tracer)
        for i in range(num):
            s = tracer.start_span("test." + str(i))
            if finish:
                s.end()

    if span_type == "opentracing":
        tracer = OpenTracer(dd_tracer=datadog_tracer)
        for i in range(num):
            s = tracer.start_span("test." + str(i))
            if finish:
                s.finish()


def get_metrics():
    namespace_metrics = telemetry_metrics_writer._namespace.get()

    metrics = [
        m.to_dict()
        for payload_type, namespaces in namespace_metrics.items()
        for namespace, metrics in namespaces.items()
        for m in metrics.values()
    ]
    return metrics
