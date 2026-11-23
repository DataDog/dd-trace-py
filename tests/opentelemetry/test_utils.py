from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
from threading import Event

from tests.opentelemetry.utils import _capture_http_requests


def test_http_receiver_captures_before_acknowledging(monkeypatch):
    release_first_handler = Event()
    original_end_headers = BaseHTTPRequestHandler.end_headers

    def end_headers(handler):
        original_end_headers(handler)
        if handler.path == "/first":
            # Keep the handler paused after the client has received its response.
            assert release_first_handler.wait(timeout=5)

    monkeypatch.setattr(BaseHTTPRequestHandler, "end_headers", end_headers)
    payloads = [b"first", b"second"]
    with _capture_http_requests(0) as requests:
        try:
            for index, payload in enumerate(payloads):
                with closing(HTTPConnection("127.0.0.1", requests.port, timeout=5)) as connection:
                    connection.request("POST", f"/{payload.decode()}", body=payload)
                    response = connection.getresponse()
                    assert response.status == 200
                    response.read()
                assert requests == payloads[: index + 1]
        finally:
            release_first_handler.set()

    assert requests == payloads
