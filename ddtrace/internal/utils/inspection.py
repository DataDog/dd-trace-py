from collections import deque
from collections.abc import Iterator
from dis import findlinestarts
from functools import partial
from functools import singledispatch
from pathlib import Path
from types import CodeType
from types import FunctionType
from types import ModuleType
from typing import Optional
from typing import cast
import weakref

from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import IdentityWeakKeyDictionary
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.cache import miss
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

        # PERF: g itself is the answer when it already matches, none of the explicit wrapper
        # relationships above led elsewhere, and the queue holds no other candidate that the
        # BFS would have reached first. Both conditions are load-bearing:
        #   - checking here rather than before the probes preserves their precedence, so a
        #     wrapper sharing the target's name and file still resolves to the original it
        #     closes over;
        #   - requiring an empty queue keeps the BFS honest when an outer wrapper matches but
        #     a queued intermediate leads to the real original (see
        #     test_undecorated_same_name_outer_wrapper_defers_to_queued_candidates).
        # For a plain function neither applies and the expensive __dir__() scan below is
        # skipped, which is the case the pytest plugin hits once per test.
        if not q and _isinstance(g, FunctionType) and match(g):
            return g

        # Last resort.
        # NOTE: the try wraps the whole loop, so the first name in object.__dir__(g) that is
        # not gettable via object.__getattribute__ ends the scan early. Bound methods hit
        # this: object.__dir__ merges in the underlying function's attributes, so a wrapper
        # decorated with functools.wraps surfaces __wrapped__, which a method object does not
        # forward, and the scan stops before reaching __func__. That is why a bound method
        # can come back unresolved, and why the shortcut above is restricted to functions.
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


# CodeType.__eq__ treats structurally-identical code objects (e.g. the code
# objects produced by reloading a module whose source hasn't changed) as
# equal, which a plain lru_cache or weakref.WeakKeyDictionary would conflate,
# returning a stale functions list computed for the old, possibly dead, code
# object. IdentityWeakKeyDictionary keys on id(code) instead. The cached
# value additionally holds the functions only weakly: a function keeps its
# own __code__ alive, so a cache that held them strongly would keep the old
# code object (and thus its own entry) reachable forever, defeating the
# point of keying on the code's liveness.
_functions_for_code_gc_cache: "IdentityWeakKeyDictionary[CodeType, list[weakref.ref[FunctionType]]]" = (
    IdentityWeakKeyDictionary()
)


def _functions_for_code_gc(code: CodeType) -> list[FunctionType]:
    import gc

    cached_refs = _functions_for_code_gc_cache.get(code, miss)
    if cached_refs is not miss:
        return [f for f in (ref() for ref in cached_refs) if f is not None]

    functions = [_ for _ in gc.get_referrers(code) if isinstance(_, FunctionType) and _.__code__ is code]
    _functions_for_code_gc_cache[code] = [weakref.ref(f) for f in functions]

    return functions


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

    Both caches this clears are already self-cleaning on garbage collection, so
    this is not required for correctness on module reload. It remains as an
    explicit, immediate reset for tests and other callers that don't want to
    wait on GC.
    """
    global _functions_for_code_gc_cache

    _functions_for_code_gc_cache = IdentityWeakKeyDictionary()
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
        # Tracks every module we have ever compiled, so we can tell a fresh
        # compile apart from a recompile (e.g. importlib.reload()) even after
        # self._code's entry for the module has been released by every
        # subscriber. Unlike self._code, entries here are never removed early.
        self._seen: weakref.WeakSet[ModuleType] = weakref.WeakSet()

    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        if module in self._seen:
            # The module is being recompiled (e.g. importlib.reload()). Code
            # objects compare and hash by value, not identity, so an unmodified
            # module recompiles into code objects that are indistinguishable
            # from the old ones to identity-agnostic caches: functions_for_code's
            # lru_cache and _CODE_TO_ORIGINAL_FUNCTION_MAPPING would otherwise
            # keep returning the pre-reload function objects forever.
            clear()
        else:
            self._seen.add(module)
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
