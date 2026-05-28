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
