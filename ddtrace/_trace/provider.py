import abc
import contextvars
import sys
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import DefaultContextProvider as _NativeDefaultContextProvider


log = get_logger(__name__)


ActiveTrace = Union[Span, Context]
_DD_CONTEXTVAR: contextvars.ContextVar[Optional[ActiveTrace]] = contextvars.ContextVar(
    "datadog_contextvar", default=None
)


# PyContextVar_Set is not atomic before CPython 3.12: an allocation during
# the HAMT rebuild can trigger a cyclic GC pass that frees a node still in use,
# crashing with SEGV_MAPERR. On affected versions we route the set through a
# native helper that secures the context's storage during the call.
# CPython 3.12+ is unaffected, so use the plain (and faster) set there.
if sys.version_info < (3, 12):
    from ddtrace.internal.native._native import safe_contextvar_set

    def _activate_contextvar(ctx: Optional[ActiveTrace]) -> None:
        safe_contextvar_set(_DD_CONTEXTVAR, ctx)

else:
    _activate_contextvar = _DD_CONTEXTVAR.set  # type: ignore[assignment]


class BaseContextProvider(metaclass=abc.ABCMeta):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
    * the ``active`` method, that returns the current active ``Context``
    * the ``activate`` method, that sets the current active ``Context``
    """

    @abc.abstractmethod
    def _has_active_context(self) -> bool:
        pass

    @abc.abstractmethod
    def activate(self, ctx: Optional[ActiveTrace]) -> None:
        core.dispatch("ddtrace.context_provider.activate", (ctx,))

    @abc.abstractmethod
    def active(self) -> Optional[ActiveTrace]:
        pass

    def __call__(self, *args: Any, **kwargs: Any) -> Optional[ActiveTrace]:
        """Method available for backward-compatibility. It proxies the call to
        ``self.active()`` and must not do anything more.
        """
        return self.active()


class DefaultContextProvider(_NativeDefaultContextProvider):
    """Context provider that retrieves contexts from a context variable.

    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.

    PERF: the entire provider hot path — ``active()``/``_has_active_context()``/
    ``activate()``/``_update_active()``/``__call__`` — is implemented natively (see
    ``src/native/context_provider.rs``), so per-span activation/read carries no Python
    frame. This Python subclass only wires the shared contextvar + Span type into the
    native base and keeps ``DefaultContextProvider`` a registered ``BaseContextProvider``.
    """

    def __init__(self) -> None:
        # The native base constructs via __new__ (no args here); hand it the shared
        # contextvar + the Span type it needs for its exact-type check. Mirrors the
        # previous pure-Python behaviour exactly.
        self._configure(_DD_CONTEXTVAR, Span)


# DefaultContextProvider now derives from the native base rather than BaseContextProvider
# (avoids a metaclass conflict between PyO3's type and ABCMeta). Register it as a virtual
# subclass so `isinstance(provider, BaseContextProvider)` / `issubclass` still hold.
BaseContextProvider.register(DefaultContextProvider)
