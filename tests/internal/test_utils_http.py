from unittest import mock

import pytest

from ddtrace.internal.http import HTTPResponse
from ddtrace.internal.http import NativeHTTPConnection
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import parse_form_multipart


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_connector(scheme):
    # The native Rust HTTP client bypasses Python's socket module, so
    # httpretty-style socket-level mocking doesn't work. Mock at the
    # NativeHTTPConnection level instead.
    mock_resp = mock.Mock(spec=HTTPResponse)
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"hello": "world"}'

    with mock.patch.object(NativeHTTPConnection, "getresponse", return_value=mock_resp):
        connect = connector("%s://localhost:8181" % scheme)
        with connect() as conn:
            conn.request("GET", "/api/test")
            response = conn.getresponse()
            assert response.status == 200
            assert response.read() == b'{"hello": "world"}'


def test_parse_form_multipart_handles_charset_suffix() -> None:
    body = (
        "--BOUNDARY\r\n"
        'Content-Disposition: form-data; name="meta"\r\n'
        "Content-Type: application/json; charset=utf-8\r\n"
        "\r\n"
        '{"id": 42}\r\n'
        "--BOUNDARY\r\n"
        'Content-Disposition: form-data; name="raw"\r\n'
        "Content-Type: application/json\r\n"
        "\r\n"
        '{"id": 99}\r\n'
        "--BOUNDARY--\r\n"
    )
    headers = {"Content-Type": "multipart/form-data; boundary=BOUNDARY"}
    parsed = parse_form_multipart(body, headers)

    # Both parts (with and without the charset suffix) must be JSON-parsed,
    # not collapsed to empty strings.
    assert parsed["meta"] == {"id": 42}
    assert parsed["raw"] == {"id": 99}
