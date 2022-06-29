import pytest

from ddtrace.internal.glob_matching import GlobMatcher


@pytest.mark.parametrize(
    "pattern,string,result",
    [
        ("test_string", "test_string", True),
        ("test_string", "a_test_string_a", False),
        ("test_st?ing", "test_string", True),
        ("test_st?i?g", "test_string", True),
        ("test_str*", "test_string", True),
        ("t?st_str*", "test_string", True),
        ("t?st_str*", "test_string", True),
        ("?est_string", "test_string", True),
        ("test_strin?", "test_string", True),
        ("te?st_string", "test_string", False),  # Test empty string for ?
        ("test_s*ring", "test_string", True),  # Test empty string for *
        ("foo.*", "foo.you", True),
        ("foo.*", "snafoo", False),
        ("*stuff", "lots of stuff", True),
        ("*stuff", "stuff to think about", False),
        ("*a*a*a*a*a*a", "aaaaaaaaaaaaaaaaaaaaaaaaaax", False),
        ("*a*a*a*a*a*a", "aaaaaaaarrrrrrraaaraaarararaarararaarararaaa", True),
    ],
)
def test_matching(pattern, string, result):
    glob_matcher = GlobMatcher(pattern)
    assert result == glob_matcher.match(string)
