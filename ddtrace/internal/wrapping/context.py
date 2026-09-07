from abc import ABC
from contextvars import ContextVar
from functools import lru_cache
from inspect import CO_COROUTINE
from inspect import CO_GENERATOR
import sys
from types import CodeType
from types import FrameType
from types import FunctionType
from types import TracebackType
import typing as t
from typing import Protocol
import weakref

import bytecode
from bytecode import Bytecode

from ddtrace.internal.assembly import Assembly
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock
from ddtrace.internal.threads import RLock
from ddtrace.internal.utils.obfuscation import ObfuscatedCodeError
from ddtrace.internal.utils.obfuscation import is_obfuscated_code
from ddtrace.internal.wrapping import WrappedFunction
from ddtrace.internal.wrapping import Wrapper
from ddtrace.internal.wrapping import get_function_code
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import link_function_to_code
from ddtrace.internal.wrapping import set_function_code
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


class _ContextRecord:
    """Holds all wrapping-context metadata for a single function.

    Stores only weak references to context objects so that the WeakKeyDictionary
    key (the function) is not kept alive by the registry value chain:
      _registry -> _ContextRecord -> context -> context.__wrapped__ -> function
    Breaking this path with weak references lets ephemeral functions be garbage
    collected as soon as all external strong references drop.
    """

    __slots__ = ("_uwc_ref", "lazy_contexts")

    def __init__(self) -> None:
        self._uwc_ref: t.Optional[weakref.ref["_UniversalWrappingContext"]] = None
        # WeakSet so that LazyWrappingContext instances (which also hold
        # __wrapped__ = f) do not prevent the function from being collected.
        self.lazy_contexts: weakref.WeakSet["LazyWrappingContext"] = weakref.WeakSet()

    @property
    def uwc(self) -> t.Optional["_UniversalWrappingContext"]:
        ref: t.Optional[weakref.ref["_UniversalWrappingContext"]] = self._uwc_ref
        return ref() if ref is not None else None

    @uwc.setter
    def uwc(self, value: t.Optional["_UniversalWrappingContext"]) -> None:
        self._uwc_ref = weakref.ref(value) if value is not None else None

    @classmethod
    def get_or_create(cls, f: FunctionType) -> "_ContextRecord":
        record: t.Optional["_ContextRecord"] = _registry.get(f)
        if record is None:
            with _registry_lock:
                record = _registry.get(f)
                if record is None:
                    record = cls()
                    _registry[f] = record
        return record


# Per-function registry for wrapping-context machinery. WeakKeyDictionary so
# functions are not kept alive by the registry alone. Storing data here instead
# of as function attributes keeps __dict__ clean, preventing frameworks that
# copy function __dict__ (e.g. functools.wraps,
# self.__dict__.update(f.__dict__)) from capturing non-picklable objects.
_registry: weakref.WeakKeyDictionary[FunctionType, _ContextRecord] = weakref.WeakKeyDictionary()
_registry_lock = RLock()


log = get_logger(__name__)


# Closures (nested functions with free variables) created by repeated calls to
# the same factory function all share a single underlying code object. Each
# time such a closure is wrapped we would otherwise decompile, instrument and
# recompile that identical code object from scratch. Instead, the first time a
# given code object is instrumented we cache the resulting "template" code
# object, which has placeholder consts in place of the instance-specific
# enter/return/exit callables. Subsequent wraps of a function sharing the same
# original code object reuse the template and only need a cheap CodeType.replace
# to swap in the real callables, skipping the decompile/instrument/recompile
# steps entirely.
#
# The cache is bounded (simple LRU eviction, via lru_cache) so that
# pathological cases, e.g. generating a large number of distinct dynamic code
# objects, cannot grow it without bound.
_TEMPLATE_CACHE_MAX_SIZE = 512


# Sentinels marking the position of instance-specific consts in a cached
# template, in place of the instance-specific context_enter/context_return/
# context_exit bound methods (Python >= 3.11), or the context object itself
# (Python < 3.11), when building a cacheable template out of a function's
# bytecode. Only identity matters, so plain objects suffice.
_ENTER_PLACEHOLDER = object()
_RETURN_PLACEHOLDER = object()
_EXIT_PLACEHOLDER = object()
_CONTEXT_PLACEHOLDER = object()


T = t.TypeVar("T")

# Hoisted out of the hot enter/exit/return paths: subscripting a generic alias
# like dict[str, t.Any] allocates a new types.GenericAlias on every evaluation,
# so re-typing it inline on each call is not free.
WrappingContextStorage = dict[str, t.Any]
StorageVar = ContextVar[t.Optional[dict[str, t.Any]]]

_STORAGE_PREV = "__dd_wrapping_context_prev__"
_STORAGE_OWNER = "__dd_wrapping_context_owner__"
# Set in per-call storage when a raise originates from context machinery itself
# (__return__, or on 3.15+ on_py_start) rather than from the wrapped function
# body, so the resulting exception does not also trigger __exit__. Consumed by
# _UniversalWrappingContext._exit (bytecode path, >=3.11) and on_py_unwind
# (sys.monitoring path, >=3.15).
_SKIP_EXIT_KEY = "__dd_wrapping_context_skip_exit__"

# Free lists of storage context variables, keyed by variable name.
#
# Once a ContextVar has been set, the running Context holds a strong reference
# to it for the lifetime of the thread, and there is no way to drop that entry
# again (ContextVar.reset is not usable here, see the note on _pop_storage).
# Since wrapping contexts are created per function object, code that decorates
# ephemeral functions on every call would otherwise pin one variable per
# invocation. Recycling the variables of collected wrapping contexts caps the
# number of live ones at the peak number of concurrently live wrapping
# contexts. The pool is deliberately never trimmed: dropping a variable from it
# would not release the Context entries it already holds, and would only force
# the allocation of a new variable, adding entries instead of reusing them. That
# peak is therefore retained for the lifetime of the process, but it no longer
# grows with the number of functions that get wrapped.
#
# A recycled variable may still be set in some Context when it is handed out: a
# context that is collected without exiting leaves its storage behind, and the
# finalizer cannot reset it because it runs in an unrelated Context. Storage
# dicts are tagged with an owner token so that the new owner can tell a leftover
# value apart from one of its own; see __enter__.
_storage_var_pools: dict[str, list[StorageVar]] = {}

# Reentrant because the release happens from a finalizer, which can run at
# any point, including in the middle of an acquisition on the same thread.
_storage_var_pools_lock = RLock()


def _acquire_storage_var(name: str) -> StorageVar:
    with _storage_var_pools_lock:
        pool = _storage_var_pools.get(name)
        if pool:
            var = pool.pop()
            if not pool:
                # Drop exhausted pools so that the names of wrapping contexts
                # that are no longer in use don't accumulate either.
                del _storage_var_pools[name]
            return var

    return ContextVar(name, default=None)


def _release_storage_var(name: str, var: StorageVar) -> None:
    with _storage_var_pools_lock:
        _storage_var_pools.setdefault(name, []).append(var)


# This module implements utilities for wrapping a function with a context
# manager. The rough idea is to re-write the function's bytecode to look like
# this:
#
#   def foo():
#       with wrapping_context:
#           # Original function code
#
# Because we also want to capture the return value, our context manager extends
# the Python one by implementing a __return__ method that will be called with
# the return value of the function. Contrary to ordinary context managers,
# though, the __exit__ method is only called if the function raises an
# exception.
#
# Because CPython 3.11 introduced zero-cost exceptions, we cannot nest try
# blocks in the function's bytecode. In this case, we call the context manager
# methods directly at the right places, and set up the appropriate exception
# handling code. For older versions of Python we rely on the with statement to
# perform entry and exit operations. Calls to __return__ are explicit in all
# cases.
#
# Some advantages of wrapping a function this way are:
# - Access to the local variables on entry and on return/exit via the frame
#   object.
# - No intermediate function calls that pollute the call stack.
# - No need to call the wrapped function manually.
#
# The actual bytecode wrapping is performed once on a target function via a
# universal wrapping context. Multiple context wrapping of a function is allowed
# and it is virtually implemented on top of the concrete universal wrapping
# context. This makes multiple wrapping/unwrapping easy, as it translates to a
# single bytecode wrapping/unwrapping operation.
#
# Context wrappers should be implemented as subclasses of the WrappingContext
# class. The __priority__ attribute can be used to control the order in which
# multiple context wrappers are entered and exited. The __enter__ and __exit__
# methods should be implemented to perform the necessary operations. The
# __exit__ method is called if the wrapped function raises an exception. The
# frame of the wrapped function can be accessed via the __frame__ property. The
# __return__ method can be implemented to capture the return value of the
# wrapped function. If implemented, its return value will be used as the wrapped
# function return value. The wrapped function can be accessed via the
# __wrapped__ attribute. Context-specific values can be stored and retrieved
# with the set and get methods.

CONTEXT_HEAD = Assembly()
CONTEXT_RETURN = Assembly()
CONTEXT_FOOT = Assembly()

if sys.version_info >= (3, 16):
    raise NotImplementedError("This version of Python is not supported yet")
elif sys.version_info >= (3, 15):
    # We rely on sys.monitoring for wrapping, so no bytecode manipulation is
    # needed.
    pass
elif sys.version_info >= (3, 13):
    CONTEXT_HEAD.parse(
        r"""
            load_const                  {context_enter}
            push_null
            call                        0
            pop_top
        """
    )
    CONTEXT_RETURN.parse(
        r"""
            push_null
            load_const                  {context_return}
            swap                        3
            call                        1
        """
    )

    CONTEXT_RETURN_CONST = Assembly()
    CONTEXT_RETURN_CONST.parse(
        r"""
            load_const                  {context_return}
            push_null
            load_const                  {value}
            call                        1
        """
    )

    CONTEXT_FOOT.parse(
        r"""
        try                             @_except lasti
            push_exc_info
            load_const                  {context_exit}
            push_null
            call                        0
            pop_top
            reraise                     2
        tried

        _except:
            copy                        3
            pop_except
            reraise                     1
        """
    )

elif sys.version_info >= (3, 12):
    CONTEXT_HEAD.parse(
        r"""
            push_null
            load_const                  {context_enter}
            call                        0
            pop_top
        """
    )

    CONTEXT_RETURN.parse(
        r"""
            load_const                  {context_return}
            push_null
            swap                        3
            call                        1
        """
    )

    CONTEXT_RETURN_CONST = Assembly()
    CONTEXT_RETURN_CONST.parse(
        r"""
            push_null
            load_const                  {context_return}
            load_const                  {value}
            call                        1
        """
    )

    CONTEXT_FOOT.parse(
        r"""
        try                             @_except lasti
            push_exc_info
            push_null
            load_const                  {context_exit}
            call                        0
            pop_top
            reraise                     2
        tried

        _except:
            copy                        3
            pop_except
            reraise                     1
        """
    )


elif sys.version_info >= (3, 11):
    CONTEXT_HEAD.parse(
        r"""
            push_null
            load_const                  {context_enter}
            precall                     0
            call                        0
            pop_top
        """
    )

    CONTEXT_RETURN.parse(
        r"""
            load_const                  {context_return}
            push_null
            swap                        3
            precall                     1
            call                        1
        """
    )

    CONTEXT_EXC_HEAD = Assembly()
    CONTEXT_EXC_HEAD.parse(
        r"""
            push_null
            load_const                  {context_exit}
            precall                     0
            call                        0
            pop_top
        """
    )

    CONTEXT_FOOT.parse(
        r"""
        try                             @_except lasti
            push_exc_info
            push_null
            load_const                  {context_exit}
            precall                     0
            call                        0
            pop_top
            reraise                     2
        tried

        _except:
            copy                        3
            pop_except
            reraise                     1
        """
    )

elif sys.version_info >= (3, 10):
    CONTEXT_HEAD.parse(
        r"""
            load_const                  {context}
            setup_with                  @_except
            pop_top
        _except:
        """
    )

    CONTEXT_RETURN.parse(
        r"""
            pop_block
            load_const                  {context}
            load_method                 $__return__
            rot_three
            rot_three
            call_method                 1
            rot_two
            pop_top
        """
    )

    CONTEXT_FOOT.parse(
        r"""
            with_except_start
            pop_top
            reraise                     1
        """
    )

elif sys.version_info >= (3, 9):
    CONTEXT_HEAD.parse(
        r"""
            load_const                  {context}
            setup_with                  @_except
            pop_top
        _except:
        """
    )

    CONTEXT_RETURN.parse(
        r"""
            pop_block
            load_const                  {context}
            load_method                 $__return__
            rot_three
            rot_three
            call_method                 1
            rot_two
            pop_top
        """
    )

    CONTEXT_FOOT.parse(
        r"""
            with_except_start
            pop_top
            reraise
        """
    )


# On the bytecode path __enter__ is invoked directly from inside the wrapped
# function, so the monitored frame is one level up. On the monitoring path
# (3.15+) the stack is:
#   monitored function → monitoring._on_py_start → uwc.on_py_start → __enter__
# so the monitored frame is three levels up.
_ENTER_FRAME_DEPTH = 3 if sys.version_info >= (3, 15) else 1

if sys.version_info >= (3, 15):
    from ddtrace.internal import monitoring as _monitoring

    # Keyed by code object: drives sys.monitoring dispatch and is_wrapped/extract lookup.
    # CodeType overrides __eq__/__hash__ (equal for structurally-identical code), and
    # monitor_code is produced by original_code.replace(), which compares equal to
    # original_code. A plain weakref.WeakKeyDictionary would therefore conflate the
    # clone with the original -- and, via that, conflate closures that share one
    # original code object -- so this uses the identity-keyed mapping instead.
    _ctx_registry: "_monitoring._IdentityWeakKeyDictionary" = _monitoring._IdentityWeakKeyDictionary()
    # Keyed by function instance: distinguishes functions that share a code object
    # (e.g. closures re-created in a loop) from one another. Kept off the function's
    # __dict__ (unlike a plain attribute) so functools.wraps does not propagate
    # wrapping metadata and deepcopy of decorated functions does not traverse a
    # context holding unpicklable state (locks, ContextVars). See issue #16443.
    _fn_registry: "weakref.WeakKeyDictionary[FunctionType, _UniversalWrappingContext]" = weakref.WeakKeyDictionary()
    _ctx_registry_lock = Lock()


# This is abstract and should not be used directly
class BaseWrappingContext(ABC):
    __priority__: int = 0

    def __init__(self, f: FunctionType):
        # Store a weak reference so that context objects do not keep the wrapped
        # function alive. CodeType is not GC-tracked in CPython, so the cycle
        #   f → code.co_consts → bound_methods(uwc) → uwc.__wrapped__ → f
        # cannot be broken by the cyclic GC. A weak ref here allows f's
        # reference count to reach zero (and be freed) as soon as all external
        # strong refs drop, without relying on the cyclic GC at all.
        self._wrapped_ref: weakref.ref[FunctionType] = weakref.ref(f)

        # Identifies the storage dicts written by this context. A dedicated token
        # is used rather than self so that the storage dict cannot keep the
        # context, and therefore the wrapped function, alive.
        self._storage_owner = object()

        # Qualified so that same-named context types (e.g. the two
        # LazyWrappingContext classes in this package) do not share a pool.
        name = f"{type(self).__module__}.{type(self).__qualname__}__storage"
        self._storage: StorageVar = _acquire_storage_var(name)
        weakref.finalize(self, _release_storage_var, name, self._storage).atexit = False

    @property
    def __wrapped__(self) -> FunctionType:
        f = self._wrapped_ref()
        if f is None:
            raise RuntimeError(f"{type(self).__name__}.__wrapped__: the wrapped function has been garbage collected")
        return f

    @__wrapped__.setter
    def __wrapped__(self, f: FunctionType) -> None:
        self._wrapped_ref = weakref.ref(f)

    def __enter__(self) -> "BaseWrappingContext":
        prev = self._storage.get()
        if prev is not None and prev.get(_STORAGE_OWNER) is not self._storage_owner:
            # Storage left behind by a previous owner of this recycled variable.
            # Chaining it into our own prev would restore it on every exit from
            # now on, pinning it (and the frame it holds, for a universal
            # wrapping context) for the lifetime of the thread. Dropping it here
            # instead frees it as soon as we overwrite the variable below.
            prev = None
        self._storage.set({_STORAGE_PREV: prev, _STORAGE_OWNER: self._storage_owner})

        return self

    def _pop_storage(self) -> dict[str, t.Any]:
        storage = t.cast(t.Optional[WrappingContextStorage], self._storage.get())
        if storage is None:
            return {}
        self._storage.set(storage.pop(_STORAGE_PREV))
        del storage[_STORAGE_OWNER]
        return storage

    def __return__(self, value: T) -> T:
        self._pop_storage()
        return value

    def __exit__(
        self,
        exc_type: t.Optional[type[BaseException]],
        exc_val: t.Optional[BaseException],
        exc_tb: t.Optional[TracebackType],
    ) -> None:
        self._pop_storage()

    def get(self, key: str) -> t.Any:
        return t.cast(WrappingContextStorage, self._storage.get())[key]

    def set(self, key: str, value: T) -> T:
        t.cast(WrappingContextStorage, self._storage.get())[key] = value
        return value

    @classmethod
    def wrapped(cls, f: FunctionType) -> "BaseWrappingContext":
        try:
            context = cls.extract(f)
            assert isinstance(context, cls)  # nosec
        except ValueError:
            context = cls(f)
            context.wrap()
        return context

    @classmethod
    def is_wrapped(cls, _f: FunctionType) -> bool:
        raise NotImplementedError

    @classmethod
    def extract(cls, _f: FunctionType) -> "BaseWrappingContext":
        raise NotImplementedError

    def wrap(self) -> None:
        raise NotImplementedError

    def unwrap(self) -> None:
        raise NotImplementedError


# This is the public interface exported by this module
class WrappingContext(BaseWrappingContext):
    @property
    def __frame__(self) -> FrameType:
        try:
            return t.cast(
                FrameType,
                _UniversalWrappingContext.extract(t.cast(FunctionType, self.__wrapped__)).get("__frame__"),
            )
        except ValueError:
            raise AttributeError("Wrapping context not entered")

    def get_local(self, name: str) -> t.Any:
        return self.__frame__.f_locals[name]

    @classmethod
    def is_wrapped(cls, f: FunctionType) -> bool:
        try:
            return bool(cls.extract(f))
        except ValueError:
            return False

    @classmethod
    def extract(cls, f: FunctionType) -> "WrappingContext":
        try:
            return _UniversalWrappingContext.extract(f).registered(cls)
        except (ValueError, KeyError):
            msg = f"Function is not wrapped with {cls}"
            raise ValueError(msg)

    def wrap(self) -> None:
        f = t.cast(FunctionType, self.__wrapped__)
        context = t.cast(_UniversalWrappingContext, _UniversalWrappingContext.wrapped(f))
        context.register(self)

    def unwrap(self) -> None:
        f = t.cast(FunctionType, self.__wrapped__)

        try:
            _UniversalWrappingContext.extract(f).unregister(self)
        except ValueError:
            pass


if sys.version_info >= (3, 15):
    # Monitoring-based instrumentation has negligible per-function overhead, so
    # there is no benefit to deferring wrapping until first call. On Python 3.15+
    # this is a transparent alias for WrappingContext kept only for API compatibility.
    LazyWrappingContext = WrappingContext

else:

    class LazyWrappingContext(WrappingContext):
        def __init__(self, f: FunctionType):
            super().__init__(f)

            self._trampoline: t.Optional[Wrapper] = None
            self._trampoline_lock = Lock()

        @classmethod
        def is_wrapped(cls, f: FunctionType) -> bool:
            with _registry_lock:
                record: t.Optional[_ContextRecord] = _registry.get(f)
                if record is None:
                    return False
                return any(isinstance(c, cls) for c in record.lazy_contexts)

        def wrap(self) -> None:
            """Perform the bytecode wrapping on first invocation."""
            with (tl := self._trampoline_lock):
                if self._trampoline is not None:
                    return

                # If the function is already universally wrapped it's less expensive
                # to do the normal wrapping.
                if _UniversalWrappingContext.is_wrapped(t.cast(FunctionType, self.__wrapped__)):
                    super().wrap()
                    return

                def trampoline(_: t.Any, args: tuple[t.Any, ...], kwargs: dict[str, t.Any]) -> t.Any:
                    with tl:
                        f = t.cast(WrappedFunction, self.__wrapped__)
                        if is_wrapped_with(t.cast(FunctionType, self.__wrapped__), trampoline):
                            f = t.cast(WrappedFunction, unwrap(f, trampoline))

                            self._trampoline = None

                            inconsistent: bool = False
                            with _registry_lock:
                                record: t.Optional[_ContextRecord] = _registry.get(t.cast(FunctionType, f))
                                if record is not None:
                                    inconsistent = self not in record.lazy_contexts
                                    record.lazy_contexts.discard(self)
                                    if not record.lazy_contexts and record.uwc is None:
                                        _registry.pop(t.cast(FunctionType, f), None)
                            if inconsistent:
                                log.warning("Inconsistent lazy wrapping context state")

                            super(LazyWrappingContext, self).wrap()
                    return f(*args, **kwargs)

                wrap(t.cast(FunctionType, self.__wrapped__), trampoline)

                self._trampoline = trampoline

                _ContextRecord.get_or_create(t.cast(FunctionType, self.__wrapped__)).lazy_contexts.add(self)

        def unwrap(self) -> None:
            with self._trampoline_lock:
                if _UniversalWrappingContext.is_wrapped(t.cast(FunctionType, self.__wrapped__)):
                    assert self._trampoline is None  # nosec
                    super().unwrap()
                elif self._trampoline is not None:
                    with _registry_lock:
                        record: t.Optional[_ContextRecord] = _registry.get(t.cast(FunctionType, self.__wrapped__))
                        if record is not None:
                            record.lazy_contexts.discard(self)
                            if not record.lazy_contexts and record.uwc is None:
                                _registry.pop(t.cast(FunctionType, self.__wrapped__), None)

                    unwrap(t.cast(WrappedFunction, self.__wrapped__), self._trampoline)
                    self._trampoline = None


class ContextWrappedFunction(Protocol):
    """A function that is (or can be) wrapped with a WrappingContext.

    Used purely as a structural type marker for call sites that operate on
    wrapped functions. Per-function wrapping state is tracked off the function
    object (see _fn_registry on 3.15+ / the registry on older versions), so this
    protocol intentionally carries no wrapping-metadata attribute.
    """

    def __call__(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        pass


# On 3.15+ _UniversalWrappingContext also implements MonitoringEventHandler so
# it can be registered directly with the multiplexer via register(code, self).
if sys.version_info >= (3, 15):
    from ddtrace.internal.monitoring import MonitoringEventHandler as _MonitoringEventHandler

    _UWC_BASES: tuple[type, ...] = (BaseWrappingContext, _MonitoringEventHandler)
else:
    _UWC_BASES = (BaseWrappingContext,)


# This class provides an interface between single bytecode wrapping and multiple
# logical context wrapping
class _UniversalWrappingContext(*_UWC_BASES):  # type: ignore[misc]
    def __init__(self, f: FunctionType) -> None:
        super().__init__(f)

        self._contexts: list[WrappingContext] = []

    def register(self, context: WrappingContext) -> None:
        _type = type(context)
        if any(isinstance(c, _type) for c in self._contexts):
            raise ValueError("Context already registered")

        self._contexts.append(context)
        self._contexts.sort(key=lambda c: c.__priority__)

    def unregister(self, context: WrappingContext) -> None:
        try:
            self._contexts.remove(context)
        except ValueError:
            raise ValueError("Context not registered")

        if not self._contexts:
            self.unwrap()

    def is_registered(self, context: WrappingContext) -> bool:
        return any(isinstance(c, type(context)) for c in self._contexts)

    def registered(self, context_type: type[WrappingContext]) -> WrappingContext:
        for context in self._contexts:
            if isinstance(context, context_type):
                return context
        raise KeyError(f"Context {context_type} not registered")

    def __enter__(self) -> "_UniversalWrappingContext":
        super().__enter__()

        storage = t.cast(WrappingContextStorage, self._storage.get())

        # Make the frame object available to the contexts
        storage["__frame__"] = sys._getframe(_ENTER_FRAME_DEPTH)

        # Freeze the list of contexts so that we know exactly which ones to
        # exit, in case new contexts are registered during the execution of
        # the wrapped function. Contexts are appended only once entered, so
        # that a failure partway through does not leave contexts that never
        # entered in the snapshot.
        entered: list[WrappingContext] = []
        storage["__contexts__"] = entered
        for context in self._contexts:
            try:
                context.__enter__()
            except Exception:
                log.debug("Failed to enter wrapping context %r", context, exc_info=True)
                continue
            entered.append(context)

        return self

    def _exit(self) -> None:
        # Only reached on the bytecode path for Python >= 3.11, where the
        # injected __return__ call sits inside the same try/except as the rest
        # of the wrapped function body (see CONTEXT_FOOT). Skip the exit here
        # too, so a failing __return__ behaves the same on every version.
        storage = self._storage.get()
        if storage is not None and storage.pop(_SKIP_EXIT_KEY, False):
            return
        self.__exit__(*sys.exc_info())

    def __exit__(
        self,
        exc_type: t.Optional[type[BaseException]],
        exc_value: t.Optional[BaseException],
        traceback: t.Optional[TracebackType],
    ) -> None:
        if exc_value is None:
            return

        try:
            contexts = t.cast(WrappingContextStorage, self._storage.get())["__contexts__"]
        except (TypeError, KeyError):
            log.debug("Universal wrapping context exited without entering")
            return

        for context in contexts[::-1]:
            context.__exit__(exc_type, exc_value, traceback)

        super().__exit__(exc_type, exc_value, traceback)

    def __return__(self, value: T) -> T:
        storage = t.cast(WrappingContextStorage, self._storage.get())
        try:
            contexts = storage["__contexts__"]
        except (TypeError, KeyError):
            log.debug("Universal wrapping context returned without entering")
            return t.cast(T, super().__return__(value))

        try:
            for context in contexts[::-1]:
                context.__return__(value)
        except BaseException:
            # A failing __return__ must not be treated as an exception from the
            # wrapped function body -- it never gets to run, so __exit__ must
            # not run for it either. See _exit and on_py_unwind, which consume
            # this flag on the bytecode and sys.monitoring paths respectively.
            storage[_SKIP_EXIT_KEY] = True
            raise

        return t.cast(T, super().__return__(value))

    if sys.version_info >= (3, 15):
        # Exceptions here are deliberately left uncaught (see the propagation
        # warning on MonitoringEventHandler), which matches bytecode-path
        # with-statement semantics -- safe because this is the only handler
        # ddtrace registers for these events on a given code object.
        #
        # CPython also fires a synthetic PY_UNWIND after a failing PY_START/
        # PY_RETURN; _SKIP_EXIT_KEY suppresses the resulting __exit__ call so
        # it only fires for a real exception from the wrapped function body.
        # It lives in per-call storage (a ContextVar), not a plain attribute,
        # because this same instance is shared across concurrent calls.

        def on_py_start(self, code: t.Any, instruction_offset: int) -> None:
            try:
                self.__enter__()
            except BaseException:
                storage = self._storage.get()
                if storage is not None:
                    storage[_SKIP_EXIT_KEY] = True
                raise

        def on_py_return(self, code: t.Any, instruction_offset: int, retval: t.Any) -> None:
            self.__return__(retval)

        def on_py_unwind(self, code: t.Any, instruction_offset: int, exception: BaseException) -> None:
            storage = self._storage.get()
            if storage is not None and storage.pop(_SKIP_EXIT_KEY, False):
                return
            self.__exit__(type(exception), exception, exception.__traceback__)

        @classmethod
        def is_wrapped(cls, f: FunctionType) -> bool:
            try:
                code: CodeType = get_function_code(f)
                if code not in _ctx_registry:
                    return False
                # Also verify that THIS function instance is wrapped, not just some
                # other function that shares the same code object (e.g. closures
                # re-created in a loop).
                return _fn_registry.get(f) is _ctx_registry[code]
            except Exception:
                return False

        @classmethod
        def extract(cls, f: FunctionType) -> "_UniversalWrappingContext":
            ctx: t.Optional["_UniversalWrappingContext"] = _ctx_registry.get(get_function_code(f))
            if ctx is None:
                raise ValueError("Function is not wrapped")
            # Monitoring dispatches per code object, so a fresh function instance
            # that merely shares a code object (e.g. a closure re-created in a
            # loop) maps to the same registry entry without being wrapped itself.
            # Mirror is_wrapped()'s per-instance check so callers such as wrapped()
            # replace the stale registration via wrap() instead of double-
            # registering on a context that belongs to another (often dead) function.
            if _fn_registry.get(f) is not ctx:
                raise ValueError("Function is not wrapped")
            return ctx

        def wrap(self) -> None:
            f: FunctionType = self.__wrapped__
            original_code: CodeType = get_function_code(f)
            with _ctx_registry_lock:
                if original_code in _ctx_registry:
                    existing: "_UniversalWrappingContext" = _ctx_registry[original_code]
                    if _fn_registry.get(f) is existing:
                        raise ValueError("Function already wrapped")
                    # Only replace a registry entry when the prior wrapped function
                    # has been collected. A live function sharing this code object is
                    # still actively wrapped and must not be unregistered.
                    try:
                        existing.__wrapped__
                    except RuntimeError:
                        _ctx_registry.pop(original_code)
                        _monitoring.unregister(original_code, existing)
                    else:
                        raise ValueError("Function already wrapped")

                # sys.monitoring dispatches per code object. Clone the code so
                # unwrapped siblings that share the same CodeType are not affected.
                from ddtrace.internal.bytecode_injection import migrate_line_hooks

                link_function_to_code(original_code, f)
                monitor_code: CodeType = original_code.replace()
                migrate_line_hooks(original_code, monitor_code)

                _ctx_registry[monitor_code] = self
                _fn_registry[f] = self
                self._finalize = weakref.finalize(
                    f,
                    _finalize_monitoring_wrap,
                    weakref.ref(self),
                    monitor_code,
                )
                self._finalize.atexit = False
                # Register monitoring before swapping __code__ so no thread can
                # observe monitor_code without an active handler.
                _monitoring.register(monitor_code, self)
                set_function_code(f, monitor_code)
                self._original_code = original_code

        def unwrap(self) -> None:
            f: FunctionType = self.__wrapped__
            finalize: t.Optional[weakref.finalize] = getattr(self, "_finalize", None)
            if finalize is not None:
                finalize.detach()
                del self._finalize
            code: CodeType = get_function_code(f)
            with _ctx_registry_lock:
                if code not in _ctx_registry:
                    return
                del _ctx_registry[code]
                _fn_registry.pop(f, None)
            _monitoring.unregister(code, self)
            original_code: t.Optional[CodeType] = getattr(self, "_original_code", None)
            if original_code is not None:
                from ddtrace.internal.bytecode_injection import migrate_line_hooks

                migrate_line_hooks(code, original_code)
                set_function_code(f, original_code)
                del self._original_code

    else:

        @classmethod
        def is_wrapped(cls, f: FunctionType) -> bool:
            try:
                with _registry_lock:
                    record: t.Optional[_ContextRecord] = _registry.get(f)
                    if record is None or record.uwc is None:
                        return False
                    # Verify the registry entry matches actual bytecode wrapping.
                    if sys.version_info >= (3, 11):
                        return record.uwc.__enter__ in get_function_code(f).co_consts
                    else:
                        return record.uwc in get_function_code(f).co_consts
            except AttributeError:
                return False

        @classmethod
        def extract(cls, f: FunctionType) -> "_UniversalWrappingContext":
            with _registry_lock:
                if not cls.is_wrapped(f):
                    raise ValueError("Function is not wrapped")
                return t.cast(_UniversalWrappingContext, _registry[f].uwc)

        def wrap(self) -> None:
            f = t.cast(FunctionType, self.__wrapped__)

            with _registry_lock:
                if self.is_wrapped(f):
                    raise ValueError("Function already wrapped")

                code = get_function_code(f)
                if is_obfuscated_code(code):
                    raise ObfuscatedCodeError(
                        f"Cannot wrap {code.co_name!r}: code object appears to be obfuscated (e.g. by PyArmor)"
                    )

                # Closures created from repeated calls to the same factory share
                # the same code object: _build_template is memoized so the
                # expensive decompile/instrument/recompile step is reused across
                # them.
                template = self._build_template(code)

                # Register the wrapping context and link the function to the new
                # code object.
                _ContextRecord.get_or_create(f).uwc = self
                link_function_to_code(code, f)

                # Substitute the template's placeholder consts with the real,
                # instance-specific values.
                replacements = self._template_replacements()
                set_function_code(
                    f, template.replace(co_consts=tuple(replacements.get(c, c) for c in template.co_consts))
                )

        if sys.version_info >= (3, 11):

            @staticmethod
            @lru_cache(maxsize=_TEMPLATE_CACHE_MAX_SIZE)
            def _build_template(code: "CodeType") -> "CodeType":
                """Build a cacheable, instance-agnostic instrumented copy of *code*.

                The instance-specific context_enter/context_return/context_exit
                bound methods are replaced with placeholders so that the result
                can be shared across multiple closures backed by the same code
                object; see wrap(). Memoized via lru_cache, keyed on the code
                object, so repeated wraps of closures sharing the same underlying
                code skip the decompile/instrument/recompile.
                """
                bc = Bytecode.from_code(code)

                # Prefix every return
                i = 0
                while i < len(bc):
                    instr = bc[i]
                    try:
                        if instr.name == "RETURN_VALUE":
                            return_code = CONTEXT_RETURN.bind(
                                {"context_return": _RETURN_PLACEHOLDER}, lineno=instr.lineno
                            )
                        elif sys.version_info >= (3, 12) and instr.name == "RETURN_CONST":  # Python 3.12+
                            return_code = CONTEXT_RETURN_CONST.bind(
                                {"context_return": _RETURN_PLACEHOLDER, "value": instr.arg}, lineno=instr.lineno
                            )
                        else:
                            return_code = []

                        bc[i:i] = return_code
                        i += len(return_code)
                    except AttributeError:
                        # Not an instruction
                        pass
                    i += 1

                # Search for the RESUME instruction
                for i, instr in enumerate(bc, 1):
                    try:
                        if instr.name == "RESUME":
                            break
                    except AttributeError:
                        # Not an instruction
                        pass
                else:
                    i = 0

                bc[i:i] = CONTEXT_HEAD.bind({"context_enter": _ENTER_PLACEHOLDER}, lineno=code.co_firstlineno)

                # Wrap every line outside a try block
                except_label = bytecode.Label()
                first_try_begin = last_try_begin = bytecode.TryBegin(except_label, push_lasti=True)

                i = 0
                while i < len(bc):
                    instr = bc[i]
                    if isinstance(instr, bytecode.TryBegin) and last_try_begin is not None:
                        bc.insert(i, bytecode.TryEnd(last_try_begin))
                        last_try_begin = None
                        i += 1
                    elif isinstance(instr, bytecode.TryEnd):
                        j = i + 1
                        while j < len(bc) and not isinstance(bc[j], bytecode.TryBegin):
                            if isinstance(bc[j], bytecode.Instr):
                                last_try_begin = bytecode.TryBegin(except_label, push_lasti=True)
                                bc.insert(i + 1, last_try_begin)
                                break
                            j += 1
                        i += 1
                    i += 1

                bc.insert(0, first_try_begin)

                bc.append(bytecode.TryEnd(last_try_begin))
                bc.append(except_label)
                bc.extend(CONTEXT_FOOT.bind({"context_exit": _EXIT_PLACEHOLDER}, lineno=code.co_firstlineno))

                return bc.to_code()

            def _template_replacements(self) -> dict[object, object]:
                return {
                    _ENTER_PLACEHOLDER: self.__enter__,
                    _RETURN_PLACEHOLDER: self.__return__,
                    _EXIT_PLACEHOLDER: self._exit,
                }

            def unwrap(self) -> None:
                f = self.__wrapped__

                with _registry_lock:
                    if not self.is_wrapped(f):
                        return

                    wc = _registry[f].uwc

                    bc = Bytecode.from_code(get_function_code(f))

                    # Remove the exception handling code
                    bc[-len(CONTEXT_FOOT) :] = []
                    bc.pop()
                    bc.pop()

                    except_label = bc.pop(0).target

                    # Remove the try blocks
                    i = 0
                    while i < len(bc):
                        instr = bc[i]
                        if isinstance(instr, bytecode.TryBegin) and instr.target is except_label:
                            bc.pop(i)
                        elif isinstance(instr, bytecode.TryEnd) and instr.entry.target is except_label:
                            bc.pop(i)
                        else:
                            i += 1

                    # Remove the head of the try block
                    for i, instr in enumerate(bc):
                        if isinstance(instr, bytecode.Instr) and instr.name == "LOAD_CONST" and instr.arg is wc:
                            break

                    # Search for the RESUME instruction
                    for i, instr in enumerate(bc, 1):
                        try:
                            if instr.name == "RESUME":
                                break
                        except AttributeError:
                            # Not an instruction
                            pass
                    else:
                        i = 0

                    bc[i : i + len(CONTEXT_HEAD)] = []

                    # Un-prefix every return
                    i = 0
                    while i < len(bc):
                        instr = bc[i]
                        try:
                            if instr.name == "RETURN_VALUE":
                                return_code = CONTEXT_RETURN
                            elif sys.version_info >= (3, 12) and instr.name == "RETURN_CONST":  # Python 3.12+
                                return_code = CONTEXT_RETURN_CONST
                            else:
                                return_code = None

                            if return_code is not None:
                                bc[i - len(return_code) : i] = []
                                i -= len(return_code)
                        except AttributeError:
                            # Not an instruction
                            pass
                        i += 1

                    # Recreate the code object
                    set_function_code(f, bc.to_code())

                    # Clear the UWC from the registry; remove the record if fully empty.
                    record: t.Optional[_ContextRecord] = _registry.get(f)
                    if record is not None:
                        record.uwc = None
                        if not record.lazy_contexts:
                            _registry.pop(f, None)

        else:

            @staticmethod
            @lru_cache(maxsize=_TEMPLATE_CACHE_MAX_SIZE)
            def _build_template(code: "CodeType") -> "CodeType":
                """Build a cacheable, instance-agnostic instrumented copy of *code*.

                The instance-specific context object is replaced with a placeholder
                so that the result can be shared across multiple closures backed by
                the same code object; see wrap(). Memoized via lru_cache, keyed on
                the code object, so repeated wraps of closures sharing the same
                underlying code skip the decompile/instrument/recompile.
                """
                bc = Bytecode.from_code(code)

                # Prefix every return
                i = 0
                while i < len(bc):
                    instr = bc[i]
                    if isinstance(instr, bytecode.Instr):
                        if instr.name == "RETURN_VALUE":
                            return_code = CONTEXT_RETURN.bind({"context": _CONTEXT_PLACEHOLDER}, lineno=instr.lineno)
                            bc[i:i] = return_code
                            i += len(return_code)
                    i += 1

                # Search for the GEN_START instruction, which needs to stay on top.
                i = 0
                if sys.version_info >= (3, 10) and (code.co_flags & (CO_GENERATOR | CO_COROUTINE)):
                    for i, instr in enumerate(bc, 1):
                        if isinstance(instr, bytecode.Instr) and instr.name == "GEN_START":
                            break

                *bc[i:i], except_label = CONTEXT_HEAD.bind(
                    {"context": _CONTEXT_PLACEHOLDER}, lineno=code.co_firstlineno
                )

                bc.append(except_label)
                bc.extend(CONTEXT_FOOT.bind(lineno=code.co_firstlineno))

                return bc.to_code()

            def _template_replacements(self) -> dict[object, object]:
                return {_CONTEXT_PLACEHOLDER: self}

            def unwrap(self) -> None:
                f = t.cast(FunctionType, self.__wrapped__)

                with _registry_lock:
                    if not self.is_wrapped(f):
                        return

                    wc = _registry[f].uwc

                    bc = Bytecode.from_code(get_function_code(f))

                    # Remove the exception handling code
                    bc[-len(CONTEXT_FOOT) :] = []
                    bc.pop()

                    # Remove the head of the try block
                    for i, instr in enumerate(bc):
                        if isinstance(instr, bytecode.Instr) and instr.name == "LOAD_CONST" and instr.arg is wc:
                            break

                    bc[i : i + len(CONTEXT_HEAD) - 1] = []

                    # Remove all the return handlers
                    i = 0
                    while i < len(bc):
                        instr = bc[i]
                        if isinstance(instr, bytecode.Instr) and instr.name == "RETURN_VALUE":
                            bc[i - len(CONTEXT_RETURN) : i] = []
                            i -= len(CONTEXT_RETURN)
                        i += 1

                    # Recreate the code object
                    set_function_code(f, bc.to_code())

                    # Clear the UWC from the registry; remove the record if fully empty.
                    record: t.Optional[_ContextRecord] = _registry.get(f)
                    if record is not None:
                        record.uwc = None
                        if not record.lazy_contexts:
                            _registry.pop(f, None)


if sys.version_info >= (3, 15):

    def _finalize_monitoring_wrap(
        self_ref: "weakref.ref[_UniversalWrappingContext]",
        code: CodeType,
    ) -> None:
        """Unregister sys.monitoring when a wrapped function is collected without unwrap().

        weakref.finalize fires only after the wrapped function is unreachable, so by the
        time this runs, self.__wrapped__ is already gone; unwrap() (and self.unwrap's use
        of self.__wrapped__) cannot be used here. Clean up via the cloned monitor code
        object instead, which the finalizer callback captures directly.
        """
        self: t.Optional["_UniversalWrappingContext"] = self_ref()
        if self is None:
            return
        try:
            with _ctx_registry_lock:
                if _ctx_registry.get(code) is not self:
                    return
                del _ctx_registry[code]
            _monitoring.unregister(code, self)
        except Exception:
            log.exception("ddtrace: error during finalizer cleanup of monitoring wrap")

    def wrapping_context_for(f: FunctionType) -> "t.Optional[_UniversalWrappingContext]":
        """Return the _UniversalWrappingContext for *f*, or None if not context-wrapped."""
        try:
            return _UniversalWrappingContext.extract(f)
        except ValueError:
            return None

else:

    def wrapping_context_for(f: FunctionType) -> "t.Optional[_UniversalWrappingContext]":
        """Return the _UniversalWrappingContext for *f*, or None if not context-wrapped."""
        with _registry_lock:
            record: t.Optional[_ContextRecord] = _registry.get(f)
            return record.uwc if record is not None else None
