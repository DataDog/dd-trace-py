import pytest

from ddtrace.appsec import _http_utils


@pytest.mark.parametrize(
    "input_headers, expected",
    [
        ({"Host": "Example.COM"}, {"host": "Example.COM"}),
        (
            {"X-Custom-None": "", "Content-Type": "application/json", "X-Custom-Spacing ": " trim spaces  "},
            {"x-custom-none": None, "content-type": "application/json", "x-custom-spacing": "trim spaces"},
        ),
    ],
)
def test_normalize_headers(input_headers, expected):
    result = _http_utils.normalize_headers(input_headers)
    assert result == expected


@pytest.mark.parametrize(
    "headers, body, is_body_base64, expected_output",
    [
        # Body is None
        ({}, None, False, None),
        # Base64 encoded body - text/plain
        (
            {"content-type": "text/plain"},
            "dGV4dCBib2R5",
            True,
            "text body",
        ),
        # Base64 encoded body - application/json
        (
            {"content-type": "application/json"},
            "eyJrZXkiOiAidmFsdWUifQ==",
            True,
            {"key": "value"},
        ),
        # Base64 decoding failure - text/plain
        (
            {"content-type": "text/plain"},
            "invalid_base64_string",
            True,
            None,
        ),
        # JSON content types
        ({"content-type": "application/json"}, '{"key": "value"}', False, {"key": "value"}),
        ({"content-type": "application/vnd.api+json"}, '{"key": "value"}', False, {"key": "value"}),
        ({"content-type": "text/json"}, '{"key": "value"}', False, {"key": "value"}),
        # Form urlencoded
        (
            {"content-type": "application/x-www-form-urlencoded"},
            "key=value&key2=value2",
            False,
            {"key": ["value"], "key2": ["value2"]},
        ),
        # XML content types
        ({"content-type": "application/xml"}, "<root><key>value</key></root>", False, {"root": {"key": "value"}}),
        ({"content-type": "text/xml"}, "<root><key>value</key></root>", False, {"root": {"key": "value"}}),
        # Text plain
        ({"content-type": "text/plain"}, "simple text body", False, "simple text body"),
        # Unsupported content type
        ({"content-type": "application/octet-stream"}, "binary data", False, None),
        # No content type provided
        ({}, "some body", False, None),
        # Invalid JSON
        ({"content-type": "application/json"}, "not a valid json string", False, None),
        # Invalid XML
        ({"content-type": "application/xml"}, "<root><key>value</missing_key></root>", False, None),
        # Multipart form data
        (
            {"content-type": "multipart/form-data; boundary=boundary"},
            (
                "--boundary\r\n"
                'Content-Disposition: form-data; name="formPart"\r\n'
                "content-type: application/x-www-form-urlencoded\r\n"
                "\r\n"
                "key=value\r\n"
                "--boundary--"
            ),
            False,
            {"formPart": {"key": ["value"]}},  # Mocked return value for parse_form_multipart
        ),
        # Invalid base64 encoded body (decoding fails)
        (
            {"content-type": "application/xml"},
            "invalid_base64_and_invalid_xml",
            True,
            None,
        ),
    ],
)
def test_parse_http_body(headers, body, is_body_base64, expected_output, mocker):
    result = _http_utils.parse_http_body(headers, body, is_body_base64)
    assert result == expected_output


@pytest.mark.parametrize(
    "input_headers, expected",
    [
        (
            {"cookie": "sessionid=abc123; csrftoken=xyz789"},
            {"sessionid": "abc123", "csrftoken": "xyz789"},
        ),
        (
            {"set-cookie": "sessionid=abc123; Path=/; HttpOnly"},
            {"sessionid": "abc123"},
        ),
        ({"cookie": ""}, {}),
        ({"cookie": None}, {}),
        ({"set-cookie": None}, {}),
    ],
)
# Tests for extract_cookies_from_headers
def test_extract_cookies_from_headers(input_headers, expected):
    result = _http_utils.extract_cookies_from_headers(input_headers)
    assert result == expected
