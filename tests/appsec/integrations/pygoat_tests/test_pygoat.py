import json
import time

import pytest
import requests

from tests.appsec.iast.conftest import iast_span_defaults
from tests.utils import flaky


span_defaults = iast_span_defaults  # So ruff does not remove it


# Note: these tests require the testagent and pygoat images to be up from the docker-compose file
# dc up -d pygoat testagent

IMAGE_NAME = "pygoat:2.0.1"
PYGOAT_URL = "http://0.0.0.0:8321"
TESTAGENT_URL = "http://0.0.0.0:9126/test/session"
TESTAGENT_TOKEN = "pygoat_test"
TESTAGENT_HEADERS = {"X-Datadog-Test-Session-Token": TESTAGENT_TOKEN}
TESTAGENT_TOKEN_PARAM = "?test_session_token=" + TESTAGENT_TOKEN


@pytest.fixture(autouse=False)
def client():
    agent_client = requests.session()
    reply = agent_client.get(TESTAGENT_URL + "/start" + TESTAGENT_TOKEN_PARAM, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    pygoat_client, token = login_to_pygoat()

    class RetClient:
        agent_session = agent_client
        pygoat_session = pygoat_client
        csrftoken = token

    return RetClient


def start_testagent_session():
    client = requests.session()
    reply = client.get(TESTAGENT_URL + "/start" + TESTAGENT_TOKEN_PARAM, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    return client


def login_to_pygoat():
    client = requests.session()
    LOGIN_URL = PYGOAT_URL + "/login/"

    client.get(LOGIN_URL)
    csrftoken = client.cookies["csrftoken"]

    login_data = {"username": "admin", "password": "adminpassword", "csrfmiddlewaretoken": csrftoken}
    reply = client.post(LOGIN_URL, data=login_data, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    csrftoken = client.cookies["csrftoken"]
    return client, csrftoken


def get_traces(agent_client: requests.Session) -> requests.Response:
    return agent_client.get(TESTAGENT_URL + "/traces" + TESTAGENT_TOKEN_PARAM, headers=TESTAGENT_HEADERS)


def vulnerability_in_traces(vuln_type: str, agent_client: requests.Session) -> bool:
    time.sleep(5)
    traces = get_traces(agent_client)
    assert traces.status_code == 200
    traces_list = json.loads(traces.text)

    class InnerBreakException(Exception):
        pass

    try:
        for trace in traces_list:
            for trace_dict in trace:
                if "meta" not in trace_dict:
                    continue
                iast_data = trace_dict["meta"].get("_dd.iast.json")
                if not iast_data:
                    continue

                vulnerabilities = json.loads(iast_data).get("vulnerabilities")
                if not vulnerabilities:
                    continue

                for vuln in vulnerabilities:
                    if vuln["type"] == vuln_type:
                        raise InnerBreakException()
    except InnerBreakException:
        return True

    return False


def test_insecure_cookie(client):
    payload = {"name": "admin", "pass": "adminpassword", "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/sql_lab", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("INSECURE_COOKIE", client.agent_session)


def test_nohttponly_cookie(client):
    payload = {"email": "test@test.com", "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/otp", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("NO_HTTPONLY_COOKIE", client.agent_session)


@flaky(1735812000)
def test_weak_random(client):
    reply = client.pygoat_session.get(PYGOAT_URL + "/otp?email=test%40test.com", headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("WEAK_RANDOMNESS", client.agent_session)


def test_weak_hash(client):
    payload = {"username": "admin", "password": "adminpassword", "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(
        PYGOAT_URL + "/cryptographic_failure/lab", data=payload, headers=TESTAGENT_HEADERS
    )
    assert reply.status_code == 200
    assert vulnerability_in_traces("WEAK_HASH", client.agent_session)


def test_cmdi(client):
    payload = {"domain": "google.com && ls", "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/cmd_lab", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("COMMAND_INJECTION", client.agent_session)


def test_sqli(client):
    payload = {"name": "admin", "pass": "anything' OR '1' ='1", "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/sql_lab", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("SQL_INJECTION", client.agent_session)


@pytest.mark.skip("TODO: SSRF is not implemented for open()")
def test_ssrf1(client, tracer, iast_span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    s = "templates/Lab/ssrf/blogs/blog2.txt"
    tainted_path = taint_pyobject(
        pyobject=s,
        source_name="test_ssrf",
        source_value=s,
        source_origin=OriginType.PARAMETER,
    )
    payload = {"blog": tainted_path, "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/ssrf_lab", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("SSRF", client.agent_session)


def test_ssrf2(client, tracer, span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    s = "http://example.com"
    tainted_path = taint_pyobject(
        pyobject=s,
        source_name="test_ssrf",
        source_value=s,
        source_origin=OriginType.PARAMETER,
    )
    payload = {"url": tainted_path, "csrfmiddlewaretoken": client.csrftoken}
    reply = client.pygoat_session.post(PYGOAT_URL + "/ssrf_lab2", data=payload, headers=TESTAGENT_HEADERS)
    assert reply.status_code == 200
    assert vulnerability_in_traces("SSRF", client.agent_session)
