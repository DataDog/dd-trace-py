import base64
import json
import os
from typing import Any
from typing import Generator
from typing import Tuple

import attr
import pytest

from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.utils.formats import parse_tags_str
from tests.utils import request_token


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter()
    # Enable the TelemetryWriter without queuing an app-started event
    # and setting up exit hooks
    telemetry_writer._enabled = True
    yield telemetry_writer


@attr.s
class TelemetryTestSession(object):
    token = attr.ib(type=str)
    telemetry_writer = attr.ib(type=TelemetryWriter)

    def create_connection(self):
        parsed = parse.urlparse(self.telemetry_writer._agent_url)
        return httplib.HTTPConnection(parsed.hostname, parsed.port)

    def _request(self, method, url):
        # type: (str, str) -> Tuple[int, bytes]
        conn = self.create_connection()
        try:
            conn.request(method, url)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def clear(self):
        status, _ = self._request("GET", "/test/session/clear?test_session_token=%s" % self.token)
        if status != 200:
            pytest.fail("Failed to clear session: %s" % self.token)
        return True

    def get_requests(self):
        """Get a list of the requests sent to the test agent

        Results are in reverse order by ``seq_id``
        """
        status, body = self._request("GET", "/test/session/requests?test_session_token=%s" % self.token)
        if status != 200:
            pytest.fail("Failed to fetch session requests: %s" % self.token)
        requests = json.loads(body.decode("utf-8"))
        for req in requests:
            body_str = base64.b64decode(req["body"]).decode("utf-8")
            req["body"] = json.loads(body_str)

        return sorted(requests, key=lambda r: r["body"]["seq_id"], reverse=True)

    def get_events(self):
        """Get a list of the event payloads sent to the test agent

        Results are in reverse order by ``seq_id``
        """
        status, body = self._request("GET", "/test/session/apmtelemetry?test_session_token=%s" % self.token)
        if status != 200:
            pytest.fail("Failed to fetch session events: %s" % self.token)
        return sorted(json.loads(body.decode("utf-8")), key=lambda e: e["seq_id"], reverse=True)


@pytest.fixture
def test_agent_session(telemetry_writer, request):
    # type: (TelemetryWriter, Any) -> Generator[TelemetryTestSession, None, None]
    token = request_token(request)

    telemetry_writer._headers["X-Datadog-Test-Session-Token"] = token

    # Also add a header to the environment for subprocesses test cases that might use snapshotting.
    existing_headers = parse_tags_str(os.environ.get("_DD_TELEMETRY_WRITER_ADDITIONAL_HEADERS", ""))
    existing_headers.update({"X-Datadog-Test-Session-Token": token})
    os.environ["_DD_TELEMETRY_WRITER_ADDITIONAL_HEADERS"] = ",".join(
        ["%s:%s" % (k, v) for k, v in existing_headers.items()]
    )

    requests = TelemetryTestSession(token=token, telemetry_writer=telemetry_writer)

    conn = requests.create_connection()
    try:
        conn.request("GET", "/test/session/start?test_session_token=%s" % token)
        r = conn.getresponse()
        print(r.read())
    finally:
        conn.close()

    try:
        yield requests
    finally:
        # Force a flush
        telemetry_writer.periodic()
        del telemetry_writer._headers["X-Datadog-Test-Session-Token"]
        del os.environ["_DD_TELEMETRY_WRITER_ADDITIONAL_HEADERS"]
