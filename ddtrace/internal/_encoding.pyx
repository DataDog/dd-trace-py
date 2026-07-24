from cpython cimport *
from cpython.bytearray cimport PyByteArray_CheckExact
from libc cimport stdint

import threading

from .logger import get_logger


cdef object log = get_logger(__name__)


cdef extern from "pack.h":
    struct msgpack_packer:
        char* buf
        size_t length
        size_t buf_size

    int msgpack_pack_nil(msgpack_packer* pk)
    int msgpack_pack_long_long(msgpack_packer* pk, long long d)
    int msgpack_pack_unsigned_long_long(msgpack_packer* pk, unsigned long long d)
    int msgpack_pack_double(msgpack_packer* pk, double d)
    int msgpack_pack_array(msgpack_packer* pk, size_t l)
    int msgpack_pack_map(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw(msgpack_packer* pk, size_t l)
    int msgpack_pack_bin(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw_body(msgpack_packer* pk, char* body, size_t l)
    int msgpack_pack_unicode(msgpack_packer* pk, object o, long long limit)
    int msgpack_pack_true(msgpack_packer* pk)
    int msgpack_pack_false(msgpack_packer* pk)


cdef long long ITEM_LIMIT = (2**32)-1


cdef inline int PyBytesLike_CheckExact(object o):
    return PyBytes_CheckExact(o) or PyByteArray_CheckExact(o)


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


cdef class StringTable(object):
    cdef dict _table
    cdef stdint.uint32_t _next_id

    def __init__(self):
        self._table = {"": 0}
        self.insert("")
        self._next_id = 1

    cdef insert(self, object string):
        pass

    cdef stdint.uint32_t _index(self, object string) except? -1:
        cdef stdint.uint32_t _id
        cdef int ret

        if string is None:
            return 0

        ret = PyDict_Contains(self._table, string)
        if ret == -1:
            return ret
        if ret:
            return PyLong_AsLong(<object>PyDict_GetItem(self._table, string))

        _id = self._next_id
        ret = PyDict_SetItem(self._table, string, PyLong_FromLong(_id))
        if ret == -1:
            return ret
        self.insert(string)
        self._next_id += 1
        return _id

    cpdef stdint.uint32_t index(self, object string) except? -1:
        return self._index(string)

    cdef reset(self):
        PyDict_Clear(self._table)
        PyDict_SetItem(self._table, "", 0)
        self.insert("")
        self._next_id = 1

    def __len__(self):
        return PyLong_FromLong(self._next_id)

    def __contains__(self, object string):
        return PyBool_FromLong(PyDict_Contains(self._table, string))


cdef class ListStringTable(StringTable):
    cdef list _list

    def __init__(self):
        self._list = []
        super(ListStringTable, self).__init__()

    cdef insert(self, object string):
        PyList_Append(self._list, string)

    def __iter__(self):
        return iter(self._list)


cdef class BufferedEncoder(object):
    content_type: str = None

    cdef public size_t max_size
    cdef public size_t max_item_size
    cdef object _lock

    def __cinit__(self, size_t max_size, size_t max_item_size):
        self.max_size = max_size
        self.max_item_size = max_item_size
        self._lock = threading.Lock()

    # ---- Abstract methods ----

    def put(self, item):
        raise NotImplementedError()

    def encode(self):
        raise NotImplementedError()


cdef class Packer(object):
    """Slightly modified version of the v0.6.2 msgpack Packer
    which only supports basic Python types (int, bool, float, dict, list).

    Note that _only_ the basic types can be encoded. Subtypes of these types
    are not supported.

    - strict_type argument is removed and assumed to be True
    - use_bin_type argument is removed and assumed to be True (use the msgpack 2.0 bin type fields when possible)
    - use_single_float is removed and assumed to be False
    - autoreset is removed and assumed to be True (bytes are always returned from pack and the buffer reset)

    https://github.com/msgpack/msgpack-python/tree/v0.6.2
    """
    cdef msgpack_packer pk
    cdef object _default
    cdef object _berrors
    cdef const char *encoding
    cdef const char *unicode_errors

    def __cinit__(self):
        cdef int buf_size = 1024*1024
        self.pk.buf = <char*> PyMem_Malloc(buf_size)
        if self.pk.buf == NULL:
            raise MemoryError("Unable to allocate internal buffer.")
        self.pk.buf_size = buf_size
        self.pk.length = 0

    def __init__(self, default=None):
        if default is not None:
            if not PyCallable_Check(default):
                raise TypeError("default must be a callable.")
        self._default = default

        if PY_MAJOR_VERSION < 3:
            self.encoding = "utf-8"
        else:
            self.encoding = NULL

    def __dealloc__(self):
        PyMem_Free(self.pk.buf)
        self.pk.buf = NULL

    cdef int _pack(self, object o) except -1:
        cdef long long llval
        cdef unsigned long long ullval
        cdef double dval
        cdef char* rawval
        cdef int ret
        cdef dict d
        cdef Py_ssize_t L
        cdef int default_used = 0

        while True:
            if o is None:
                ret = msgpack_pack_nil(&self.pk)
            elif PyLong_CheckExact(o):
                try:
                    if o > 0:
                        ullval = o
                        ret = msgpack_pack_unsigned_long_long(&self.pk, ullval)
                    else:
                        llval = o
                        ret = msgpack_pack_long_long(&self.pk, llval)
                except OverflowError as oe:
                    if not default_used and self._default is not None:
                        o = self._default(o)
                        default_used = True
                        continue
                    else:
                        o = "Integer value out of range"
                        continue
            elif PyFloat_CheckExact(o):
                dval = o
                ret = msgpack_pack_double(&self.pk, dval)
            elif PyBytesLike_CheckExact(o):
                L = len(o)
                if L > ITEM_LIMIT:
                    PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(o).tp_name)
                rawval = o
                ret = msgpack_pack_bin(&self.pk, L)
                if ret == 0:
                    ret = msgpack_pack_raw_body(&self.pk, rawval, L)
            elif PyUnicode_CheckExact(o):
                if self.encoding == NULL:
                    ret = msgpack_pack_unicode(&self.pk, o, ITEM_LIMIT)
                    if ret == -2:
                        o = f"Unicode string is too large {L}"
                        continue
                else:
                    o = PyUnicode_AsEncodedString(o, self.encoding, self.unicode_errors)
                    L = len(o)
                    if L > ITEM_LIMIT:
                        o = f"Unicode string is too large {L}"
                        continue
                    ret = msgpack_pack_raw(&self.pk, L)
                    if ret == 0:
                        rawval = o
                        ret = msgpack_pack_raw_body(&self.pk, rawval, L)
            elif PyDict_CheckExact(o):
                d = <dict>o
                L = len(d)
                if L > ITEM_LIMIT:
                    o = f"Dictionary is too large {L}"
                    continue
                ret = msgpack_pack_map(&self.pk, L)
                if ret == 0:
                    for k, v in d.items():
                        ret = self._pack(k)
                        if ret != 0:
                            break
                        ret = self._pack(v)
                        if ret != 0:
                            break
            elif PyList_CheckExact(o):
                L = Py_SIZE(o)
                if L > ITEM_LIMIT:
                    o = f"List is too large {L}"
                    continue
                ret = msgpack_pack_array(&self.pk, L)
                if ret == 0:
                    for v in o:
                        ret = self._pack(v)
                        if ret != 0:
                            break
            elif PyBool_Check(o):
                if o:
                    ret = msgpack_pack_true(&self.pk)
                else:
                    ret = msgpack_pack_false(&self.pk)
            else:
                o = f"Can not serialize [{type(o).__name__}] object"
                continue
            return ret

    cpdef pack(self, object obj):
        cdef int ret
        try:
            ret = self._pack(obj)
        except Exception:
            self.pk.length = 0

            raise
        if ret:  # should not happen.
            raise RuntimeError("internal error")

        # Reset the buffer.
        buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
        self.pk.length = 0
        return buf

    def bytes(self):
        """Return internal buffer contents as bytes object"""
        return PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)


def packb(o, **kwargs):
    """
    Pack object `o` and return packed bytes
    See :class:`Packer` for options.
    """
    return Packer(**kwargs).pack(o)
