from collections import deque
from dis import findlinestarts
from functools import lru_cache
from functools import partial
from functools import singledispatch
from pathlib import Path
from types import CodeType
from types import FunctionType
from types import ModuleType
from typing import Iterator
from typing import Optional
from typing import cast
import weakref

from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.wrapping import _code_to_fn as _CODE_TO_ORIGINAL_FUNCTION_MAPPING
from ddtrace.internal.wrapping import is_wrapped as _dd_is_wrapped


@singledispatch
def linenos(_) -> set[int]:
    raise NotImplementedError()


@linenos.register
def _(code: CodeType) -> set[int]:
    """Get the line numbers of a function."""
    return {ln for _, ln in findlinestarts(code) if ln is not None} - {code.co_firstlineno}


@linenos.register
def _(f: FunctionType) -> set[int]:
    return linenos(f.__code__)


@cached(maxsize=4 << 10)
def _filename_to_resolved_path(filename: str) -> Path:
    return Path(filename).resolve()


def resolved_code_origin(code: CodeType) -> Path:
    return _filename_to_resolved_path(code.co_filename)


def undecorated(f: FunctionType, name: str, path: Path) -> FunctionType:
    # Find the original function object from a decorated function. We use the
    # expected function name to guide the search and pick the correct function.
    # The recursion is needed in case of multiple decorators. We make it BFS
    # to find the function as soon as possible.

    def match(g):
        return g.__code__.co_name == name and resolved_code_origin(g.__code__) == path

    seen_functions = {f}
    q = deque([f])  # FIFO: use popleft and append

    while q:
        g = q.popleft()

        # Look for a wrapped function. These attributes are generally used by
        # the decorators provided by the standard library (e.g. partial)
        for attr in ("__wrapped__", "func"):
            try:
                wrapped = object.__getattribute__(g, attr)
                if _isinstance(wrapped, FunctionType) and wrapped not in seen_functions:
                    if match(wrapped):
                        return wrapped
                    q.append(wrapped)
                    seen_functions.add(wrapped)
            except AttributeError:
                pass

        # A partial object is a common decorator. The function can either be the
        # curried function, or it can appear as one of the arguments (e.g. the
        # implementation of the wraps decorator).
        if _isinstance(g, partial):
            p = cast(partial, g)
            if match(p.func):
                return cast(FunctionType, p.func)
            for arg in p.args:
                if _isinstance(arg, FunctionType) and arg not in seen_functions:
                    if match(arg):
                        return arg
                    q.append(arg)
                    seen_functions.add(arg)
            for arg in p.keywords.values():
                if _isinstance(arg, FunctionType) and arg not in seen_functions:
                    if match(arg):
                        return arg
                    q.append(arg)
                    seen_functions.add(arg)

        # Look for a closure (function decoration)
        if _isinstance(g, FunctionType):
            for c in (_.cell_contents for _ in (g.__closure__ or []) if _isinstance(_.cell_contents, FunctionType)):
                if c not in seen_functions:
                    if match(c):
                        return c
                    q.append(c)
                    seen_functions.add(c)

        # If the function has bytecode wrapping we return the function itself.
        # We don't want to descend into the temporary inner copy.
        if _dd_is_wrapped(g):
            return g

        # Look for a function attribute (method decoration)
        # DEV: We don't recurse over arbitrary objects. We stop at the first
        # depth level.
        try:
            for v in object.__getattribute__(g, "__dict__").values():
                if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                    return v
        except AttributeError:
            # Maybe we have slots
            try:
                for v in (object.__getattribute__(g, _) for _ in object.__getattribute__(g, "__slots__")):
                    if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                        return v
            except AttributeError:
                pass

        # Last resort
        try:
            for v in (object.__getattribute__(g, a) for a in object.__dir__(g)):
                if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                    return v
        except AttributeError:
            pass

    return f


def collect_code_objects(code: CodeType) -> Iterator[CodeType]:
    q = deque([code])
    while q:
        c = q.popleft()
        for new_code in (_ for _ in c.co_consts if isinstance(_, CodeType)):
            yield new_code
            q.append(new_code)


@lru_cache(maxsize=(1 << 14))  # 16k entries
def _functions_for_code_gc(code: CodeType) -> list[FunctionType]:
    import gc

    return [_ for _ in gc.get_referrers(code) if isinstance(_, FunctionType) and _.__code__ is code]


def functions_for_code(code: CodeType) -> list[FunctionType]:
    try:
        # Try to get the function from the original code-to-function mapping
        return [_CODE_TO_ORIGINAL_FUNCTION_MAPPING[code]]
    except KeyError:
        # If the code is not in the mapping, we fall back to the garbage
        # collector
        return _functions_for_code_gc(code)


def clear():
    """Clear the inspection state.

    This should be called when modules are reloaded to ensure that the mappings
    stay relevant.
    """
    _functions_for_code_gc.cache_clear()
    _CODE_TO_ORIGINAL_FUNCTION_MAPPING.clear()


class ModuleCodeCollector(BaseModuleWatchdog):
    """Collect the nested code objects of every module compiled after install.

    Some products need the full set of code objects a module was compiled with,
    including ones that become unreachable from the module's namespace after
    decoration. This watchdog collects them at compile time, before any
    decorator runs, so that a product can still recover them regardless of what
    decorators did to the module's namespace.

    Products subscribe with register unconditionally at their own
    product-module import time (i.e. regardless of whether the product itself
    is enabled), so that the data is already available if the product is
    enabled later on. A module's entry is kept until every subscriber that was
    registered when the module was compiled has called release for it.
    """

    _subscribers: set[str] = set()

    def __init__(self) -> None:
        super().__init__()
        self._code: weakref.WeakKeyDictionary[ModuleType, tuple[list[CodeType], set[str]]] = weakref.WeakKeyDictionary()

    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        self._code[module] = (list(collect_code_objects(code)), set(self._subscribers))
        return code

    def after_import(self, module: ModuleType) -> None:
        pass

    @classmethod
    def register(cls, subscriber: str) -> None:
        """Declare interest in the collected code objects.

        This must be called unconditionally at product-module import time, not
        gated behind the product's own enablement check, otherwise modules
        compiled before the product enables would be missing from its data.
        """
        cls._subscribers.add(subscriber)
        if not cls.is_installed():
            cls.install()

    @classmethod
    def get_code_objects(cls, module: ModuleType) -> Optional[list[CodeType]]:
        """Get the code objects collected for a module, if any."""
        if not cls.is_installed():
            return None
        entry = cast("ModuleCodeCollector", cls._instance)._code.get(module)
        return entry[0] if entry is not None else None

    @classmethod
    def release(cls, module: ModuleType, subscriber: str) -> None:
        """Release a subscriber's interest in a module's collected code objects.

        Once every subscriber that was registered when the module was compiled
        has released it, the entry is discarded and the memory reclaimed by the
        garbage collector.
        """
        if not cls.is_installed():
            return
        instance = cast("ModuleCodeCollector", cls._instance)
        entry = instance._code.get(module)
        if entry is None:
            return
        _, pending = entry
        pending.discard(subscriber)
        if not pending:
            del instance._code[module]
