# distutils: include_dirs = ddtrace/appsec/include
# distutils: library_dirs = ddtrace/appsec/lib
# distutils: libraries = ddwaf

import attr
import six
import typing
from cpython.mem cimport PyMem_Malloc, PyMem_Realloc, PyMem_Free

from _libddwaf cimport (
    ddwaf_object,
    ddwaf_object_invalid,
    ddwaf_object_array,
    ddwaf_object_map,
    ddwaf_object_stringl_nc,
    ddwaf_version,
    ddwaf_get_version
)


def version():
    # type: () -> typing.Tuple[int, int, int]
    cdef ddwaf_version version
    ddwaf_get_version(&version)
    return (version.major, version.minor, version.patch)


cdef class _Wrapper(object):
    cdef ddwaf_object *_ptr
    cdef public object _strings
    cdef public ssize_t _size
    cdef public ssize_t _next_idx

    def __init__(self, value):
        self._strings = []
        self._convert(self._reserve_obj(), value)

    cdef ddwaf_object* _reserve_obj(self, ssize_t n=1) except NULL:
        cdef ssize_t idx, i
        cdef ddwaf_object* ptr

        idx = self._next_idx
        ptr = self._ptr
        if idx + n > self._size:
            self._size += n + ((64 - (n % 64)) % 64)
            ptr = <ddwaf_object *> PyMem_Realloc(self._ptr, self._size * sizeof(ddwaf_object))
            if ptr == NULL:
                raise MemoryError
            self._ptr = ptr
        self._next_idx += n
        for i in range(idx, idx + n):
            ddwaf_object_invalid(ptr + i)
        return ptr + idx

    cdef void _convert(self, ddwaf_object* obj, value) except *:
        cdef ssize_t i

        if isinstance(value, (int, float)):
            value = str(value)

        if isinstance(value, six.text_type):
            value = value.encode("utf-8", errors="surrogatepass")

        if isinstance(value, bytes):
            self._strings.append(value)
            ddwaf_object_stringl_nc(obj, value, len(value))

        elif isinstance(value, (list, tuple)):
            ddwaf_object_array(obj);
            n = len(value)
            items_obj = self._reserve_obj(n)
            for i in range(n):
                self._convert(items_obj + i, value[i])
            obj.array = items_obj
            obj.nbEntries = n

        elif isinstance(value, dict):
            ddwaf_object_map(obj)
            n = len(value)
            items_obj = self._reserve_obj(n)
            for i, (k, v) in enumerate(six.iteritems(value)):
                if isinstance(k, six.text_type):
                    k = k.encode("utf-8", errors="surrogatepass")
                if isinstance(k, bytes):
                    item_obj = items_obj + i
                    self._strings.append(k)
                    item_obj.parameterName = k
                    item_obj.parameterNameLength = len(k)
                    self._convert(item_obj, v)
            obj.array = items_obj
            obj.nbEntries = n

    def __repr__(self):
        return "<_Wrapper for {0._next_idx} elements>".format(self)

    def __sizeof__(self):
        return super(_Wrapper, self).__sizeof__() + self._size

    def __dealloc__(self):
        PyMem_Free(self._ptr)
