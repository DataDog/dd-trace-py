"""@tracer.wrap() composed with @classmethod / @staticmethod (decorator ordering).

``tracer.wrap()`` is dd-trace's public decorator; users stack it with
``@classmethod`` / ``@staticmethod`` in either order. These tests pin which
orderings work. They are mechanism-specific -- only tracer_wrap has a
decorator-stacking surface (internal_wrap / wrapt / wrapping_context are applied
by name or in place, where the descriptor is never at risk) -- so they opt out of
the all-mechanisms ``mech`` guardrail.

Finding: ``@tracer.wrap()`` applied *over* ``@classmethod`` / ``@staticmethod``
receives the descriptor object, wraps it as a plain callable, and loses the
binding -- broken on every supported version (AttributeError at decoration on 3.9,
TypeError at call on 3.11+). Applying ``tracer.wrap()`` *innermost* (below the
descriptor) works. The broken orderings are codified with a strict xfail: if
tracer.wrap() ever learns to preserve the descriptor, the XPASS fails CI and these
markers come out.
"""

import pytest

from ddtrace.trace import tracer


pytestmark = pytest.mark.mechanism_specific


def test_classmethod_innermost_works():
    class C:
        @classmethod
        @tracer.wrap()
        def m(cls, x):
            return (cls.__name__, x)

    assert C.m(5) == ("C", 5)
    assert C().m(5) == ("C", 5)
    assert type(C.__dict__["m"]) is classmethod


@pytest.mark.xfail(
    strict=True,
    reason="@tracer.wrap() over @classmethod wraps the classmethod descriptor as a plain callable and "
    "loses the binding; apply tracer.wrap() innermost (@classmethod @tracer.wrap()) instead",
)
def test_classmethod_outermost_broken():
    class C:
        @tracer.wrap()
        @classmethod
        def m(cls, x):
            return (cls.__name__, x)

    assert C.m(5) == ("C", 5)
    assert C().m(5) == ("C", 5)


def test_staticmethod_innermost_works():
    class C:
        @staticmethod
        @tracer.wrap()
        def m(x):
            return ("static", x)

    assert C.m(5) == ("static", 5)
    assert C().m(5) == ("static", 5)
    assert type(C.__dict__["m"]) is staticmethod


@pytest.mark.xfail(
    strict=True,
    reason="@tracer.wrap() over @staticmethod loses the staticmethod binding, so instance access "
    "passes self as a positional arg; apply tracer.wrap() innermost (@staticmethod @tracer.wrap())",
)
def test_staticmethod_outermost_broken():
    class C:
        @tracer.wrap()
        @staticmethod
        def m(x):
            return ("static", x)

    assert C.m(5) == ("static", 5)
    assert C().m(5) == ("static", 5)
