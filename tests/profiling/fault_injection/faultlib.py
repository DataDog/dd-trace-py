"""A minimal fault-injection framework for the ddtrace profiler.

Goal
----
Complement the existing "fuzz the memory reads" style tests (memalloc snapshot
parsing, etc.) with **lifecycle** fault injection: make every stage of the
profiler's start / run / stop pipeline fail in messy ways and assert the
profiler degrades gracefully.

The profiler lifecycle (see ddtrace/profiling/profiler.py) is:

    Profiler.start()
      └─ _ProfilerInstance.start()           [Service: status + _service_lock]
           ├─ _start_exporter()              ddup.config(), ddup.start()
           ├─ _start_collectors()            each Collector.start()
           │    ├─ StackCollector._init()     native sampling thread, core.on(...)
           │    └─ ...                        one PeriodicThread per collector
           └─ Scheduler.start()               one PeriodicThread (upload worker)
    run
      └─ Scheduler.periodic() every interval
           ├─ before_flush() == _collectors_snapshot()   each Collector.snapshot()
           └─ ddup.upload()
    Profiler.stop()
      ├─ Scheduler.stop() + join() (+ final flush)
      └─ each Collector.stop() + join()

Every box in that tree is a fault point.  This framework lets a test arm a
fault at any of them with a handful of primitives:

    raise_()     inject an exception at the call site
    hang()       block the calling thread (forever, or for a while)
    skip()      do not run the original call, return a canned value
    kill()       os._exit — fatal, only meaningful in subprocess scenarios

Injection works by monkeypatching a named attribute (module function, class
method, instance method) with a wrapper that consults the fault policy.  No
ddtrace source is modified.

The framework is thread-safe: faults fire from scheduler threads, sampling
threads and workload threads alike.

Usage sketch::

    injector = Injector()
    injector.arm("ddup.start", ddup, "start", raise_(RuntimeError("boom")))
    try:
        profiler.Profiler().start()   # must not crash the app
    finally:
        injector.disarm_all()
"""

import faulthandler
import functools
import inspect
import random
import sys
import tempfile
import threading
import time
import typing


# ---------------------------------------------------------------------------
# Actions: what happens when a fault fires
# ---------------------------------------------------------------------------

ExcType = typing.Union[BaseException, typing.Callable[[], BaseException]]


class Action(typing.Protocol):
    def __call__(self, ctx: "CallContext") -> typing.Any: ...


class _Raise:
    """Raise an exception at the fault point."""

    __slots__ = ("exc",)

    def __init__(self, exc: ExcType) -> None:
        self.exc = exc

    def __call__(self, ctx: "CallContext") -> typing.Any:
        exc = self.exc() if callable(self.exc) and not isinstance(self.exc, BaseException) else self.exc
        raise exc


class _Hang:
    """Block the calling thread.  ``duration=None`` hangs forever."""

    __slots__ = ("duration",)

    def __init__(self, duration: typing.Optional[float] = None) -> None:
        self.duration = duration

    def __call__(self, ctx: "CallContext") -> typing.Any:
        if self.duration is None:
            # Park the thread until the test process goes away.  The thread is
            # a daemon so it does not block interpreter shutdown (unless the
            # code under test is joining it — which is exactly what we probe).
            threading.Event().wait()
        else:
            time.sleep(self.duration)
        # Never reached for the forever-hang; harmless otherwise.
        return None


class _Skip:
    """Do not run the original call; return ``value`` instead."""

    __slots__ = ("value",)

    def __init__(self, value: typing.Any = None) -> None:
        self.value = value

    def __call__(self, ctx: "CallContext") -> typing.Any:
        return self.value


class _Kill:
    """Terminate the process instantly (fatal fault, subprocess scenarios)."""

    __slots__ = ("code",)

    def __init__(self, code: int = 17) -> None:
        self.code = code

    def __call__(self, ctx: "CallContext") -> typing.Any:
        import os

        os._exit(self.code)


def raise_(exc: ExcType) -> Action:
    return _Raise(exc)


def hang(duration: typing.Optional[float] = None) -> Action:
    return _Hang(duration)


def skip(value: typing.Any = None) -> Action:
    return _Skip(value)


def kill(code: int = 17) -> Action:
    return _Kill(code)


# ---------------------------------------------------------------------------
# Call context and policies
# ---------------------------------------------------------------------------


class CallContext:
    """Information about the intercepted call, available to policies."""

    __slots__ = ("name", "obj", "attr", "args", "kwargs", "hits", "thread")

    def __init__(
        self,
        name: str,
        obj: typing.Any,
        attr: str,
        args: tuple,
        kwargs: dict,
        hits: int,
        thread: threading.Thread,
    ) -> None:
        self.name = name
        self.obj = obj
        self.attr = attr
        self.args = args
        self.kwargs = kwargs
        self.hits = hits  # how many times this fault point fired so far, incl. this call
        self.thread = thread

    def __repr__(self) -> str:
        return "CallContext(name=%r, hits=%d, thread=%r, args=%r)" % (self.name, self.hits, self.thread, self.args)


# A policy receives the call context and decides whether the fault fires.
# ``1`` means "fire on the first call only", ``None``/``True`` = always, an
# int n = fire on the nth call, a float p in [0, 1] = fire with probability p,
# a callable receives the context and returns a bool.
Policy = typing.Union[int, float, bool, None, typing.Callable[[CallContext], bool]]


def _make_policy(policy: Policy) -> typing.Callable[[CallContext], bool]:
    if policy is None:
        return lambda ctx: True
    if policy is True:
        return lambda ctx: True
    if policy is False:
        return lambda ctx: False
    if isinstance(policy, int):
        return lambda ctx: ctx.hits == policy
    if isinstance(policy, float):
        return lambda ctx: random.random() < policy
    return policy


# ---------------------------------------------------------------------------
# Injector
# ---------------------------------------------------------------------------


class FaultEvent(typing.NamedTuple):
    name: str
    action: str
    thread: str
    fired_at: float
    detail: str = ""


class Fault:
    """One armed fault: the patched callable plus its bookkeeping."""

    def __init__(self, name: str, action: Action, policy: typing.Callable[[CallContext], bool]) -> None:
        self.name = name
        self.action = action
        self.policy = policy
        self.hits = 0
        self.lock = threading.Lock()

    def should_fire(self, ctx: CallContext) -> bool:
        with self.lock:
            self.hits += 1
            ctx.hits = self.hits
            return self.policy(ctx)


def _make_wrapper(name: str, fault: "Fault", action: Action, original: typing.Any, recorder: "Injector") -> typing.Any:
    def wrapper(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        ctx = CallContext(name, None, "", args, kwargs, 0, threading.current_thread())
        if fault.should_fire(ctx):
            recorder._record(name, action, ctx)
            return action(ctx)
        return original(*args, **kwargs)

    return wrapper


class _ArmedFault:
    """Bookkeeping for one armed fault."""

    __slots__ = ("fault", "obj", "attr", "original", "patch_value", "is_static", "is_class")

    def __init__(
        self,
        fault: Fault,
        obj: typing.Any,
        attr: str,
        original: typing.Any,
        patch_value: typing.Any,
        is_static: bool,
        is_class: bool,
    ) -> None:
        self.fault = fault
        self.obj = obj
        self.attr = attr
        self.original = original  # strong ref to the un-patched callable
        self.patch_value = patch_value
        self.is_static = is_static
        self.is_class = is_class


def _resolve_patch_target(obj: typing.Any, attr: str) -> tuple[typing.Any, typing.Any, bool]:
    """Return (original callable, value to setattr, is_static_descriptor).

    Handles plain functions, staticmethods and classmethods so that patching
    e.g. ``StackCollector.snapshot`` (a staticmethod) keeps the same calling
    convention as the original.
    """
    raw = inspect.getattr_static(obj, attr)
    if isinstance(raw, staticmethod):
        return raw.__func__, raw, True  # re-wrap later into a fresh staticmethod
    if isinstance(raw, classmethod):
        return raw.__func__, raw, False
    return raw, raw, False


def _apply_patch(armed: _ArmedFault, wrapper: typing.Any) -> None:
    if armed.is_static:
        setattr(armed.obj, armed.attr, staticmethod(wrapper))
    else:
        setattr(armed.obj, armed.attr, wrapper)


def _restore_patch(armed: _ArmedFault) -> None:
    try:
        if armed.is_static:
            setattr(armed.obj, armed.attr, staticmethod(armed.original))
        else:
            setattr(armed.obj, armed.attr, armed.original)
    except (AttributeError, TypeError):
        pass


class Injector:
    """Arm faults at named call sites (module functions, class or instance methods).

    Thread-safe; all armed faults are disarmed by :meth:`disarm_all` or the
    context manager form.
    """

    def __init__(self) -> None:
        self._faults: dict[str, _ArmedFault] = {}
        self._lock = threading.Lock()
        self.history: list[FaultEvent] = []

    # -- arming ----------------------------------------------------------

    def arm(
        self,
        name: str,
        obj: typing.Any,
        attr: str,
        action: Action,
        policy: Policy = 1,
    ) -> Fault:
        """Patch ``obj.attr`` so that ``action`` may fire per ``policy``.

        :param name: unique fault name used for reporting and lookup
        :param obj: module, class or instance owning the attribute
        :param attr: attribute name to patch
        :param action: what happens when the fault fires (see actions above)
        :param policy: when the fault fires (nth call, probability, predicate)
        """
        with self._lock:
            if name in self._faults:
                raise ValueError(f"fault {name!r} is already armed")
            original, _, is_static = _resolve_patch_target(obj, attr)
            fault = Fault(name, action, _make_policy(policy))
            wrapper = _make_wrapper(name, fault, action, original, self)
            try:
                functools.wraps(original)(wrapper)
            except (AttributeError, TypeError):  # builtins without metadata
                pass

            target = obj
            try:
                armed = _ArmedFault(fault, target, attr, original, None, is_static, isinstance(target, type))
                _apply_patch(armed, wrapper)
            except (AttributeError, TypeError):
                # e.g. __slots__ instances: patch on the class instead
                target = obj if isinstance(obj, type) else type(obj)
                original, _, is_static = _resolve_patch_target(target, attr)
                armed = _ArmedFault(fault, target, attr, original, None, is_static, True)
                _apply_patch(armed, wrapper)
            self._faults[name] = armed
            return fault

    def disarm(self, name: str) -> None:
        with self._lock:
            armed = self._faults.pop(name, None)
            if armed is not None:
                _restore_patch(armed)

    def disarm_all(self) -> None:
        for name in list(self._faults):
            self.disarm(name)

    def __enter__(self) -> "Injector":
        return self

    def __exit__(self, *exc: typing.Any) -> None:
        self.disarm_all()

    # -- introspection -----------------------------------------------------

    def fired(self, name: str) -> int:
        armed = self._faults.get(name)
        return armed.fault.hits if armed else 0

    def events(self, name: typing.Optional[str] = None) -> list[FaultEvent]:
        return [e for e in self.history if name is None or e.name == name]

    def _record(self, name: str, action: Action, ctx: CallContext) -> None:
        self.history.append(
            FaultEvent(
                name=name,
                action=type(action).__name__.strip("_").lower(),
                thread=ctx.thread.name,
                fired_at=time.monotonic(),
                detail=repr(ctx.args[:2]),
            )
        )


# ---------------------------------------------------------------------------
# Oracle: liveness / containment checks that must hold under any fault
# ---------------------------------------------------------------------------


class DeadlineExceeded(AssertionError):
    """A lifecycle call did not return in time — the profiler hung."""


def completes_within(
    fn: typing.Callable[[], typing.Any],
    timeout: float,
    label: str = "call",
) -> typing.Any:
    """Run ``fn`` and require it to return within ``timeout`` seconds.

    On timeout, dumps every thread's stack (via faulthandler) into the
    exception message so the report shows exactly where the profiler is stuck.
    """
    result: list = []
    error: list = []

    def _run() -> None:
        try:
            result.append(fn())
        except BaseException as e:  # noqa: BLE001 — propagate to caller below
            error.append(e)

    t = threading.Thread(target=_run, name=f"faultlib-watchdog[{label}]", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        # faulthandler requires a real file (fileno), not StringIO
        with tempfile.TemporaryFile(mode="w+") as buf:
            faulthandler.dump_traceback(file=buf)
            buf.seek(0)
            stacks = buf.read()
        raise DeadlineExceeded(
            f"{label} did not complete within {timeout}s.\n"
            f"--- all thread stacks at deadline ---\n{stacks}"
        )
    if error:
        raise error[0]
    return result[0] if result else None


# Profiler threads live under ddtrace.profiling.*; other ddtrace products (telemetry,
# remote configuration, ...) run their own periodic threads that may legitimately
# outlive the profiler. Findings are therefore classified per product.
_PRODUCT_THREAD_MARKERS: "typing.OrderedDict[str, tuple[str, ...]]" = {
    # order matters: first match wins (telemetry is a prefix-specialization of internal)
    "telemetry": ("ddtrace.internal.telemetry",),
    "profiling": ("ddtrace.profiling",),
    "remote-config": ("ddtrace.internal.remoteconfig", "ddtrace.internal.datastreams"),
    "other-internal": ("ddtrace.internal", "ddtrace.appsec", "ddtrace.lla", "ddtrace.llmobs"),
}


def _classify_thread_name(name: str) -> typing.Optional[str]:
    for product, markers in _PRODUCT_THREAD_MARKERS.items():
        if any(marker in name for marker in markers):
            return product
    return None


def _iter_native_periodic_threads():
    try:
        from ddtrace.internal import _threads
    except ImportError:
        return
        yield  # pragma: no cover
    yield from _threads.periodic_threads.values()


def _leftover_candidates() -> list:
    """All profiler/product worker threads still alive, native and Python alike."""
    me = threading.current_thread()
    python_side = [
        t
        for t in threading.enumerate()
        if t is not me and not t.daemon and _classify_thread_name(t.name) is not None
    ]
    native_side = [
        t
        for t in _iter_native_periodic_threads()
        if _classify_thread_name(str(getattr(t, "name", ""))) is not None
    ]
    return python_side + list(native_side)


def leftover_threads_by_product() -> "typing.OrderedDict[str, list]":
    """Leftover ddtrace product threads, grouped by product.

    Split per product so failures name their owner: a leftover
    ``ddtrace.profiling.*`` worker is a profiler leak; a leftover
    ``ddtrace.internal.telemetry.*`` worker belongs to the telemetry product
    (e.g. the dependency collector started by product activation, which
    outlives Profiler.stop by design and is stopped at process exit).
    """
    import collections

    grouped: "typing.OrderedDict[str, list]" = collections.OrderedDict()
    for t in _leftover_candidates():
        product = _classify_thread_name(str(getattr(t, "name", ""))) or "unknown"
        grouped.setdefault(product, []).append(t)
    return grouped


def format_leftovers_by_product(grouped: "typing.Optional[typing.OrderedDict[str, list]]" = None) -> str:
    grouped = grouped if grouped is not None else leftover_threads_by_product()
    if not grouped:
        return "no leftover ddtrace threads"
    return "; ".join(
        f"{product}: {[str(getattr(t, 'name', t)) for t in threads]}" for product, threads in grouped.items()
    )


def leftover_profiler_threads() -> list:
    """Profiler (ddtrace.profiling.*) worker threads still alive after a stop."""
    return leftover_threads_by_product().get("profiling", [])


def periodic_profiler_threads() -> list:
    """Native profiler worker threads still running (should be empty after stop)."""
    return [
        t
        for t in _iter_native_periodic_threads()
        if _classify_thread_name(str(getattr(t, "name", ""))) == "profiling"
    ]


class Workload:
    """A small application workload to run under the profiler.

    Several threads hammer CPU, allocations and lock contention while the
    profiler is running, so that snapshots have something to chew on and any
    exception leaking out of profiler internals into *application* threads can
    be detected (containment oracle).
    """

    def __init__(self, threads: int = 3, duration: float = 1.0) -> None:
        self.threads = threads
        self.duration = duration
        self.exceptions: list[tuple[str, BaseException]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _spin(self, n: int) -> None:
        deadline = time.monotonic() + self.duration
        acc = 0
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                with self._lock:
                    # CPU + allocation + lock churn, in the spirit of a real app
                    for i in range(200):
                        acc = (acc * 31 + i) % 1000003
                        _ = [acc] * 8
                    _ = sorted(str(acc) for _ in range(16))
                if acc == -1:
                    raise RuntimeError("unreachable")
            except BaseException as e:  # noqa: BLE001 — containment oracle
                self.exceptions.append((f"workload-{n}", e))
                return

    def run(self) -> None:
        self._stop.clear()
        threads = [
            threading.Thread(target=self._spin, args=(n,), name=f"faultlib-workload-{n}", daemon=True)
            for n in range(self.threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.duration + 10)

    def stop(self) -> None:
        self._stop.set()
