# distutils: include_dirs = ddtrace/appsec/include
# distutils: library_dirs = ddtrace/appsec/lib
# distutils: libraries = ddwaf

import typing
from cpython.mem cimport PyMem_Malloc, PyMem_Realloc, PyMem_Free

from _libddwaf cimport (
    ddwaf_object,
    ddwaf_object_array,
    ddwaf_object_array_add,
    ddwaf_object_invalid,
    ddwaf_object_map,
    ddwaf_object_map_addl,
    ddwaf_object_stringl,
    ddwaf_object_free,
    ddwaf_version,
    ddwaf_get_version
)


def version():
    # type: () -> typing.Tuple[int, int, int]
    cdef ddwaf_version version
    ddwaf_get_version(&version)
    return (version.major, version.minor, version.patch)


cdef ddwaf_object* _alloc():
    return <ddwaf_object *> PyMem_Malloc(sizeof(ddwaf_object))

cdef void _dealloc(ddwaf_object *obj):
    if obj != NULL:
        ddwaf_object_free(obj)
        PyMem_Free(obj)

# TODO unicode
cdef ddwaf_object* _convert(value):
    obj = _alloc()
    if obj == NULL:
        return NULL
    if isinstance(value, unicode):
        value = value.encode("utf-8", errors="surrogatepass")
    if isinstance(value, bytes):
        ddwaf_object_stringl(obj, value, len(value))
    elif isinstance(value, (list, tuple)):
        ddwaf_object_array(obj);
        for item in value:
            item_obj = _convert(item)
            ret = ddwaf_object_array_add(obj, item_obj)
            if ret is False:
                _dealloc(item_obj)
    elif isinstance(value, dict):
        ddwaf_object_map(obj)
        for k, v in value.items():
            item_obj = _convert(v)
            if isinstance(k, unicode):
                k = k.encode("utf-8", errors="surrogatepass")
            if isinstance(k, bytes):
                ret = ddwaf_object_map_addl(obj, k, len(k), item_obj)
                if ret is False:
                    _dealloc(item_obj)
    else:
        ddwaf_object_invalid(obj)
    return obj

cdef class _Wrapper:
    cdef ddwaf_object *_ptr

    def __cinit__(self, value):
        self._ptr = _convert(value)
        if self._ptr == NULL:
            raise MemoryError

    def __dealloc__(self):
        _dealloc(self._ptr)
        self._ptr = NULL
