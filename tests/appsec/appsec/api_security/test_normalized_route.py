import pytest

from ddtrace.appsec._api_security._normalized_route import normalize_route
from ddtrace.appsec._api_security._normalized_route import normalize_route_django


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # RFC examples - FastAPI
        (
            "/dashboard/shared_widget_update/{id}/{widget_id}",
            "/dashboard/shared_widget_update/{id}/{widget_id}",
        ),
        # Single-param segment with static glue (rule 5: single dynamic, with or without static text)
        ("/users/user-{id}", "/users/{id}"),
        # Single-param segment with both prefix and suffix static text — still rule 5 single-param.
        ("/users/u-{id}-x", "/users/{id}"),
        # Multi-param segment combined with `+` (rule 5)
        ("/photos/{id}.{format}", "/photos/{id+format}"),
        ("/files/{a}.{b}.{c}", "/files/{a+b+c}"),
        # Converter types are stripped from the param name
        ("/sleep/{seconds:int}", "/sleep/{seconds}"),
        ("/asm/{param_int:int}/{param_str:str}", "/asm/{param_int}/{param_str}"),
        # Catch-all path converter: rule 5 catch-all exception
        ("/files/{file_path:path}", "/files/{file_path}"),
        ("/{tail:path}", "/{tail}"),
        # Catch-all with in-segment static prefix: entire tail is one atomic element
        ("/files/file-{tail:path}", "/files/{tail}"),
        # Trailing slash preserved when declared (rule 1)
        ("/api/", "/api/"),
        ("/api/{v}/", "/api/{v}/"),
        # Rule 1 also applies on catch-all routes: declared trailing slash must be preserved.
        ("/files/{file_path:path}/", "/files/{file_path}/"),
        ("/{tail:path}/", "/{tail}/"),
        ("/files/file-{tail:path}/", "/files/{tail}/"),
        # Root only
        ("/", "/"),
        # Static-constant URL-encoding (rule 3): unsafe chars encoded, safe set preserved
        ("/path with space", "/path%20with%20space"),
        ("/safe.-~_", "/safe.-~_"),
        ("/é", "/%C3%A9"),
    ],
)
def test_normalize_route_happy_path(route, expected):
    assert normalize_route(route) == expected


@pytest.mark.parametrize(
    "route",
    [
        None,
        "",
        "no-leading-slash",
        "/double//slash",
        "/{tail:path}/and-after",  # catch-all not at end
    ],
)
def test_normalize_route_returns_none_on_invalid(route):
    assert normalize_route(route) is None


def test_normalize_route_path_params_argument_is_accepted_but_unused():
    # Starlette/FastAPI have no optional path elements. The signature accepts `path_params` for parity with frameworks
    # that do; passing it must not change the result.
    assert normalize_route("/users/{id}", {"id": 42}) == "/users/{id}"
    assert normalize_route("/users/{id}", None) == "/users/{id}"


def test_normalize_route_param_name_with_reserved_char_is_url_encoded():
    # Rule 4: framework-supplied param names containing /?#+{} must be URL-encoded. Starlette's PARAM_REGEX prevents
    # this from arising naturally — exercise the internal encoder directly to keep coverage.
    from ddtrace.appsec._api_security._normalized_route import _encode_param_name

    assert _encode_param_name("foo+bar") == "foo%2Bbar"
    assert _encode_param_name("a/b") == "a%2Fb"
    assert _encode_param_name("clean_name") == "clean_name"


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # RFC examples - Django
        ("^dump-request$", "/dump-request"),
        ("sleep/<int:seconds>", "/sleep/{seconds}"),
        # path() converters are stripped to the bare parameter name.
        ("asm/<int:param_int>/<str:param_str>/", "/asm/{param_int}/{param_str}/"),
        ("asm/<int:param_int>/<str:param_str>", "/asm/{param_int}/{param_str}"),
        # No-converter shorthand (`<name>`) defaults to the str converter.
        ("users/<name>/", "/users/{name}/"),
        # `<path:...>` catch-all (Django's only multi-segment converter): rule 5 exception emits a single tail element.
        ("files/<path:file_path>", "/files/{file_path}"),
        # Catch-all with in-segment static prefix: entire tail is one atomic element.
        ("files/file-<path:tail>", "/files/{tail}"),
        # Rule 1: declared trailing slash on a catch-all is preserved.
        ("files/<path:file_path>/", "/files/{file_path}/"),
        # Multi-param-in-segment: rule 5 combines names with `+`.
        ("multi-param/<str:first>.<str:last>/", "/multi-param/{first+last}/"),
        # re_path() roots: `^$` becomes the bare root.
        ("^$", "/"),
        # re_path() with optional trailing slash `/?$` — not "declared with a trailing slash" (rule 1), so dropped.
        ("^asm/?$", "/asm"),
        # re_path() with named groups; regex bodies (char classes, quantifiers) discarded — only the name survives.
        (r"^asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)/$", "/asm/{param_int}/{param_str}/"),
        (r"^asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)$", "/asm/{param_int}/{param_str}"),
        # Static-constant URL-encoding (rule 3).
        ("path with space", "/path%20with%20space"),
        ("safe.-~_", "/safe.-~_"),
    ],
)
def test_normalize_route_django_happy_path(route, expected):
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    "route",
    [
        None,
        "",
        # consecutive slashes are illegal per rule 2
        "double//slash",
        # `<path:...>` catch-all must be the final segment
        "files/<path:tail>/and-after",
        # ...and must be the last atom of that segment — a static suffix is impossible in Django (path: to end).
        "files/<path:tail>extra",
        # Unnamed regex group — we don't guess a placeholder, omit the tag.
        "asm/(?:nogrp)",
        # Bare character class outside a named group — same rationale.
        "[abc]",
        # Malformed path() converter.
        "asm/<unterminated",
        # Reserved Django converter syntax: empty name.
        "asm/<int:>",
        # re_path-shaped routes (detected by ``^``/``$``/``(?P<``) reject top-level regex meta outside named groups
        # rather than emit them as URL-encoded literals.
        "^foo|bar$",  # alternation
        "^foo+$",  # quantifier on previous literal
        "^foo*$",  # zero-or-more quantifier
        # Mixed: re_path-shaped (has ``(?P<``) with stray meta on another segment.
        r"^(?P<id>\d+)/foo|bar$",
    ],
)
def test_normalize_route_django_returns_none_on_invalid(route):
    assert normalize_route_django(route) is None


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # path()-shaped routes treat regex metachars as URL literals (rule 3). Only re_path-shaped routes reject them.
        ("foo+bar/<int:id>", "/foo%2Bbar/{id}"),
        ("foo*bar/<int:id>", "/foo%2Abar/{id}"),
        ("foo?bar/<int:id>", "/foo%3Fbar/{id}"),
        ("foo|bar/<int:id>", "/foo%7Cbar/{id}"),
    ],
)
def test_normalize_route_django_meta_chars_literal_in_path_routes(route, expected):
    assert normalize_route_django(route) == expected


def test_normalize_route_django_required_params_with_path_params():
    # When every declared param resolves to a non-empty value (the common case), the result must match the
    # no-path_params output. Required params always have a non-empty binding, so the filter is a no-op here.
    assert normalize_route_django("users/<int:id>", {"id": 42}) == "/users/{id}"
    assert normalize_route_django("users/<int:id>", None) == "/users/{id}"
    # An integer 0 is a legitimate value — not empty, not None.
    assert normalize_route_django("sleep/<int:seconds>", {"seconds": 0}) == "/sleep/{seconds}"


@pytest.mark.parametrize(
    ("route", "path_params", "expected"),
    [
        # re_path() optional named group that didn't match: ``path_params``
        # carries it as ``None`` (or missing key, or empty string). Drop the
        # param so the normalized route reflects the URL actually served.
        (r"^posts/(?P<id>\d+)?$", {"id": None}, "/posts"),
        (r"^posts/(?P<id>\d+)?$", {"id": ""}, "/posts"),
        (r"^posts/(?P<id>\d+)?$", {}, "/posts"),
        (r"^posts/(?P<id>\d+)?$", {"id": "42"}, "/posts/{id}"),
        # Multi-param-in-segment with one optional absent → combined name collapses to remaining params (rule 5).
        ("<a>.<b>.<c>/", {"a": "x", "b": "y", "c": None}, "/{a+b}/"),
        ("<a>.<b>.<c>/", {"a": "x", "b": None, "c": "z"}, "/{a+c}/"),
        # Two of three absent → single remaining param stands alone (no `+`).
        ("<a>.<b>.<c>/", {"a": None, "b": "y", "c": None}, "/{b}/"),
        # All absent → segment disappears (URL had nothing matching it). Trailing slash also vanishes.
        ("<a>.<b>.<c>/", {"a": None, "b": None, "c": None}, "/"),
        # Catch-all that didn't bind (rare; possible with custom converters) → drop the trailing segment entirely.
        ("files/<path:tail>", {"tail": None}, "/files"),
        ("files/<path:tail>", {"tail": ""}, "/files"),
    ],
)
def test_normalize_route_django_drops_absent_params(route, path_params, expected):
    assert normalize_route_django(route, path_params) == expected


def test_normalize_route_django_include_joined_route():
    # When Django joins parent + child via `include()`, the child's leading `^` is stripped, so a regex sub-urlconf
    # mounted under a path() parent arrives as a single mixed string. The normalizer still produces a valid output.
    assert normalize_route_django(r"asm/(?P<param_int>[0-9]+)/(?P<param_str>\w+)/$") == "/asm/{param_int}/{param_str}/"


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Flat unnamed groups → auto-numbered ``paramN`` placeholders, in declaration order (RFC-1103 rule 4).
        (r"^a/(\d+)/(\w+)$", "/a/{param1}/{param2}"),
        # Optional unnamed without path_params: treat as required (no info).
        (r"^a/(\d+)?/(\w+)$", "/a/{param1}/{param2}"),
        # Multiple unnamed in one segment combine with ``+`` (rule 5).
        (r"^a/(\d+)\.(\w+)$", "/a/{param1+param2}"),
        # Nested capture: outer wins; inner is silently counted-but-not-emitted so ``param2`` aligns with sibling.
        (r"^a/((\d)+x)/(a|b)$", "/a/{param1}/{param2}"),
        # Nested with optional outer.
        (r"^a/((\d)+x)?/(a|b)$", "/a/{param1}/{param2}"),
        # ``(.*)`` is just an unnamed flat capture; no special multi-segment handling without ``path:``.
        (r"^prefix-(\d+)$", "/{param1}"),
    ],
)
def test_normalize_route_django_unnamed_happy_path(route, expected):
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    ("route", "args_tuple", "expected"),
    [
        # Required: both present.
        (r"^a/(\d+)/(\w+)$", ("42", "abc"), "/a/{param1}/{param2}"),
        # First optional didn't match → drop param1.
        (r"^a/(\d+)?/(\w+)$", (None, "abc"), "/a/{param2}"),
        # Both optional, neither matched → segment(s) drop, root remains.
        (r"^a/(\d+)?/(\w+)?$", (None, None), "/a"),
        # Empty-string captures count as absent (zero-width regex match).
        (r"^a/(\d*)?/(\w+)$", ("", "abc"), "/a/{param2}"),
        # Multi-param-in-segment: filtering one drops it from the combined name.
        (r"^a/(\d+)\.(\w+)$", ("42", None), "/a/{param1}"),
        # Nested unnamed: param2 must look at args[2] (sibling), not args[1] (inner).
        (r"^a/((\d)+x)/(a|b)$", ("55x", "5", "a"), "/a/{param1}/{param2}"),
        # Outer optional didn't match: args = (None, None, "a").
        (r"^a/((\d)+x)?/(a|b)$", (None, None, "a"), "/a/{param2}"),
    ],
)
def test_normalize_route_django_unnamed_filters_by_args(route, args_tuple, expected):
    assert normalize_route_django(route, args_tuple) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Named group called ``param1`` → next unnamed becomes ``param2``.
        (r"^(?P<param1>\d+)/(\w+)$", "/{param1}/{param2}"),
        # ``param2`` taken → unnamed groups get ``param1``, ``param3`` (skipping 2).
        (r"^(?P<param2>\d+)/(\w+)/(\d+)$", "/{param2}/{param1}/{param3}"),
        # Multiple named ``paramK`` reservations.
        (r"^(?P<param1>\d+)/(?P<param2>\d+)/(\w+)/(\w+)$", "/{param1}/{param2}/{param3}/{param4}"),
    ],
)
def test_normalize_route_django_unnamed_avoids_paramN_collisions(route, expected):
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # All-required ``path()`` shapes go through the regex fast path. The output is identical to the slow path —
        # only the runtime cost differs (cached string vs. per-request parse+assemble).
        ("asm/<int:param_int>/<str:param_str>/", "/asm/{param_int}/{param_str}/"),
        ("asm/<int:param_int>/<str:param_str>", "/asm/{param_int}/{param_str}"),
        ("sleep/<int:seconds>", "/sleep/{seconds}"),
        ("users/<name>/", "/users/{name}/"),
        ("login/", "/login/"),
        ("login", "/login"),
    ],
)
def test_normalize_route_django_fast_path_pure_path_routes(route, expected):
    from ddtrace.appsec._api_security._normalized_route import _normalize_route_django_fast_path

    assert _normalize_route_django_fast_path(route) == expected
    # Public API returns the same value (fast path is wired in front of the slow parse).
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    "route",
    [
        # Regex-shaped: anchors, optional slash, named/unnamed groups.
        "^$",
        "^asm/?$",
        r"^asm/(?P<id>\d+)$",
        r"^a/(\d+)$",
        # Multi-param-in-segment: slow path applies rule 5 combining.
        "multi-param/<str:first>.<str:last>/",
        # ``<path:...>`` catch-all has non-trivial trailing semantics — slow path.
        "files/<path:file_path>",
        # Leading slash is illegal for Django routes; fast path must refuse.
        "/leading-slash",
    ],
)
def test_normalize_route_django_fast_path_skips_non_eligible_shapes(route):
    from ddtrace.appsec._api_security._normalized_route import _normalize_route_django_fast_path

    assert _normalize_route_django_fast_path(route) is None


@pytest.mark.parametrize(
    "route",
    [
        # ``{n}`` / ``{n,m}`` repetition quantifiers on regex routes have no RFC mapping — the slow path now rejects
        # them outright instead of URL-encoding the braces as static text.
        "^foo{3}$",
        "^a{2,5}b$",
        "^bar{0,}$",
    ],
)
def test_normalize_route_django_rejects_brace_quantifier_on_regex_routes(route):
    assert normalize_route_django(route) is None


def test_normalize_route_django_brace_inside_named_group_body_is_balanced_through():
    # ``{3,4}`` inside ``(?P<x>...)`` is part of the regex body — balancing scans through it and only the outer named
    # group emits an atom.
    assert normalize_route_django(r"^(?P<x>\d{3,4})/$", {"x": "1234"}) == "/{x}/"


def test_normalize_route_django_fast_path_rejects_empty_string():
    from ddtrace.appsec._api_security._normalized_route import _normalize_route_django_fast_path

    # The fast-path regex matches the empty string (every alternation is optional) but yields a meaningless ``"/"``.
    # The helper guards against this so the cache never stores a phantom entry — defense-in-depth alongside the public
    # entry point's ``if not route`` guard.
    assert _normalize_route_django_fast_path("") is None


@pytest.mark.parametrize(
    "path_params",
    [
        # ``str``/``bytes``/``bytearray`` are technically Sequence-compatible but indexing into them yields characters
        # or byte values rather than captured parameters. Treat as "no info" so a defensive caller doesn't accidentally
        # filter atoms on character data.
        "stray-string",
        b"stray-bytes",
        bytearray(b"stray-bytearray"),
    ],
)
def test_normalize_route_django_classify_str_bytes_as_no_info(path_params):
    # With ``_MODE_NO_INFO`` selected, every atom is kept regardless — the result must equal the no-path_params output.
    assert normalize_route_django(r"^a/(\d+)/(\w+)$", path_params) == "/a/{param1}/{param2}"


def test_normalize_route_django_unnamed_unfilterable_against_dict_path_params():
    # Mixed (named + unnamed): Django drops unnamed values from both args and kwargs, leaving us no way to verify
    # presence. Per "omit rather than guess" we emit the placeholder unconditionally.
    assert normalize_route_django(r"^(?P<id>\d+)/(\w+)$", {"id": "42"}) == "/{id}/{param1}"


@pytest.mark.parametrize(
    "route",
    [
        # Top-level non-capturing groups: rejected (alternation has no clean RFC mapping; plain literal is rare).
        "^foo/(?:bar)/$",
        "^foo/(?:bar|baz)/$",
        # Top-level lookarounds and other ``(?...)`` extensions: rejected — no positional capture, no user param.
        "^foo/(?=bar)/$",
        "^foo/(?!bar)/$",
        "^foo/(?<=bar)/$",
        "^foo/(?<!bar)/$",
        # Inline-flag groups, comments, named backreferences, conditionals.
        "^foo/(?i:bar)/$",
        "^foo/(?#comment)bar/$",
        r"^(?P<x>\d+)/(?P=x)/$",
        r"^(?(1)yes|no)/$",
    ],
)
def test_normalize_route_django_non_capturing_groups_rejected(route):
    assert normalize_route_django(route) is None


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # `<paramK>` (via ``path()`` converter) reserves K from the unnamed placeholder pool, same as ``(?P<paramK>``.
        # Reachable via ``include()`` joining a ``path()`` parent with an unnamed ``re_path()`` child.
        (r"<int:param1>/(\w+)", "/{param1}/{param2}"),
        (r"<param1>/(\w+)", "/{param1}/{param2}"),
        # Multiple converter-supplied ``paramK`` reservations.
        (r"<int:param1>/<str:param2>/(\w+)/(\w+)", "/{param1}/{param2}/{param3}/{param4}"),
    ],
)
def test_normalize_route_django_paramN_reserves_path_converter_names(route, expected):
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    ("route", "args_tuple", "expected"),
    [
        # Nested lookahead inside a top-level unnamed capture must NOT bump the args-index counter (``(?=...)``
        # allocates no positional slot in Python ``re``). The trailing ``(\w+)`` lives at ``args[1]``, not ``args[2]``.
        (r"^a/((?=foo)bar)/(\w+)$", ("bar", "abc"), "/a/{param1}/{param2}"),
        # Same for negative lookahead.
        (r"^a/((?!xyz)bar)/(\w+)$", ("bar", "abc"), "/a/{param1}/{param2}"),
        # Lookbehind doesn't allocate a slot either.
        (r"^a/(b(?<=b)ar)/(\w+)$", ("bar", "abc"), "/a/{param1}/{param2}"),
        # Comment ``(?#...)`` doesn't allocate.
        (r"^a/(bar(?#hello))/(\w+)$", ("bar", "abc"), "/a/{param1}/{param2}"),
        # Inline-flag group ``(?i:...)`` doesn't allocate.
        (r"^a/((?i:bar))/(\w+)$", ("bar", "abc"), "/a/{param1}/{param2}"),
    ],
)
def test_normalize_route_django_nested_non_capturing_doesnt_drift_args_index(route, args_tuple, expected):
    assert normalize_route_django(route, args_tuple) == expected
