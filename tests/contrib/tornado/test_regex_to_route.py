import pytest

from ddtrace.contrib.internal.tornado.handlers import _regex_to_route


@pytest.mark.parametrize(
    "pattern,expected",
    [
        # --- Tornado anchor stripping ---
        # Tornado always appends "$"; leading "^" may also be present.
        ("/success/$", "/success/"),
        ("^/success/$", "/success/"),
        # --- No groups: pattern kept verbatim (minus anchors) ---
        ("/static/", "/static/"),
        ("/items/new/", "/items/new/"),
        # --- Positional capturing groups → %s ---
        ("/status_code/([0-9]+)$", "/status_code/%s"),
        ("/items/([0-9]+)/([a-z]+)/$", "/items/%s/%s/"),
        # --- Named capturing groups (?P<name>...) → %s ---
        ("/items/(?P<id>[0-9]+)/$", "/items/%s/"),
        ("/a/(?P<x>[0-9]+)/b/(?P<y>[a-z]+)/$", "/a/%s/b/%s/"),
        # --- Non-capturing groups kept verbatim ---
        ("/complex/(?:new|existing)/$", "/complex/(?:new|existing)/"),
        # --- Mixed: non-capturing + capturing → non-capturing kept, capturing → %s ---
        ("/mixed/(?:items|things)/([0-9]+)/$", "/mixed/(?:items|things)/%s/"),
        # --- Lookaheads / lookbehinds kept verbatim ---
        ("/(?=items)[a-z]+/$", "/(?=items)[a-z]+/"),
        ("/(?!admin)[a-z]+/$", "/(?!admin)[a-z]+/"),
        # --- Nested groups: outer capturing → %s, content (including inner) dropped ---
        ("/a/(b([0-9]+))/$", "/a/%s/"),
        # --- Escaped parens are not treated as group delimiters ---
        (r"/items/\([0-9]+\)/$", r"/items/\([0-9]+\)/"),
    ],
)
def test_regex_to_route(pattern, expected):
    assert _regex_to_route(pattern) == expected
