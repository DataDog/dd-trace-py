# Acquire a reference to the open function from the builtins module. This is
# necessary to ensure that the open function can be used unpatched when required.
from builtins import open as unpatched_open  # noqa
from json import loads as unpatched_json_loads  # noqa

# Acquire a reference to the threading module. Some parts of the library (e.g.
# the profiler) might be enabled programmatically and therefore might end up
# getting a reference to the tracee's threading module. By storing a reference
# to the threading module used by ddtrace here, we make it easy for those parts
# to get a reference to the right threading module.
import threading as _threading  # noqa
import gc as _gc  # noqa
import sys

threading_Lock = _threading.Lock
threading_RLock = _threading.RLock
threading_Event = _threading.Event

# Unpatched _thread.allocate_lock / _thread.RLock for callers that need
# real OS-level locks (e.g. threads.py forking state).
try:
    import _thread as _thread_module

    unpatched_allocate_lock = _thread_module.allocate_lock
    unpatched_RLock = getattr(_thread_module, "RLock", threading_RLock)
except ImportError:
    unpatched_allocate_lock = threading_Lock  # type: ignore[assignment]
    unpatched_RLock = threading_RLock  # type: ignore[assignment]

# DEV: If gevent.monkey.patch_all() ran before this module was imported (the
# hostile ordering tested by tests/profiling/gevent_fork.py), the assignments
# above captured the *patched* classes. gevent.monkey.get_original() retrieves
# the pre-patch originals, so we can recover the real OS primitives here.
# Keeping real locks for ddtrace-internal paths prevents a deadlock where a
# native C++ periodic thread waits on a gevent semaphore that requires the hub
# to run, while the main thread is parked in os.register_at_fork's before-hook
# waiting for that thread to finish.
# Note: gevent patches threading.Lock and threading.Event but NOT threading.RLock
# (it only patches _allocate_lock underneath RLock). _thread.RLock is also not
# patched. See: tests/internal/test_unpatched.py::test_unpatched_primitives_after_gevent_patch_all
try:
    import gevent.monkey as _gevent_monkey  # type: ignore[import]

    if _gevent_monkey.is_object_patched("threading", "Lock"):
        threading_Lock = _gevent_monkey.get_original("threading", "Lock")
    if _gevent_monkey.is_object_patched("threading", "Event"):
        threading_Event = _gevent_monkey.get_original("threading", "Event")
    if _gevent_monkey.is_object_patched("_thread", "allocate_lock"):
        unpatched_allocate_lock = _gevent_monkey.get_original("_thread", "allocate_lock")
except ImportError:
    pass


previous_loaded_modules = frozenset(sys.modules.keys())
from subprocess import Popen as unpatched_Popen  # noqa # nosec B404
from os import close as unpatched_close  # noqa: F401, E402

loaded_modules = frozenset(sys.modules.keys())
for module in previous_loaded_modules - loaded_modules:
    del sys.modules[module]
