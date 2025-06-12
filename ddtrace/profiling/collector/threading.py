from __future__ import absolute_import

import threading
import typing  # noqa:F401

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import stack_v2
from ddtrace.settings.profiling import config

from . import _lock


class _ProfiledThreadingLock(_lock._ProfiledLock):
    pass


class ThreadingLockCollector(_lock.LockCollector):
    """Record threading.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingLock

    def _get_patch_target(self):
        # type: (...) -> typing.Any
        return threading.Lock

    def _set_patch_target(
        self,
        value,  # type: typing.Any
    ):
        # type: (...) -> None
        threading.Lock = value


# Also patch threading.Thread so echion can track thread lifetimes
def init_stack_v2():
    if config.stack.v2_enabled and stack_v2.is_available:
        _thread_set_native_id = ddtrace_threading.Thread._set_native_id
        _thread_bootstrap_inner = ddtrace_threading.Thread._bootstrap_inner

        def thread_set_native_id(self, *args, **kswargs):
            _thread_set_native_id(self, *args, **kswargs)
            stack_v2.register_thread(self.ident, self.native_id, self.name)

        def thread_bootstrap_inner(self, *args, **kwargs):
            _thread_bootstrap_inner(self, *args, **kwargs)
            stack_v2.unregister_thread(self.ident)

        ddtrace_threading.Thread._set_native_id = thread_set_native_id
        ddtrace_threading.Thread._bootstrap_inner = thread_bootstrap_inner

        # Instrument any living threads
        for thread_id, thread in ddtrace_threading._active.items():
            stack_v2.register_thread(thread_id, thread.native_id, thread.name)
