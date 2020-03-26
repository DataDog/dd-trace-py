from __future__ import absolute_import

import os.path
import sys
import threading

from ddtrace.vendor.six.moves import _thread

from ddtrace.vendor import wrapt

from ddtrace import compat
from ddtrace.profiling import _attr
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.vendor import attr
from ddtrace.profiling.collector import _traceback


@event.event_class
class LockEventBase(event.Event):
    """Base Lock event."""

    lock_name = attr.ib(default=None)
    frames = attr.ib(default=None)
    nframes = attr.ib(default=None)
    thread_id = attr.ib(default=None)
    thread_name = attr.ib(default=None)
    sampling_pct = attr.ib(default=None)


@event.event_class
class LockAcquireEvent(LockEventBase):
    """A lock has been acquired."""

    wait_time_ns = attr.ib(default=None)


@event.event_class
class LockReleaseEvent(LockEventBase):
    """A lock has been released."""

    locked_for_ns = attr.ib(default=None)


def _current_thread():
    # This is a custom version of `threading.current_thread`
    # that does not try # to create a `DummyThread` on `KeyError`.
    ident = _thread.get_ident()
    try:
        thread = threading._active[ident]
    except KeyError:
        name = None
    else:
        name = thread.name
    return ident, name


# We need to know if wrapt is compiled in C or not. If it's not using the C module, then the wrappers function will
# appear in the stack trace and we need to hide it.
if os.environ.get("WRAPT_DISABLE_EXTENSIONS"):
    WRAPT_C_EXT = False
else:
    try:
        import ddtrace.vendor.wrapt._wrappers as _w  # noqa: F401
    except ImportError:
        WRAPT_C_EXT = False
    else:
        WRAPT_C_EXT = True
        del _w


class _ProfiledLock(wrapt.ObjectProxy):
    def __init__(self, wrapped, recorder, max_nframes, capture_sampler):
        wrapt.ObjectProxy.__init__(self, wrapped)
        self._self_recorder = recorder
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        frame = sys._getframe(2 if WRAPT_C_EXT else 3)
        code = frame.f_code
        self._self_name = "%s:%d" % (os.path.basename(code.co_filename), frame.f_lineno)

    def acquire(self, *args, **kwargs):
        if not self._self_capture_sampler.capture():
            return self.__wrapped__.acquire(*args, **kwargs)

        start = compat.monotonic_ns()
        try:
            return self.__wrapped__.acquire(*args, **kwargs)
        finally:
            try:
                end = self._self_acquired_at = compat.monotonic_ns()
                thread_id, thread_name = _current_thread()
                frames, nframes = _traceback.pyframe_to_frames(sys._getframe(1), self._self_max_nframes)
                self._self_recorder.push_event(
                    LockAcquireEvent(
                        lock_name=self._self_name,
                        frames=frames,
                        nframes=nframes,
                        thread_id=thread_id,
                        thread_name=thread_name,
                        wait_time_ns=end - start,
                        sampling_pct=self._self_capture_sampler.capture_pct,
                    )
                )
            except Exception:
                pass

    def release(self, *args, **kwargs):
        try:
            return self.__wrapped__.release(*args, **kwargs)
        finally:
            try:
                if hasattr(self, "_self_acquired_at"):
                    try:
                        end = compat.monotonic_ns()
                        frames, nframes = _traceback.pyframe_to_frames(sys._getframe(1), self._self_max_nframes)
                        thread_id, thread_name = _current_thread()
                        self._self_recorder.push_event(
                            LockReleaseEvent(
                                lock_name=self._self_name,
                                frames=frames,
                                nframes=nframes,
                                thread_id=thread_id,
                                thread_name=thread_name,
                                locked_for_ns=end - self._self_acquired_at,
                                sampling_pct=self._self_capture_sampler.capture_pct,
                            )
                        )
                    finally:
                        del self._self_acquired_at
            except Exception:
                pass

    acquire_lock = acquire


class FunctionWrapper(wrapt.FunctionWrapper):
    # Override the __get__ method: whatever happens, _allocate_lock is always considered by Python like a "static"
    # method, even when used as a class attribute. Python never tried to "bind" it to a method, because it sees it is a
    # builtin function. Override default wrapt behavior here that tries to detect bound method.
    def __get__(self, instance, owner=None):
        return self


@attr.s
class LockCollector(collector.CaptureSamplerCollector):
    """Record lock usage."""

    nframes = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_FRAMES", 64, int))

    def start(self):
        """Start collecting `threading.Lock` usage."""
        super(LockCollector, self).start()
        self.patch()

    def stop(self):
        """Stop collecting `threading.Lock` usage."""
        self.unpatch()
        super(LockCollector, self).stop()

    def patch(self):
        """Patch the threading module for tracking lock allocation."""
        # We only patch the lock from the `threading` module.
        # Nobody should use locks from `_thread`; if they do so, then it's deliberate and we don't profile.
        self.original = threading.Lock

        def _allocate_lock(wrapped, instance, args, kwargs):
            lock = wrapped(*args, **kwargs)
            return _ProfiledLock(lock, self.recorder, self.nframes, self._capture_sampler)

        threading.Lock = FunctionWrapper(self.original, _allocate_lock)

    def unpatch(self):
        """Unpatch the threading module for tracking lock allocation."""
        threading.Lock = self.original
