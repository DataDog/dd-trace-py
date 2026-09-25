from http import client as httplib
import json
from urllib import parse

from ddtrace import tracer
from tests.webclient import PING_TRACE_ID


def start_trace(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/start?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    return resp.read()


def clear_session(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/clear?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    return resp.read()


def _get_span(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/traces?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    traces = json.loads(resp.read())
    kept = [trace for trace in traces if not trace or trace[0].get("trace_id") != PING_TRACE_ID]
    print(f"Dropped {len(traces) - len(kept)} readiness probe trace(s)")
    return kept


def _get_agent_client():
    parsed = parse.urlparse(tracer._span_aggregator.writer.intake_url)
    conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
    return conn
