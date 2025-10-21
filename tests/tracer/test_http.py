import os
import re
from urllib import parse

import pytest

from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.internal.utils.http import redact_url
from ddtrace.internal.utils.http import strip_query_string


def _url_fixtures():
    filename = os.path.join(os.path.dirname(__file__), "fixtures", "urls.txt")
    with open(filename) as fp:
        for line in fp:
            yield line.strip()


class TestHeaderNameNormalization(object):
    def test_name_is_trimmed(self):
        assert normalize_header_name("   content-type   ") == "content-type"

    def test_name_is_lowered(self):
        assert normalize_header_name("Content-Type") == "content-type"

    def test_none_does_not_raise_exception(self):
        assert normalize_header_name(None) is None

    def test_empty_does_not_raise_exception(self):
        assert normalize_header_name("") == ""


@pytest.mark.parametrize("url", _url_fixtures())
def test_strip_query_string(url):
    parsed_url = parse.urlparse(url)
    assert strip_query_string(url) == parse.urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            None,
            parsed_url.fragment,
        )
    )


@pytest.mark.parametrize("url", _url_fixtures())
def test_redact_url_not_redacts_without_param(url):
    res = redact_url(url, re.compile("\\@"), None)
    assert res == url


@pytest.mark.parametrize("url", _url_fixtures())
def test_redact_url_not_redacts_with_param(url):
    parsed_url = parse.urlparse(url)
    assert redact_url(url, re.compile("\\*"), "query_string") == parse.urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            "query_string",
            parsed_url.fragment,
        )
    )


@pytest.mark.parametrize(
    "url, regex, query_string, expected",
    (
        ("://?&?", re.compile("\\?"), None, "://?&<redacted>"),
        ("://?&?", re.compile("\\?"), None, "://?&<redacted>"),
        ("://?x", re.compile("x"), None, "://?<redacted>"),
        ("://x", re.compile("x"), "x", "://x?<redacted>"),
        ("://y", re.compile("x"), "x", "://y?<redacted>"),
    ),
)
def test_redact_url_does_redact(url, regex, query_string, expected):
    assert redact_url(url, regex, query_string) == expected
