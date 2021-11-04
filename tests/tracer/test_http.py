from hypothesis import example
from hypothesis import given
from hypothesis.provisional import urls

from ddtrace.internal.compat import parse
from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.internal.utils.http import strip_query_string


class TestHeaderNameNormalization(object):
    def test_name_is_trimmed(self):
        assert normalize_header_name("   content-type   ") == "content-type"

    def test_name_is_lowered(self):
        assert normalize_header_name("Content-Type") == "content-type"

    def test_none_does_not_raise_exception(self):
        assert normalize_header_name(None) is None

    def test_empty_does_not_raise_exception(self):
        assert normalize_header_name("") == ""


@given(urls())
@example("/relative/path")
@example("")
@example("#fragment?with=query&string")
@example(":")
@example(":/")
@example("://?")
@example("://?&?")
@example("://?&#")
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
