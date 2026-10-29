from unittest import mock

import pytest

from ddtrace.internal.http import HTTPResponse
from ddtrace.internal.http import NativeHTTPConnection
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import inspectable_charset
from ddtrace.internal.utils.http import is_punycode_charset
from ddtrace.internal.utils.http import is_slow_charset
from ddtrace.internal.utils.http import is_utf8_charset
from ddtrace.internal.utils.http import parse_form_multipart
from ddtrace.internal.utils.http import parse_json_bytes


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


@pytest.mark.parametrize(
    ("charset", "size", "expected"),
    [
        ("Latin-1", 0, "iso8859-1"),
        ("UTF-7", 0, "utf-7"),
        (None, 0, "utf-8"),
        ("", 0, "utf-8"),
        ("unknown-charset", 0, "utf-8"),
        ("\udcff", 0, "utf-8"),
        (("utf-8", "", "utf-8"), 0, "utf-8"),
        ("hex", 0, "utf-8"),
        ("zlib", 0, "utf-8"),
        ("utf.16be", 0, "utf-16-be"),
        ("ebcdic.cp.us", 0, "cp037"),
        ("ebcdic\u00e9cp\u00e9us", 0, "cp037"),
        ("ebcdic\u212acp\u212aus", 0, "cp037"),
        ("utf\u013016\u0130be", 0, "utf-16-be"),
        ("cp037" + "-" * 60, 0, "cp037"),
        ("cp875", 0, "cp875"),
        ("unicode_escape", 0, "unicode-escape"),
        ("punycode", 100, "punycode"),
        ("punycode", 100_000, "utf-8"),
    ],
)
def test_inspectable_charset(charset, size, expected) -> None:
    assert inspectable_charset(charset, size) == expected


def test_inspectable_charset_does_not_cache_unknown_names() -> None:
    import encodings

    before = len(encodings._cache)
    for i in range(100):
        assert inspectable_charset(f"x-unknown-charset-{i}") == "utf-8"
        inspectable_charset("utf" + "\u00e9" * (i + 1) + "8")
        inspectable_charset("utf." * (i + 1) + "8")
    assert len(encodings._cache) == before


@pytest.mark.parametrize(
    ("charset", "expected"), [("utf-8", True), ("UTF8", True), ("utf_8", True), ("latin-1", False)]
)
def test_is_utf8_charset(charset, expected) -> None:
    assert is_utf8_charset(charset) is expected


@pytest.mark.parametrize(("charset", "expected"), [("punycode", True), ("idna", True), ("utf-8", False)])
def test_is_slow_charset(charset, expected) -> None:
    assert is_slow_charset(charset) is expected


@pytest.mark.parametrize(
    ("charset", "expected"), [("punycode", True), (" PunyCode ", True), ("utf-8", False), (None, False)]
)
def test_is_punycode_charset(charset, expected) -> None:
    assert is_punycode_charset(charset) is expected


@pytest.mark.parametrize(
    ("data", "charset", "punycode", "expected"),
    [
        (b'{"a": 1}', "utf-8", False, {"a": 1}),
        ('{"a": "\u00e9"}'.encode("utf-16"), "utf-16", False, {"a": "\u00e9"}),
        (b'{"a": 1}', "utf-16-be", False, {"a": 1}),
        ('{"a": "\u00e9"}'.encode("punycode"), "punycode", True, {"a": "\u00e9"}),
        (b'{"a": 1}-!!', "punycode", True, {"a": 1}),
    ],
)
def test_parse_json_bytes(data, charset, punycode, expected) -> None:
    assert parse_json_bytes(data, charset, punycode) == expected


@pytest.mark.parametrize(("data", "charset", "punycode"), [(b"{bad", "utf-8", False), (b'{"a": 1}-!!', "utf-8", False)])
def test_parse_json_bytes_invalid(data, charset, punycode) -> None:
    with pytest.raises(ValueError):
        parse_json_bytes(data, charset, punycode)
