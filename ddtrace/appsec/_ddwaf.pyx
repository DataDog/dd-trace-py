# distutils: include_dirs = ddtrace/appsec/include
# distutils: library_dirs = ddtrace/appsec/lib
# distutils: libraries = ddwaf

import six

from typing import Tuple
from collections import deque
from collections.abc import Mapping, Sequence

from cpython.mem cimport PyMem_Realloc, PyMem_Free
from libc.string cimport memset
from libc.stdint cimport uint64_t

from _libddwaf cimport (
    DDWAF_OBJ_TYPE,
    ddwaf_handle,
    ddwaf_context,
    ddwaf_result,
    ddwaf_object,
    ddwaf_version,
    ddwaf_get_version,
    ddwaf_init,
    ddwaf_run,
    ddwaf_result_free,
    ddwaf_destroy,
    ddwaf_context_init,
    ddwaf_context_destroy,
)


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

    def __init__(self, value, max_objects=1024):
        self._string_refs = []
        self._convert(value, max_objects)

    cdef ssize_t _reserve_obj(self, ssize_t n=1) except -1:
        cdef ssize_t idx, i
        cdef ddwaf_object *ptr
        cdef ddwaf_object *obj

        idx = self._next_idx
        ptr = self._ptr
        if idx + n > self._size:
            self._size += n + ((128 - (n % 128)) % 128)
            ptr = <ddwaf_object *> PyMem_Realloc(self._ptr, self._size * sizeof(ddwaf_object))
            if ptr == NULL:
                raise MemoryError
            memset(ptr + idx, 0, (self._size - idx) * sizeof(ddwaf_object))
            if self._ptr != NULL and ptr != self._ptr:
                # we need to patch all array objects because they use pointers to other objects
                for i in range(idx):
                    obj = ptr + i
                    if obj.array != NULL:
                        obj.array = obj.array - self._ptr + ptr
            self._ptr = ptr
        self._next_idx += n
        return idx

    cdef void _convert(self, value, max_objects) except *:
        cdef object stack
        cdef ddwaf_object *obj
        cdef ddwaf_object *item_obj
        cdef ssize_t i, j, idx

        i = 0
        stack = deque([(self._reserve_obj(), value)], maxlen=max_objects)
        while len(stack) and (max_objects is None or i < <ssize_t?> max_objects):
            idx, val = stack.popleft()
            obj = self._ptr + idx

            if isinstance(val, (int, float)):
                val = six.text_type(val)

            if isinstance(val, six.text_type):
                val = val.encode("utf-8", errors="surrogatepass")

            if isinstance(val, bytes):
                self._string_refs.append(val)
                obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING
                obj.stringValue = <bytes> val
                obj.nbEntries = len(val)

            elif isinstance(val, Mapping):
                obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP
                n = len(val)
                idx = self._reserve_obj(n)
                obj.array = self._ptr + idx
                obj.nbEntries = n
                for j, (k, v) in enumerate(six.iteritems(val)):
                    if isinstance(k, six.text_type):
                        k = k.encode("utf-8", errors="surrogatepass")
                    if isinstance(k, bytes):
                        item_obj = self._ptr + idx + j
                        self._string_refs.append(k)
                        item_obj.parameterName = <bytes> k
                        item_obj.parameterNameLength = len(k)
                        stack.append((idx + j, v))

            elif isinstance(val, Sequence):
                obj.type = DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY
                n = len(val)
                idx = self._reserve_obj(n)
                stack.extend([(idx + j, val[j]) for j in range(n)])
                obj.array = self._ptr + idx
                obj.nbEntries = n

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
