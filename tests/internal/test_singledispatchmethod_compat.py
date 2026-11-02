"""Regression for the Python <3.9.8 singledispatchmethod.register patch.

``hasattr(classmethod, "__func__")`` is True because the type exposes a
member_descriptor; the old compat patch then raised
``AttributeError: 'member_descriptor' object has no attribute '__annotations__'``
when Symbol Database did ``@_get_from.register(classmethod)``.
"""

from __future__ import annotations

import types
from typing import Any
from typing import Callable

from ddtrace.internal.compat import _singledispatchmethod_register
from ddtrace.internal.compat import singledispatchmethod


def _install_compat_register(generic_method: Any) -> None:
    """Bind the <3.9.8 compat register onto the descriptor (not a __get__ wrapper)."""
    generic_method.register = types.MethodType(_singledispatchmethod_register, generic_method)


def test_register_classmethod_type_under_compat_patch() -> None:
    """``@register(classmethod)`` must succeed under the <3.9.8 compat patch."""
    # The type itself has __func__, which is what made hasattr the wrong check.
    assert hasattr(classmethod, "__func__")

    class Host:
        @singledispatchmethod
        def get(self, value: object) -> str:
            return "default"

    descriptor: Any = Host.__dict__["get"]
    _install_compat_register(descriptor)

    @descriptor.register(classmethod)
    def _(self: Host, value: classmethod[Any, Any, Any]) -> str:
        return "classmethod"

    marker: classmethod[Any, Any, Any] = classmethod(lambda cls: None)
    result: str = Host().get(marker)
    assert result == "classmethod"


def test_register_staticmethod_type_under_compat_patch() -> None:
    """``@register(staticmethod)`` must succeed under the <3.9.8 compat patch."""
    assert hasattr(staticmethod, "__func__")

    class Host:
        @singledispatchmethod
        def get(self, value: object) -> str:
            return "default"

    descriptor: Any = Host.__dict__["get"]
    _install_compat_register(descriptor)

    @descriptor.register(staticmethod)
    def _(self: Host, value: staticmethod[Any, Any]) -> str:
        return "staticmethod"

    marker: staticmethod[Any, Any] = staticmethod(lambda: None)
    result: str = Host().get(marker)
    assert result == "staticmethod"


def test_compat_register_classmethod_type_matches_cpython_guard() -> None:
    """Directly exercise the patched register with the classmethod *type*."""
    registered: dict[type, Callable[..., str]] = {}

    class FakeDispatcher:
        def register(self, cls: type, func: Callable[..., str] | None = None) -> Callable[..., str] | None:
            if func is not None:
                registered[cls] = func
            return func

    class FakeGeneric:
        dispatcher: FakeDispatcher = FakeDispatcher()

    def impl(self: object, value: object) -> str:
        return "ok"

    # Must not raise AttributeError on member_descriptor.__annotations__.
    _singledispatchmethod_register(FakeGeneric(), classmethod, impl)
    assert registered[classmethod] is impl
