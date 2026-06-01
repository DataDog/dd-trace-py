import pytest

from ddtrace.appsec._api_security._normalized_route import normalize_route
from ddtrace.appsec._api_security._normalized_route import normalize_route_django
from ddtrace.appsec._api_security._normalized_route import normalize_route_flask
from ddtrace.appsec._api_security._normalized_route import normalize_route_tornado


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


# ---------------------------------------------------------------------------
# Flask / Werkzeug normalizer  (import is at the top of the file)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Simple single-param with default (string) converter — fast path
        ("/users/<id>", "/users/{id}"),
        # Converter types stripped
        ("/users/<string:id>", "/users/{id}"),
        ("/posts/<int:post_id>", "/posts/{post_id}"),
        ("/price/<float:value>", "/price/{value}"),
        ("/token/<uuid:tok>", "/token/{tok}"),
        # Multi-segment, with and without trailing slash — fast path
        ("/asm/<int:param_int>/<string:param_str>/", "/asm/{param_int}/{param_str}/"),
        ("/asm/<int:param_int>/<string:param_str>", "/asm/{param_int}/{param_str}"),
        # Trailing slash preserved when declared (rule 1)
        ("/api/", "/api/"),
        # Root
        ("/", "/"),
        # Pure static with RFC-safe chars
        ("/static/about", "/static/about"),
        # Static URL-encoding (rule 3)
        ("/path with space", "/path%20with%20space"),
        ("/safe.-~_", "/safe.-~_"),
        ("/é", "/%C3%A9"),
        # Multi-param-in-segment — slow path (rule 5 combines with `+`)
        ("/multi-param/<first>.<last>/", "/multi-param/{first+last}/"),
        ("/users/<first>-<last>", "/users/{first+last}"),
        ("/files/<a>.<b>.<c>", "/files/{a+b+c}"),
        # Static prefix before a single param is dropped (rule 5 single-param)
        ("/api_<version>", "/{version}"),
        # Catch-all `<path:name>` — slow path (rule 5 catch-all exception)
        ("/files/<path:file_path>", "/files/{file_path}"),
        ("/<path:tail>", "/{tail}"),
        # Catch-all with static prefix in same segment — static discarded, name survives (rule 5)
        ("/download/prefix-<path:tail>", "/download/{tail}"),
        # Trailing slash on catch-all
        ("/files/<path:fp>/", "/files/{fp}/"),
        # `any()` converter — stripped to param name uniformly
        ("/section/<any(v1,v2):section>", "/section/{section}"),
        # Converter with `:` in args; backtracking in [^>]+ finds the correct split point.
        ("/x/<custom_conv([a:b]+):slug>", "/x/{slug}"),
        # Param named "path" with no converter: NOT a catch-all (no ``path:`` prefix), fast path.
        ("/files/<path>", "/files/{path}"),
    ],
)
def test_normalize_route_flask_happy_path(route, expected):
    assert normalize_route_flask(route) == expected


@pytest.mark.parametrize(
    "route",
    [
        None,
        "",
        "no-leading-slash",
        "/double//slash",
        "/<path:tail>/and-after",  # non-terminal catch-all: rule 5 violation → omit tag
    ],
)
def test_normalize_route_flask_returns_none_on_invalid(route):
    assert normalize_route_flask(route) is None


def test_normalize_route_flask_path_params_accepted_but_unused():
    # Flask has no optional path elements; path_params is accepted for API parity only.
    assert normalize_route_flask("/users/<int:id>", {"id": 42}) == "/users/{id}"
    assert normalize_route_flask("/users/<int:id>", None) == "/users/{id}"


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Fast path: single simple-converter param per segment
        ("/asm/<int:param_int>/<string:param_str>/", "/asm/{param_int}/{param_str}/"),
        ("/users/<string:name>/", "/users/{name}/"),
        ("/login/", "/login/"),
        ("/login", "/login"),
        ("/", "/"),
        # Param named "path" with no converter: NOT a catch-all → fast path
        ("/files/<path>", "/files/{path}"),
    ],
)
def test_normalize_route_flask_fast_path(route, expected):
    from ddtrace.appsec._api_security._normalized_route import _FLASK_FAST_PATH_REGEX
    from ddtrace.appsec._api_security._normalized_route import _normalize_route_flask_cached

    assert _FLASK_FAST_PATH_REGEX.match(route), f"expected fast path for {route!r}"
    assert _normalize_route_flask_cached(route) == expected
    # Public entry point must produce the same result (validates input-validation + cache wiring).
    assert normalize_route_flask(route) == expected


@pytest.mark.parametrize(
    "route",
    [
        "/multi-param/<first>.<last>/",  # multi-param segment
        "/files/<path:file_path>",  # path catch-all
        "/section/<any(v1,v2):section>",  # any() converter
        "/api_<version>",  # static prefix + param
    ],
)
def test_normalize_route_flask_fast_path_skips_non_eligible_shapes(route):
    from ddtrace.appsec._api_security._normalized_route import _FLASK_FAST_PATH_REGEX

    assert not _FLASK_FAST_PATH_REGEX.match(route), f"expected slow path for {route!r}"


def test_normalize_route_flask_dm_assembly_via_handler():
    """Handler reads flask.resource.full (set by DispatcherMiddleware sub-apps) to assemble the full route."""
    from unittest.mock import MagicMock
    from unittest.mock import patch

    from ddtrace.appsec._constants import API_SECURITY
    from ddtrace.appsec._handlers import _on_set_http_meta_for_normalized_route
    from ddtrace.internal.constants import FLASK_RESOURCE_FULL
    from tests.utils import override_global_config

    span = MagicMock()
    # Simulate sub-app: url_rule.rule = "/<int:id>", script_root = "/asm"
    # => FLASK_RESOURCE_FULL = "GET /asm/<int:id>"
    span.get_tag.side_effect = lambda t: "GET /asm/<int:id>" if t == FLASK_RESOURCE_FULL else None
    span._set_attribute = MagicMock()

    asm_ctx = MagicMock()
    asm_ctx.normalized_route_emitted = False

    with (
        override_global_config(dict(_asm_enabled=True, _api_security_enabled=True)),
        patch("ddtrace.appsec._handlers.get_active_asm_context", return_value=asm_ctx),
        patch("ddtrace.appsec._handlers.core.find_item") as mock_find,
    ):
        integration_config = MagicMock()
        integration_config.integration_name = "flask"
        mock_find.return_value = integration_config

        _on_set_http_meta_for_normalized_route(
            span=span,
            request_ip=None,
            raw_uri=None,
            route="/<int:id>",  # sub-app-local route (no script_root prefix)
            method="GET",
            request_headers=None,
            request_cookies=None,
            parsed_query=None,
            request_path_params=None,
            request_body=None,
            status_code=None,
            response_headers=None,
            response_cookies=None,
        )

    span._set_attribute.assert_called_once_with(API_SECURITY.NORMALIZED_ROUTE, "/asm/{id}")


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


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # ``path("foo/?", view)`` is a valid Django route — ``?`` is escaped to a literal by ``_route_to_regex``, so
        # ``resolver_match.route`` arrives as ``"foo/?"`` and must NOT collapse to ``/foo`` (which would alias it with
        # the distinct ``path("foo", ...)`` declaration). Only ``re_path``-shaped routes treat ``/?`` as optional.
        ("foo/?", "/foo/%3F"),
        # re_path optional-slash still works as before.
        ("^foo/?$", "/foo"),
    ],
)
def test_normalize_route_django_literal_question_mark_in_path_route(route, expected):
    assert normalize_route_django(route) == expected


@pytest.mark.parametrize(
    ("route", "args_tuple", "expected"),
    [
        # Python ``re`` treats a leading ``]`` inside ``[...]`` as a literal class member, not the closer. The body
        # ``[](a)]+`` is one char class (containing ``]``, ``a``, ``)``) with ``+`` quantifier — the ``(a)`` inside
        # the class is NOT a capture. The outer ``(...)`` is the only group in the body, so the next sibling lands
        # at ``args_index == 1``, not 2.
        (r"^([](a)]+)/(\d+)$", (")))", "42"), "/{param1}/{param2}"),
        # Same rule with ``[^...]`` negated class: the ``]`` after ``^`` is a literal class member.
        (r"^([^]a)]+)/(\d+)$", ("x", "42"), "/{param1}/{param2}"),
    ],
)
def test_normalize_route_django_char_class_leading_bracket_literal(route, args_tuple, expected):
    assert normalize_route_django(route, args_tuple) == expected


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


# ---------------------------------------------------------------------------
# Tornado normalizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "path_params", "expected"),
    [
        # Root route (no groups).
        ("/", {}, "/"),
        # Static route with optional trailing slash: ``/?`` is not "declared with trailing slash".
        ("/asm/?", {}, "/asm"),
        # Static route with explicit trailing slash: kept.
        ("/static/", {}, "/static/"),
        # Named groups (dict path_params).
        ("/asm/%s/%s/?", {"param_int": "137", "param_str": "abc"}, "/asm/{param_int}/{param_str}"),
        ("/new_service/%s/?", {"service_name": "svc"}, "/new_service/{service_name}"),
        # Named groups, no trailing slash in route: output also has no trailing slash.
        ("/users/%s", {"id": "42"}, "/users/{id}"),
        # Explicit trailing slash preserved when route has one (no ``?``).
        ("/api/%s/", {"v": "1"}, "/api/{v}/"),
        # Multi-param segment: two ``%s`` in one URL segment combined with ``+`` (rule 5).
        ("/multi-param/%s.%s/?", {"first": "john", "last": "doe"}, "/multi-param/{first+last}"),
        # Three params in one segment.
        ("/x/%s.%s.%s", {"a": "1", "b": "2", "c": "3"}, "/x/{a+b+c}"),
        # Wildcard regex (.*) collapses to a single ``%s``; normalizer emits one atomic element.
        ("/files/%s", {"file_path": "some/deep/path"}, "/files/{file_path}"),
        # Positional groups (list path_params): auto-generate param1, param2, …
        ("/asm/%s/%s/?", ["137", "abc"], "/asm/{param1}/{param2}"),
        ("/redirect/%s/%s/?", ["route", "8080"], "/redirect/{param1}/{param2}"),
        # Positional empty list → same as no path_params (zero placeholders).
        ("/asm/?", [], "/asm"),
        # No path_params at all (None): auto-generate names.
        ("/asm/%s/%s/?", None, "/asm/{param1}/{param2}"),
        # Static-constant URL-encoding (rule 3): unsafe chars encoded.
        ("/path with space", {}, "/path%20with%20space"),
        ("/safe.-~_", {}, "/safe.-~_"),
        # RFC-1103 example: any-framework fallback ``*`` → ``%s`` after _regex_to_route.
        ("/%s", None, "/{param1}"),
        # ``/?`` route (just optional slash): strips to empty body → returns ``"/"``.
        ("/?", {}, "/"),
    ],
)
def test_normalize_route_tornado_happy_path(route, path_params, expected):
    assert normalize_route_tornado(route, path_params) == expected


@pytest.mark.parametrize(
    "route",
    [
        None,
        "",
        "no-leading-slash",
        "/double//slash",
    ],
)
def test_normalize_route_tornado_returns_none_on_invalid(route):
    assert normalize_route_tornado(route) is None


def test_normalize_route_tornado_mismatch_dict_too_few_keys():
    # Dict has fewer keys than ``%s`` in route → cannot map params → return None.
    assert normalize_route_tornado("/asm/%s/%s/?", {"only_one": "x"}) is None


def test_normalize_route_tornado_mismatch_dict_too_many_keys():
    # Dict has more keys than ``%s`` in route → same ``!=`` check → return None.
    assert normalize_route_tornado("/x/%s", {"a": "1", "b": "2"}) is None


def test_normalize_route_tornado_mismatch_list_wrong_length():
    # List length doesn't match placeholder count → return None.
    assert normalize_route_tornado("/asm/%s/%s/?", ["just_one"]) is None


def test_normalize_route_tornado_empty_dict_no_placeholders():
    # Empty dict path_params with no ``%s`` in route is the normal static-route case.
    assert normalize_route_tornado("/asm/?", {}) == "/asm"
    assert normalize_route_tornado("/", {}) == "/"


def test_normalize_route_tornado_param_name_reserved_char_encoded():
    # RFC rule 4: reserved chars in parameter names must be URL-encoded.
    assert normalize_route_tornado("/x/%s", {"foo+bar": "v"}) == "/x/{foo%2Bbar}"
    assert normalize_route_tornado("/x/%s", {"a/b": "v"}) == "/x/{a%2Fb}"


def test_normalize_route_tornado_caching_by_route_and_names():
    # Same route + same param-name tuple → same cached object (lru_cache hit).
    r1 = normalize_route_tornado("/asm/%s/%s/?", {"param_int": "1", "param_str": "a"})
    r2 = normalize_route_tornado("/asm/%s/%s/?", {"param_int": "2", "param_str": "b"})
    assert r1 is r2 == "/asm/{param_int}/{param_str}"


# --- Fix 2: optional groups (RFC-1103 rule 6 per-request filtering) ---


@pytest.mark.parametrize(
    ("route", "path_params", "expected"),
    [
        # Named optional group that did not match → segment dropped.
        ("/opt/%s?", {"id": None}, "/opt"),
        ("/opt/%s?", {"id": ""}, "/opt"),
        # Named optional group that matched → segment kept.
        ("/opt/%s?", {"id": "42"}, "/opt/{id}"),
        # Positional optional group that did not match → segment dropped.
        ("/opt/%s?", [None], "/opt"),
        ("/opt/%s?", [""], "/opt"),
        # Positional optional group that matched → segment kept.
        ("/opt/%s?", ["42"], "/opt/{param1}"),
        # Two params in one segment, first absent → only second emitted (no ``+``).
        ("/x/%s.%s", {"a": None, "b": "y"}, "/x/{b}"),
        # Both absent in a segment → segment dropped entirely.
        ("/x/%s.%s/suffix", {"a": None, "b": None}, "/x/suffix"),
        # Multi-segment route, middle optional absent.
        ("/a/%s?/b", {"id": None}, "/a/b"),
        # Tornado returns bytes from url_unescape(encoding=None); b"" is also absent.
        ("/opt/%s?", {"id": b""}, "/opt"),
        ("/opt/%s?", [b""], "/opt"),
        # Non-empty bytes → present (not absent).
        ("/opt/%s?", {"id": b"42"}, "/opt/{id}"),
    ],
)
def test_normalize_route_tornado_optional_group_filtering(route, path_params, expected):
    assert normalize_route_tornado(route, path_params) == expected


# --- Regex syntax in static segments ---


@pytest.mark.parametrize(
    "route",
    [
        # Alternation inside a non-capturing group → structural ambiguity → None.
        "/api/(?:v1|v2)/%s",
        "/complex/(?:new|existing)/",
        # Bare | alternation at segment level → None.
        "/foo|bar/items",
        # Lookahead group → None.
        "/items/(?=\\d+)/detail",
        # Nested alternation — ``|`` inside an inner group is also detected → None.
        "/x/(?:a(?:b|c))/detail",
    ],
)
def test_normalize_route_tornado_ambiguous_static_segment_returns_none(route):
    assert normalize_route_tornado(route, {"id": "1"} if "%s" in route else {}) is None


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Character class → anonymous dynamic parameter.
        ("/api/[a-z]+/%s", "/api/{param1}/{id}"),
        # Bare wildcard patterns → anonymous dynamic parameter.
        ("/assets/.+", "/assets/{param1}"),
        ("/items/.*", "/items/{param1}"),
        # Regex shorthand → anonymous dynamic parameter.
        (r"/items/\d+/", "/items/{param1}/"),
        (r"/prefix/\w+/suffix", "/prefix/{param1}/suffix"),
        # Character class containing ``/`` — smarter split keeps [^/]+ intact.
        ("/items/[^/]+/detail", "/items/{param1}/detail"),
        # Non-capturing group without alternation, class containing ``/`` — also intact.
        ("/items/(?:[^/]+)/detail", "/items/{param1}/detail"),
        # Brace quantifier ``{n,m}`` → anonymous dynamic parameter.
        ("/x/a{3}", "/x/{param1}"),
        ("/x/\\d{2,4}", "/x/{param1}"),
        # Multiple bare dynamic segments → param1, param2, …
        ("/a/.+/b/.+", "/a/{param1}/b/{param2}"),
        # Mixed: named %s param + bare dynamic → named first, then paramN continues.
        ("/users/%s/posts/\\d+", "/users/{id}/posts/{param1}"),
    ],
)
def test_normalize_route_tornado_anonymous_dynamic_segment(route, expected):
    # path_params: named %s routes get a dict, bare-only routes get {}.
    pp = {"id": "42"} if "%s" in route else {}
    assert normalize_route_tornado(route, pp) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        # Escaped paren ``\(`` means a literal ``(`` in the URL path — NOT regex syntax.
        # The backslash is stripped and ``(`` is URL-encoded as ``%28``.
        (r"/items/\(v1\)/item", "/items/%28v1%29/item"),
        # Escaped dot ``\.`` → literal ``.`` → already in STATIC_SAFE, no encoding.
        (r"/v1\.0/items", "/v1.0/items"),
    ],
)
def test_normalize_route_tornado_escaped_chars_in_static_segment(route, expected):
    assert normalize_route_tornado(route, {}) == expected
