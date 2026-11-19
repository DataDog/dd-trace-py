# We try to import the stdlib locks from the _thread module, where they are
# implemented in C for CPython for most platforms. If that fails, we fall back
# to the threading module, which provides a pure Python implementation that
# should work on all platforms. We also make sure to grab a reference to the
# original lock classes, in case they get patched by monkey-patching libraries
# like gevent.

try:
    from _thread import allocate_lock as Lock
except ImportError:
    from threading import Lock

try:
    from _thread import RLock
except ImportError:
    from threading import RLock


__all__ = [
    "Lock",
    "RLock",
]
