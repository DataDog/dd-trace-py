import pytest

from ddtrace.appsec._api_security._normalized_route import normalize_route


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
        # Catch-all with in-segment static prefix: entire tail is one atomic element
        ("/files/file-{tail:path}", "/files/{tail}"),
        # Trailing slash preserved when declared (rule 1)
        ("/api/", "/api/"),
        ("/api/{v}/", "/api/{v}/"),
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
    # Starlette/FastAPI have no optional path elements. The signature accepts
    # `path_params` for parity with frameworks that do; passing it must not change
    # the result.
    assert normalize_route("/users/{id}", {"id": 42}) == "/users/{id}"
    assert normalize_route("/users/{id}", None) == "/users/{id}"


def test_normalize_route_param_name_with_reserved_char_is_url_encoded():
    # Rule 4: framework-supplied param names containing /?#+{} must be URL-encoded.
    # Starlette's PARAM_REGEX prevents this from arising naturally — exercise the
    # internal encoder directly to keep coverage.
    from ddtrace.appsec._api_security._normalized_route import _encode_param_name

    assert _encode_param_name("foo+bar") == "foo%2Bbar"
    assert _encode_param_name("a/b") == "a%2Fb"
    assert _encode_param_name("clean_name") == "clean_name"
