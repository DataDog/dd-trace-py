from types import CodeType
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import cast

from ddtrace.debugging._function.discovery import FullyNamed
from ddtrace.internal.injection import HookInfoType
from ddtrace.internal.injection import HookType
from ddtrace.internal.injection import eject_hooks
from ddtrace.internal.injection import inject_hooks
from ddtrace.internal.wrapping import WrappedFunction
from ddtrace.internal.wrapping.context import WrappingContext


WrapperType = Callable[[FunctionType, Any, Any, Any], Any]


class FullyNamedWrappedFunction(FullyNamed, WrappedFunction):
    """A fully named wrapper function."""


class FunctionStore(object):
    """Function object store.

    This class provides a storage layer for patching operations, which allows us
    to store the original code object of functions being patched with either
    hook injections or wrapping. This also enforce a single wrapping layer.
    Multiple wrapping is implemented as a list of wrappers handled by the single
    wrapper function.

    If extra attributes are defined during the patching process, they will get
    removed when the functions are restored.
    """

    def __init__(self, extra_attrs: Optional[List[str]] = None) -> None:
        self._code_map: Dict[FunctionType, CodeType] = {}
        self._wrapper_map: Dict[FunctionType, WrappingContext] = {}
        self._extra_attrs = ["__dd_context_wrapped__"]
        if extra_attrs:
            self._extra_attrs.extend(extra_attrs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.restore_all()

    def _store(self, function: FunctionType) -> None:
        if function not in self._code_map:
            self._code_map[function] = function.__code__

    def inject_hooks(self, function: FullyNamedWrappedFunction, hooks: List[HookInfoType]) -> Set[str]:
        """Bulk-inject hooks into a function.

        Returns the set of probe IDs for those probes that failed to inject.
        """
        try:
            return self.inject_hooks(cast(FullyNamedWrappedFunction, function.__dd_wrapped__), hooks)
        except AttributeError:
            f = cast(FunctionType, function)
            self._store(f)
            return {p.probe_id for _, _, p in inject_hooks(f, hooks)}

    def eject_hooks(self, function: FunctionType, hooks: List[HookInfoType]) -> Set[str]:
        """Bulk-eject hooks from a function.

        Returns the set of probe IDs for those probes that failed to eject.
        """
        try:
            wrapped = cast(FullyNamedWrappedFunction, function).__dd_wrapped__
        except AttributeError:
            # Not a wrapped function so we can actually eject from it
            return {p.probe_id for _, _, p in eject_hooks(function, hooks)}
        else:
            # Try on the wrapped function.
            return self.eject_hooks(cast(FunctionType, wrapped), hooks)

    def inject_hook(self, function: FullyNamedWrappedFunction, hook: HookType, line: int, arg: Any) -> bool:
        """Inject a hook into a function."""
        return not not self.inject_hooks(function, [(hook, line, arg)])

    def eject_hook(self, function: FunctionType, hook: HookType, line: int, arg: Any) -> bool:
        """Eject a hook from a function."""
        return not not self.eject_hooks(function, [(hook, line, arg)])

    def wrap(self, function: FunctionType, wrapping_context: WrappingContext) -> None:
        """Wrap a function with a hook."""
        self._store(function)
        self._wrapper_map[function] = wrapping_context
        wrapping_context.wrap()

    def unwrap(self, function: FullyNamedWrappedFunction) -> None:
        """Unwrap a hook around a wrapped function."""
        self._wrapper_map.pop(cast(FunctionType, function)).unwrap()

    def restore_all(self) -> None:
        """Restore all the patched functions to their original form."""
        for function, code in self._code_map.items():
            function.__code__ = code
            for attr in self._extra_attrs:
                try:
                    delattr(function, attr)
                except AttributeError:
                    pass
