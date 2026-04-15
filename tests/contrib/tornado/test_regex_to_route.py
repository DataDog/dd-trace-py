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
        ("/(?<=v)/([0-9]+)/$", "/(?<=v)/%s/"),
        ("/(?<!beta)/([0-9]+)/$", "/(?<!beta)/%s/"),
        # --- Atomic group (?>...) kept verbatim — non-capturing, no backtracking ---
        ("/(?>[a-z]+)/([0-9]+)/$", "/(?>[a-z]+)/%s/"),
        # --- Conditional group (?(id)yes|no) kept verbatim ---
        # Note: (a)? — the capturing group (a) becomes %s, the ? quantifier stays.
        ("/(a)?/b(?(1)c|d)/$", "/%s?/b(?(1)c|d)/"),
        # --- Named backreference (?P=name) kept verbatim — not a capturing group ---
        ("/(?P<word>[a-z]+)/(?P=word)/$", "/%s/(?P=word)/"),
        # --- Inline comment (?#...) removed entirely — matches nothing ---
        ("/foo(?#this is a comment)/([0-9]+)/$", "/foo/%s/"),
        ("/foo/(?#comment)([0-9]+)/$", "/foo/%s/"),
        # --- Nested groups: outer capturing → %s, content (including inner) dropped ---
        ("/a/(b([0-9]+))/$", "/a/%s/"),
        # --- Escaped parens are not treated as group delimiters ---
        (r"/items/\([0-9]+\)/$", r"/items/\([0-9]+\)/"),
        # --- Character classes: '(' inside '[…]' is literal, not a group ---
        ("/foo/[()]/([0-9]+)/$", "/foo/[()]/%s/"),
        ("/bar/[^()]+/([0-9]+)/$", "/bar/[^()]+/%s/"),
        # A ']' right after '[' is a literal ']' inside the class.
        ("/baz/[]()]/([0-9]+)/$", "/baz/[]()]/%s/"),
    ],
)
def test_regex_to_route(pattern, expected):
    assert _regex_to_route(pattern) == expected
