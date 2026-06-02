import httpretty
import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import parse_form_multipart


parameters = ["http"]
# httpretty doesn't work with https/http.client/Python 3.14 and there is no apparent replacement
if PYTHON_VERSION_INFO < (3, 14):
    parameters.append("https")


@pytest.mark.parametrize("scheme", parameters)
def test_connector(scheme):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.GET, "%s://localhost:8181/api/test" % scheme, body='{"hello": "world"}')

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
