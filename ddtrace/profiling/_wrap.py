# -*- encoding: utf-8 -*-
"""Identity-preserving function-wrap helper for the profiling internals.

``_wrap(owner, name, wrapper)`` mutates ``owner.name``'s ``__code__`` in
place via ``CodeType.replace()`` to redirect every call through
``wrapper(original, args, kwargs)``. Function identity is preserved, so
references captured before the wrap was installed (e.g. ``from X import
Y`` performed at another module's import time — see uvloop) still go
through the wrap. This matches the contract of
``ddtrace.internal.wrapping.wrap`` (which uses the ``bytecode`` lib)
without taking on that dependency.

Currently used only by ``ddtrace.profiling._asyncio``; other profiling
modules can import from here if they need the same behaviour.
"""

from __future__ import annotations

from functools import wraps
import inspect
import sys
import types
import typing


_WrapperFn = typing.Callable[
    [types.FunctionType, tuple[typing.Any, ...], dict[str, typing.Any]],
    typing.Any,
]

# Per wrap site: id(grafted code) -> (user wrapper, copy of original).
# Each wrap site has a unique cloned code object so the id is stable.
_ddtrace_wrap_registry: dict[int, tuple[_WrapperFn, types.FunctionType]] = {}


# Template trampolines. We clone ``__code__`` per wrap site via
# ``CodeType.replace()`` and graft it onto the original; each clone has a
# unique id which the trampoline reads via ``sys._getframe(0).f_code``.
def _ddtrace_trampoline_sync(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
    wrapper, original_copy = _ddtrace_wrap_registry[id(sys._getframe(0).f_code)]
    return wrapper(original_copy, args, kwargs)


async def _ddtrace_trampoline_async(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
    wrapper, original_copy = _ddtrace_wrap_registry[id(sys._getframe(0).f_code)]
    return await wrapper(original_copy, args, kwargs)


def wrap(
    owner: typing.Any,
    name: str,
    wrapper: _WrapperFn,
    aliases: typing.Sequence[tuple[typing.Any, str]] = (),
) -> typing.Callable[..., typing.Any]:
    """Wrap ``owner.name`` so calls go through ``wrapper(original, args, kwargs)``.

    Pure-Python no-closure functions: mutate ``__code__`` in place — preserves
    identity, so ``from X import Y`` references captured before profiler start
    still see the wrap (the uvloop scenario). Same trick ``bytecode.wrap`` did.

    Everything else (Cython, C builtins, closures-via-``super()``): falls back
    to ``setattr`` and mirrors onto ``aliases``.
    """
    original: typing.Any = getattr(owner, name)

    if isinstance(original, types.FunctionType) and not original.__closure__:
        original_copy: types.FunctionType = types.FunctionType(
            original.__code__,
            original.__globals__,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        original_copy.__kwdefaults__ = original.__kwdefaults__

        is_async: bool = inspect.iscoroutinefunction(original)
        template = _ddtrace_trampoline_async if is_async else _ddtrace_trampoline_sync

        # Clone the template's bytecode, stamp original's metadata (each
        # ``replace()`` returns a fresh code object → unique id per wrap site).
        new_code: types.CodeType = template.__code__.replace(
            co_filename=original.__code__.co_filename,
            co_firstlineno=original.__code__.co_firstlineno,
            co_name=original.__code__.co_name,
        )
        _ddtrace_wrap_registry[id(new_code)] = (wrapper, original_copy)

        # Trampoline does LOAD_GLOBAL on these names, resolved against the
        # original's module globals at call time. Idempotent per module.
        original.__globals__.setdefault("_ddtrace_wrap_registry", _ddtrace_wrap_registry)
        original.__globals__.setdefault("sys", sys)

        original.__code__ = new_code
        # Make ``inspect.signature(original)`` follow through to the real
        # signature instead of the trampoline's ``(*args, **kwargs)``.
        original.__wrapped__ = original_copy  # type: ignore[attr-defined]
        return original

    # Fallback path — identity NOT preserved. ``aliases`` mirrors the wrap onto
    # re-exported bindings (e.g. asyncio.X aliased to asyncio.tasks.X).
    @wraps(original)
    def wrapped(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        return wrapper(original, args, kwargs)

    setattr(owner, name, wrapped)
    for alias_owner, alias_name in aliases:
        setattr(alias_owner, alias_name, wrapped)
    return wrapped
