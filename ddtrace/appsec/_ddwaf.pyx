from collections import deque
import sys
from typing import Tuple

import six


if sys.version_info >= (3, 5):
    from typing import Mapping
    from typing import Sequence
else:
    from collections import Mapping, Sequence

from _libddwaf cimport DDWAF_LOG_LEVEL
from _libddwaf cimport DDWAF_OBJ_TYPE
from _libddwaf cimport ddwaf_context
from _libddwaf cimport ddwaf_context_destroy
from _libddwaf cimport ddwaf_context_init
from _libddwaf cimport ddwaf_destroy
from _libddwaf cimport ddwaf_get_version
from _libddwaf cimport ddwaf_handle
from _libddwaf cimport ddwaf_init
from _libddwaf cimport ddwaf_object
from _libddwaf cimport ddwaf_required_addresses
from _libddwaf cimport ddwaf_result
from _libddwaf cimport ddwaf_result_free
from _libddwaf cimport ddwaf_run
from _libddwaf cimport ddwaf_set_log_cb
from _libddwaf cimport ddwaf_version
from cpython.mem cimport PyMem_Free
from cpython.mem cimport PyMem_Realloc
from libc.stdint cimport uint32_t
from libc.stdint cimport uint64_t
from libc.stdint cimport uintptr_t
from libc.string cimport memset


cdef void print_trace(DDWAF_LOG_LEVEL level, const char *function, const char *file, unsigned line, const char *message, uint64_t len):
    print("[ddwaf] {}".format(message))


ddwaf_set_log_cb(print_trace, DDWAF_LOG_LEVEL.DDWAF_LOG_TRACE)


def version():
    # type: () -> Tuple[int, int, int]
    cdef ddwaf_version version
    ddwaf_get_version(&version)
    return (version.major, version.minor, version.patch)


cdef class _Wrapper(object):
    cdef ddwaf_object *_ptr
    cdef readonly object _string_refs
    cdef readonly ssize_t _size
    cdef readonly ssize_t _next_idx

    def __init__(self, value, max_objects=5000):
        self._string_refs = []
        self._convert(value, max_objects)

    cdef ssize_t _reserve_obj(self, ssize_t n=1) except -1:
        cdef ssize_t idx, i
        cdef ddwaf_object *ptr
        cdef ddwaf_object *obj

        idx = self._next_idx
        if idx + n > self._size:
            self._size += ((1000 - (n % 1000)) % 1000)
            ptr = <ddwaf_object *> PyMem_Realloc(self._ptr, self._size * sizeof(ddwaf_object))
            if ptr == NULL:
                raise MemoryError
            memset(ptr + idx, 0, (self._size - idx) * sizeof(ddwaf_object))
            if self._ptr != NULL and ptr != self._ptr:
                # we need to patch all array objects because they use pointers to other objects
                for i in range(idx):
                    obj = ptr + i
                    if (obj.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP or obj.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY) and obj.array != NULL:
                        obj.array = obj.array - self._ptr + ptr
            self._ptr = ptr
        self._next_idx += n
        return idx

    cdef void _make_string(self, ssize_t idx, const char* val, ssize_t length):
        cdef ddwaf_object *obj
        obj = self._ptr + idx
        obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING
        obj.stringValue = val
        obj.nbEntries = length

    cdef void _make_array(self, ssize_t idx, ssize_t array_idx, ssize_t nb_entries):
        cdef ddwaf_object *obj
        obj = self._ptr + idx
        obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY
        obj.array = self._ptr + array_idx
        obj.nbEntries = nb_entries

    cdef void _make_map(self, ssize_t idx, ssize_t array_idx, ssize_t nb_entries):
        cdef ddwaf_object *obj
        obj = self._ptr + idx
        obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP
        obj.array = self._ptr + array_idx
        obj.nbEntries = nb_entries

    cdef void _set_parameter(self, ssize_t idx, const char* name, ssize_t length):
        cdef ddwaf_object *obj
        obj = self._ptr + idx
        obj.parameterName = name
        obj.parameterNameLength = length

    cdef void _convert(self, value, max_objects) except *:
        cdef object stack
        cdef ssize_t i, j, n, idx, items_idx

        i = 0
        stack = deque([(self._reserve_obj(), value)], maxlen=max_objects)
        while len(stack) and (max_objects is None or i < <ssize_t?> max_objects):
            idx, val = stack.popleft()

            if isinstance(val, (int, float)):
                val = six.text_type(val)

            if isinstance(val, six.text_type):
                val = val.encode("utf-8", errors="surrogatepass")

            if isinstance(val, bytes):
                self._string_refs.append(val)
                self._make_string(idx, <bytes> val, len(val))

            elif isinstance(val, Mapping):
                n = len(val)
                items_idx = self._reserve_obj(n)
                self._make_map(idx, items_idx, n)
                # size of val must not change!! should not happen
                # while holding the GIL?
                for j, (k, v) in enumerate(six.iteritems(val)):
                    if isinstance(k, six.text_type):
                        k = k.encode("utf-8", errors="surrogatepass")
                    if isinstance(k, bytes):
                        self._string_refs.append(k)
                        self._set_parameter(items_idx + j, <bytes> k, len(k))
                        stack.append((items_idx + j, v))

            elif isinstance(val, Sequence):
                n = len(val)
                items_idx = self._reserve_obj(n)
                self._make_array(idx, items_idx, n)
                stack.extend([(items_idx + j, val[j]) for j in range(n)])

            i += 1

    def __repr__(self):
        return "<_Wrapper for {0._next_idx} elements>".format(self)

    def __sizeof__(self):
        return super(_Wrapper, self).__sizeof__() + self._size

    def __dealloc__(self):
        PyMem_Free(self._ptr)


cdef class DDWaf(object):
    cdef ddwaf_handle _handle
    cdef object _rules

    def __init__(self, rules):
        cdef ddwaf_object* rule_objects
        self._rules = _Wrapper(rules, max_objects=None)
        rule_objects = (<_Wrapper?>self._rules)._ptr;
        self._handle = ddwaf_init(rule_objects, NULL)
        if <void *> self._handle == NULL:
            raise ValueError("invalid rules")

    @property
    def required_data(self):
        cdef uint32_t size
        cdef const char* const* ptr

        addresses = []
        ptr = ddwaf_required_addresses(self._handle, &size)
        for i in range(size):
            addresses.append((<bytes> ptr[i]).decode("utf-8"))
        return addresses

    def run(self, data, timeout_ms=1000):
        cdef ddwaf_context ctx
        cdef ddwaf_result result

        ctx = ddwaf_context_init(self._handle, NULL)
        if <void *> ctx == NULL:
            raise RuntimeError
        try:
            wrapper = _Wrapper(data)
            ddwaf_run(ctx, (<_Wrapper?>wrapper)._ptr, &result, <uint64_t?> timeout_ms)
            if result.data != NULL:
                return (<bytes> result.data).decode("utf-8")
        finally:
            ddwaf_result_free(&result)
            ddwaf_context_destroy(ctx)

    def __dealloc__(self):
        ddwaf_destroy(self._handle)
