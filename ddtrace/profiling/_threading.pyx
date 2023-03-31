from __future__ import absolute_import

import sys
import typing
import weakref

import attr
from six.moves import _thread

from ddtrace import _threading as ddtrace_threading


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
        except KeyError:
            try:
                return threading_mod._limbo[thread_id]
            except KeyError:
                pass

    return None


cpdef get_thread_name(thread_id):
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


@attr.s(slots=True, eq=False)
class _ThreadLink(_thread_link_base):
    """Link a thread with an object.

    Object is removed when the thread disappears.
    """

    # Key is a thread_id
    # Value is a weakref to an object
    _thread_id_to_object = attr.ib(factory=dict, repr=False, init=False, type=typing.Dict[int, _weakref_type])

    def link_object(
            self,
            obj  # type: _T
    ):
        # type: (...) -> None
        """Link an object to the current running thread."""
        self._thread_id_to_object[_thread.get_ident()] = weakref.ref(obj)

    def clear_threads(self,
                      existing_thread_ids,  # type: typing.Set[int]
                      ):
        """Clear the stored list of threads based on the list of existing thread ids.

        If any thread that is part of this list was stored, its data will be deleted.

        :param existing_thread_ids: A set of thread ids to keep.
        """
        # This code clears the thread/object mapping by clearing a copy and swapping it in an atomic operation This is
        # needed to be able to have this whole class lock-free and avoid concurrency issues.
        # The fact that it is lock free means we might lose some accuracy, but it's worth the trade-off for speed and simplicity.
        new_thread_id_to_object_mapping = self._thread_id_to_object.copy()
        # Iterate over a copy of the list of keys since it's mutated during our iteration.
        for thread_id in list(new_thread_id_to_object_mapping):
            if thread_id not in existing_thread_ids:
                del new_thread_id_to_object_mapping[thread_id]

        # Swap with the new list
        self._thread_id_to_object = new_thread_id_to_object_mapping

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
