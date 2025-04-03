from __future__ import absolute_import

import sys
import typing
import weakref

from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._unpatched import _threading as ddtrace_threading


from cpython cimport PyLong_FromLong


cdef extern from "<Python.h>":
    # This one is provided as an opaque struct from Cython's
    # cpython/pystate.pxd, but we need to access some of its fields so we
    # redefine it here.
    ctypedef struct PyThreadState:
        unsigned long thread_id

    PyThreadState* PyThreadState_Get()


cpdef get_thread_by_id(thread_id):
    # Do not force-load the threading module if it's not already loaded
    threading = sys.modules.get("threading", ddtrace_threading)

    # Look for all threads, including the ones we create
    for threading_mod in (threading, ddtrace_threading):
        # We don't want to bother to lock anything here, especially with
        # eventlet involved 😓. We make a best effort to get the thread name; if
        # we fail, it'll just be an anonymous thread because it's either
        # starting or dying.
        try:
            return threading_mod._active[thread_id]
        except (KeyError, AttributeError):
            try:
                return threading_mod._limbo[thread_id]
            except (KeyError, AttributeError):
                pass

    return None


cpdef get_thread_name(thread_id):
    try:
        return periodic_threads[thread_id].name
    except KeyError:
        thread = get_thread_by_id(thread_id)
        return thread.name if thread is not None else None


cpdef get_thread_native_id(thread_id):
    thread = get_thread_by_id(thread_id)
    if thread is None:
        return thread_id

    try:
        # We prioritize using native ids since we expect them to be surely unique for a program. This is less true
        # for hashes since they are relative to the memory address which can easily be the same across different
        # objects.
        return thread.native_id
    except AttributeError:
        # Python < 3.8
        return hash(thread)


# cython does not play well with mypy
if typing.TYPE_CHECKING:
    _T = typing.TypeVar("_T")
    _thread_link_base = typing.Generic[_T]
    _weakref_type = weakref.ReferenceType[_T]
else:
    _thread_link_base = object
    _weakref_type = typing.Any


class _ThreadLink(_thread_link_base):
    """Link a thread with an object.

    Object is removed when the thread disappears.
    """

    __slots__ = ('_thread_id_to_object',)

    def __init__(self):
        # Key is a thread_id
        # Value is a weakref to an object
        self._thread_id_to_object: typing.Dict[int, _weakref_type] = {}

    def link_object(
            self,
            obj  # type: _T
    ):
        # type: (...) -> None
        """Link an object to the current running thread."""
        # Because threads might become tasks with some frameworks (e.g. gevent),
        # we retrieve the thread ID using the C API instead of the Python API.
        self._thread_id_to_object[<object>PyLong_FromLong(PyThreadState_Get().thread_id)] = weakref.ref(obj)

    def clear_threads(self,
                      existing_thread_ids,  # type: typing.Set[int]
                      ):
        """Clean up the thread linking map.

        We remove all threads that are not in the existing thread IDs.

        :param existing_thread_ids: A set of thread ids to keep.
        """
        self._thread_id_to_object = {
            k: v for k, v in self._thread_id_to_object.items() if k in existing_thread_ids
        }

    def get_object(
            self,
            thread_id  # type: int
    ):
        # type: (...) -> typing.Optional[_T]
        """Return the object attached to thread.

        :param thread_id: The thread id.
        :return: The attached object.
        """
        obj_ref = self._thread_id_to_object.get(thread_id)
        if obj_ref is not None:
            return obj_ref()
