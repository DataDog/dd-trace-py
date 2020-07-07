# -*- encoding: utf-8 -*-
"""This files exposes non-gevent Python original functions."""
from ddtrace.vendor import six

try:
    import gevent.monkey
except ImportError:

    def get_original(module, func):
        return getattr(__import__(module), func)

    def is_module_patched(module):
        return False


else:
    get_original = gevent.monkey.get_original
    is_module_patched = gevent.monkey.is_module_patched


sleep = get_original("time", "sleep")

try:
    # Python ≥ 3.8
    threading_get_native_id = get_original("threading", "get_native_id")
except AttributeError:
    threading_get_native_id = None

start_new_thread = get_original(six.moves._thread.__name__, "start_new_thread")
thread_get_ident = get_original(six.moves._thread.__name__, "get_ident")
Thread = get_original("threading", "Thread")
Lock = get_original("threading", "Lock")
