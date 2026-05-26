"""Unit tests for ``_request_path_params``.

Covers the polymorphic ``kwargs or args or None`` resolution and the no-resolver-match fallback used by the Django
``set_http_meta`` callsites. Tests run against ``SimpleNamespace`` mocks so the helper's logic is exercised without
spinning up a real Django app — the fresh-resolve fallback is integration-tested via the contrib_appsec suite.
"""

from types import SimpleNamespace

import pytest

from ddtrace.contrib.internal.django.utils import _request_path_params


def _request(resolver_match):
    """Build a minimal ``request``-like object with a ``resolver_match`` attribute."""
    return SimpleNamespace(resolver_match=resolver_match)


@pytest.mark.parametrize(
    ("resolver_match", "expected"),
    [
        # No resolver match yet — pre-view dispatches fall here. The fallback resolve only fires when the helper
        # can re-import Django; in this unit-test context the fallback raises and the helper returns None.
        (None, None),
        # Both empty — route with no captures (static-only path or ``^$``).
        (SimpleNamespace(kwargs={}, args=()), None),
        # Named-only — typical ``path()`` and named-group ``re_path()`` routes.
        (SimpleNamespace(kwargs={"id": "42"}, args=()), {"id": "42"}),
        # Unnamed-only — ``re_path()`` with bare ``(...)`` captures.
        (SimpleNamespace(kwargs={}, args=("42", "abc")), ("42", "abc")),
        # Both populated (theoretical — Django's resolver drops args when any named group is present): kwargs wins
        # because the polymorphic shape favors named over positional.
        (SimpleNamespace(kwargs={"id": "42"}, args=("ignored",)), {"id": "42"}),
        # Kwargs explicitly None (some custom resolvers do this) — falls through to args.
        (SimpleNamespace(kwargs=None, args=("only-positional",)), ("only-positional",)),
    ],
)
def test_request_path_params(resolver_match, expected):
    assert _request_path_params(_request(resolver_match)) == expected


def test_request_path_params_request_without_attribute():
    # Not every callsite passes a Django ``HttpRequest`` — defensive for callers building a request-like object that
    # doesn't carry ``resolver_match`` at all.
    bare = SimpleNamespace()
    assert _request_path_params(bare) is None


def test_django_after_request_headers_post_forwards_route():
    """Regression: ``_on_django_after_request_headers_post`` must forward ``route=`` to ``set_http_meta``.

    The sync request path also dispatches ``django.finalize_response.pre`` (which forwards ``route`` explicitly), but
    the async path (``traced_get_response_async``) does NOT — it calls ``_after_request_tags`` directly, so the
    ``django.after_request_headers.post`` hook is the only callsite that fires on both code paths. Without this
    forwarding, Django ASGI deployments would silently miss the AppSec ``_dd.appsec.normalized_route`` tag.
    """
    from unittest.mock import MagicMock
    from unittest.mock import patch

    from ddtrace._trace.trace_handlers import _on_django_after_request_headers_post

    span = MagicMock()
    span.get_tag.return_value = "asm/<int:param_int>/<str:param_str>/"
    request = MagicMock()
    request.method = "GET"
    request.META.get.return_value = ""
    request.GET = {}
    request.COOKIES = {}
    request.resolver_match = SimpleNamespace(kwargs={"param_int": 42, "param_str": "abc"}, args=())

    with patch("ddtrace._trace.trace_handlers.trace_utils.set_http_meta") as mock_set_http_meta:
        _on_django_after_request_headers_post(
            request_headers={},
            response_headers={},
            span=span,
            django_config=MagicMock(),
            request=request,
            url="http://example.com/asm/42/abc/",
            raw_uri="http://example.com/asm/42/abc/",
            status=200,
            response_cookies={},
        )

    mock_set_http_meta.assert_called_once()
    call_kwargs = mock_set_http_meta.call_args.kwargs
    # ``http.route`` was set on the span by ``_set_resolver_tags`` earlier in ``_after_request_tags``; the hook reads
    # it back via ``span.get_tag("http.route")`` and forwards it so the AppSec normalized-route listener can fire.
    assert call_kwargs.get("route") == "asm/<int:param_int>/<str:param_str>/", (
        f"route not forwarded to set_http_meta; got kwargs={sorted(call_kwargs)}"
    )
    span.get_tag.assert_any_call("http.route")
