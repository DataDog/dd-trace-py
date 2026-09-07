"""try_wrap_context lifecycle: what breaks when a real application patches, reloads or forks.

These target the wrapping-context hooks specifically rather than the whole mechanism matrix, so
they opt out of the `mech` guardrail in conftest.py.
"""

import functools
import gc
import os
import sys
import threading

import pytest
import wrapt

from ddtrace.internal.wrapping.context import _STORAGE_PREV
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import _MODULE_HOOKS
from ddtrace.internal.wrapping.hooks import _SUPERSEDED_CONTEXTS
from ddtrace.internal.wrapping.hooks import _WRAPPING_CONTEXTS
from ddtrace.internal.wrapping.hooks import target_function
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context
from tests.wrapping import hooks_target


pytestmark = pytest.mark.mechanism_specific

MODULE = "tests.wrapping.hooks_target"
requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")


class Blocked(BaseException):
    """Stands in for a RASP block, which must not be catchable by an `except Exception`."""


@pytest.fixture(autouse=True)
def restore_globals():
    """Both registries and the target module are process-global, so put them back afterwards."""
    contexts = dict(_WRAPPING_CONTEXTS)
    superseded = {key: list(value) for key, value in _SUPERSEDED_CONTEXTS.items()}
    hooks = {key: list(value) for key, value in _MODULE_HOOKS.items()}
    original_method = hooks_target.Target.__dict__["method"]
    original_function = hooks_target.function
    try:
        yield
    finally:
        for module_name, name in set(_WRAPPING_CONTEXTS) - set(contexts):
            try_unwrap_context(module_name, name)
        _WRAPPING_CONTEXTS.clear()
        _WRAPPING_CONTEXTS.update(contexts)
        _SUPERSEDED_CONTEXTS.clear()
        _SUPERSEDED_CONTEXTS.update(superseded)
        _MODULE_HOOKS.clear()
        _MODULE_HOOKS.update(hooks)
        hooks_target.Target.method = original_method
        hooks_target.function = original_function


def chain_depth(context):
    """How many per-call storage dicts the context still holds on its context variable."""
    depth = 0
    storage = context._storage.get()
    while storage is not None:
        depth += 1
        storage = storage.get(_STORAGE_PREV)
    return depth


def recorder(calls):
    """A context that records the argument it saw, reading it through per-call storage."""

    class _Recorder(WrappingContext):
        def __enter__(self):
            super().__enter__()
            self.set("value", self.get_local("value"))
            calls.append(("enter", self.get("value")))
            return self

        def __return__(self, retval):
            calls.append(("return", self.get("value")))
            return super().__return__(retval)

    return _Recorder


def test_a_context_wraps_an_already_imported_module():
    calls = []
    context_cls = recorder(calls)

    try_wrap_context(MODULE, "Target.method", context_cls)

    assert hooks_target.Target().method(3) == 6
    assert calls == [("enter", 3), ("return", 3)]

    try_unwrap_context(MODULE, "Target.method")
    assert hooks_target.Target().method(3) == 6
    assert calls == [("enter", 3), ("return", 3)]


def test_a_context_wraps_a_module_that_is_imported_later(tmp_path):
    """The registration has to survive until import: a hook is all we can install up front."""
    (tmp_path / "late_import_target.py").write_text("def work(value):\n    return value * 10\n")
    sys.path.insert(0, str(tmp_path))
    calls = []

    class _Marker(WrappingContext):
        def __enter__(self):
            super().__enter__()
            calls.append(self.get_local("value"))
            return self

    try:
        try_wrap_context("late_import_target", "work", _Marker)
        assert ("late_import_target", "work") not in _WRAPPING_CONTEXTS

        import late_import_target

        assert late_import_target.work(2) == 20
        assert calls == [2]
    finally:
        try_unwrap_context("late_import_target", "work")
        sys.modules.pop("late_import_target", None)
        sys.path.remove(str(tmp_path))


def test_binding_looks_through_a_wrapt_proxy_on_the_attribute():
    """The defect this helper exists for: a context must never bind to a wrapt proxy.

    getattr on a proxy builds a fresh BoundFunctionWrapper every time, so a context bound to one
    can never be found again and unwrap silently leaves the code object rewritten.
    """
    plain = hooks_target.Target.__dict__["method"]

    def passthrough(wrapped, instance, args, kwargs):
        return wrapped(*args, **kwargs)

    hooks_target.Target.method = wrapt.FunctionWrapper(plain, passthrough)
    # isinstance would say True here, which is exactly why the helper tests type() instead.
    assert isinstance(hooks_target.Target.__dict__["method"], wrapt.FunctionWrapper)

    assert target_function(hooks_target, "Target.method") is plain

    calls = []
    try_wrap_context(MODULE, "Target.method", recorder(calls))

    installed = _WRAPPING_CONTEXTS[(MODULE, "Target.method")]
    assert installed._wrapped_ref() is plain

    # Both layers still work, and ours runs even though the proxy sits above it.
    assert hooks_target.Target().method(4) == 8
    assert calls == [("enter", 4), ("return", 4)]


def test_repeated_patch_cycles_do_not_grow_the_code_object():
    """Unwrap rebuilds the code object, so a botched cycle rewrites on top of the last one.

    Left unchecked the code object grows every cycle until the bytecode library cannot parse it,
    which surfaces as a KeyError far away from the cause. Remote config toggles this at runtime.
    """
    plain = hooks_target.Target.__dict__["method"]
    baseline = len(plain.__code__.co_code)

    def passthrough(wrapped, instance, args, kwargs):
        return wrapped(*args, **kwargs)

    for cycle in range(4):
        # Alternate the order so the context binds under a proxy on half the cycles.
        if cycle % 2:
            hooks_target.Target.method = wrapt.FunctionWrapper(plain, passthrough)

        try_wrap_context(MODULE, "Target.method", recorder([]))
        assert hooks_target.Target().method(5) == 10

        try_unwrap_context(MODULE, "Target.method")
        hooks_target.Target.method = plain
        assert len(plain.__code__.co_code) == baseline, f"code object grew on cycle {cycle}"


def test_wrapping_the_same_target_twice_is_a_no_op():
    """WrappingContext.register raises on a repeated type, unlike a repeated wrapt patch."""
    calls = []
    context_cls = recorder(calls)

    try_wrap_context(MODULE, "Target.method", context_cls)
    first = _WRAPPING_CONTEXTS[(MODULE, "Target.method")]

    try_wrap_context(MODULE, "Target.method", context_cls)

    assert _WRAPPING_CONTEXTS[(MODULE, "Target.method")] is first
    # Still exactly one layer: a second registration would record the call twice.
    assert hooks_target.Target().method(1) == 2
    assert calls == [("enter", 1), ("return", 1)]


def test_the_context_rebinds_when_the_module_is_reloaded():
    """A reload rebinds the attribute to a brand new function that nothing has wrapped."""
    calls = []
    try_wrap_context(MODULE, "function", recorder(calls))
    key = (MODULE, "function")
    first = _WRAPPING_CONTEXTS[key]

    def reloaded(value):
        return value + 100

    hooks_target.function = reloaded
    for hook in _MODULE_HOOKS[key]:
        hook(hooks_target)

    rebound = _WRAPPING_CONTEXTS[key]
    assert rebound is not first
    assert rebound._wrapped_ref() is reloaded
    assert hooks_target.function(1) == 101
    assert calls == [("enter", 1), ("return", 1)]


def test_rebinding_works_once_the_old_function_is_collected():
    """The stale context holds only a weakref, and reading __wrapped__ once it dies raises.

    Letting that RuntimeError escape aborts the hook and leaves the stale entry in place, so the
    target stays unwrapped for the rest of the process.
    """

    # A throwaway stands in for the pre-reload function: the module's own one is referenced by
    # the restore fixture, which would keep the weakref alive and make this test vacuous.
    def stale_function(value):
        return value - 1

    hooks_target.function = stale_function
    try_wrap_context(MODULE, "function", recorder([]))
    key = (MODULE, "function")
    stale = _WRAPPING_CONTEXTS[key]

    def reloaded(value):
        return value + 100

    hooks_target.function = reloaded
    del stale_function
    gc.collect()
    assert stale._wrapped_ref() is None, "the old function is still referenced; test is vacuous"

    for hook in _MODULE_HOOKS[key]:
        hook(hooks_target)

    assert _WRAPPING_CONTEXTS[key]._wrapped_ref() is reloaded
    assert hooks_target.function(1) == 101


def test_a_context_that_cannot_wrap_leaves_the_target_alone():
    """Losing our hook is acceptable; breaking the application's own call is not."""

    class _Unwrappable(WrappingContext):
        def wrap(self):
            raise RuntimeError("bytecode rewriting failed")

    try_wrap_context(MODULE, "function", _Unwrappable)

    assert (MODULE, "function") not in _WRAPPING_CONTEXTS
    assert hooks_target.function(1) == 2


def test_per_call_storage_is_released_on_every_call():
    """Storage chains through _STORAGE_PREV, so a missed release grows without bound."""
    try_wrap_context(MODULE, "function", recorder([]))
    context = _WRAPPING_CONTEXTS[(MODULE, "function")]

    for _ in range(50):
        hooks_target.function(1)

    assert chain_depth(context) == 0


def test_concurrent_calls_do_not_share_per_call_storage():
    """Storage lives in a ContextVar, so each thread's in-flight request must be isolated."""
    both_inside = threading.Barrier(2, timeout=10)
    seen = {}

    class _Concurrent(WrappingContext):
        def __enter__(self):
            super().__enter__()
            self.set("value", self.get_local("value"))
            # Hold both calls open at once, so one thread's storage could overwrite the other's.
            both_inside.wait()
            return self

        def __return__(self, retval):
            seen[threading.current_thread().name] = self.get("value")
            return super().__return__(retval)

    try_wrap_context(MODULE, "function", _Concurrent)
    context = _WRAPPING_CONTEXTS[(MODULE, "function")]

    threads = [threading.Thread(target=hooks_target.function, args=(value,), name=f"t{value}") for value in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert seen == {"t1": 1, "t2": 2}
    assert chain_depth(context) == 0


def test_a_base_exception_from_enter_propagates_and_releases_storage():
    """AppSec blocks a request this way, so it must reach the caller without stranding storage."""

    class _Blocking(WrappingContext):
        def __enter__(self):
            super().__enter__()
            raise Blocked("blocked")

    try_wrap_context(MODULE, "function", _Blocking)
    context = _WRAPPING_CONTEXTS[(MODULE, "function")]

    for _ in range(5):
        with pytest.raises(Blocked):
            hooks_target.function(1)

    assert chain_depth(context) == 0


@requires_fork
def test_the_wrapping_survives_a_fork():
    """Bytecode rewriting mutates the function in place, so a child inherits it without a hook."""
    calls = []
    try_wrap_context(MODULE, "Target.method", recorder(calls))

    pid = os.fork()
    if pid == 0:
        try:
            calls.clear()
            result = hooks_target.Target().method(6)
            os._exit(12 if result == 12 and calls == [("enter", 6), ("return", 6)] else 1)
        except BaseException:
            os._exit(2)

    assert hooks_target.Target().method(6) == 12

    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 12


@requires_fork
def test_forking_inside_a_wrapped_call_leaves_no_storage_in_the_child():
    """Both processes unwind through __return__, each releasing its own copy of the storage."""

    class _Plain(WrappingContext):
        pass

    try_wrap_context(MODULE, "fork_and_return_pid", _Plain)
    context = _WRAPPING_CONTEXTS[(MODULE, "fork_and_return_pid")]

    # The fork happens inside the wrapped body, so the child resumes mid-call and returns from it.
    pid = hooks_target.fork_and_return_pid()
    if pid == 0:
        try:
            os._exit(12 if chain_depth(context) == 0 else 1)
        except BaseException:
            os._exit(2)

    assert chain_depth(context) == 0

    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 12


def test_binding_peels_through_a_functools_wraps_decorator():
    """A wraps decorator is itself a FunctionType, so stopping at the first function binds to it.

    Its frame holds only args/kwargs, so every argument read by name comes back None and the hook
    silently stops inspecting. wraps also copies __name__, hiding the mistake from that side.
    """
    plain = hooks_target.Target.__dict__["method"]

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    hooks_target.Target.method = deco(plain)

    resolved = target_function(hooks_target, "Target.method")
    assert resolved is plain
    # __name__ is copied by wraps and so agrees either way; co_name is what actually differs, and
    # it is what report_stack matches crop anchors against.
    assert resolved.__code__.co_name == "method"

    calls = []
    try_wrap_context(MODULE, "Target.method", recorder(calls))

    assert hooks_target.Target().method(7) == 14
    # The argument is only visible by name if we bound below the decorator.
    assert calls == [("enter", 7), ("return", 7)]


def test_rebinding_keeps_the_old_function_instrumented():
    """A reload rebinds the attribute; it does not touch what already holds the old function.

    Imported aliases, subclasses and already-constructed instances keep calling it, so releasing
    the superseded context here would silently drop instrumentation for all of them.
    """

    def stale_function(value):
        return value - 1

    hooks_target.function = stale_function
    calls = []
    try_wrap_context(MODULE, "function", recorder(calls))
    key = (MODULE, "function")

    def reloaded(value):
        return value + 100

    hooks_target.function = reloaded
    for hook in _MODULE_HOOKS[key]:
        hook(hooks_target)

    calls.clear()
    assert stale_function(1) == 0
    assert calls == [("enter", 1), ("return", 1)], "the alias lost its instrumentation"

    calls.clear()
    assert hooks_target.function(1) == 101
    assert calls == [("enter", 1), ("return", 1)]


def test_unwrapping_releases_the_superseded_contexts_too():
    """Retaining the old function's context is only safe if unpatch still reaches it."""

    def stale_function(value):
        return value - 1

    hooks_target.function = stale_function
    calls = []
    try_wrap_context(MODULE, "function", recorder(calls))
    key = (MODULE, "function")

    def reloaded(value):
        return value + 100

    hooks_target.function = reloaded
    for hook in _MODULE_HOOKS[key]:
        hook(hooks_target)
    assert len(_SUPERSEDED_CONTEXTS[key]) == 1

    try_unwrap_context(MODULE, "function")

    calls.clear()
    assert stale_function(1) == 0
    assert reloaded(1) == 101
    assert calls == [], "unpatching left a function instrumented"
    assert key not in _SUPERSEDED_CONTEXTS


def test_repeated_reloads_do_not_accumulate_superseded_contexts():
    """Collected functions are pruned, so a module reloaded in a loop does not grow the list."""
    key = (MODULE, "function")

    def make(offset):
        def generated(value):
            return value + offset

        return generated

    hooks_target.function = make(0)
    try_wrap_context(MODULE, "function", recorder([]))

    for offset in range(1, 6):
        hooks_target.function = make(offset)
        for hook in _MODULE_HOOKS[key]:
            hook(hooks_target)
        gc.collect()

    # Only the immediately previous function is still referenced, by the registry entry we just
    # replaced; the earlier ones were collected and pruned.
    assert len(_SUPERSEDED_CONTEXTS[key]) <= 2, _SUPERSEDED_CONTEXTS[key]
