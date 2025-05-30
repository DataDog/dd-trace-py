import json

from tests.appsec.integrations.flask_tests.test_flask_remoteconfig import _get_agent_client


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
    return json.loads(resp.read())
