from cpython cimport *
from cpython.bytearray cimport PyByteArray_Check
import struct

from ..span import Span


cdef extern from "Python.h":
    char* PyUnicode_AsUTF8AndSize(object obj, Py_ssize_t *l) except NULL

cdef extern from "pack.h":
    struct msgpack_packer:
        char* buf
        size_t length
        size_t buf_size

    int msgpack_pack_int(msgpack_packer* pk, int d)
    int msgpack_pack_nil(msgpack_packer* pk)
    int msgpack_pack_long(msgpack_packer* pk, long d)
    int msgpack_pack_long_long(msgpack_packer* pk, long long d)
    int msgpack_pack_unsigned_long_long(msgpack_packer* pk, unsigned long long d)
    int msgpack_pack_double(msgpack_packer* pk, double d)
    int msgpack_pack_array(msgpack_packer* pk, size_t l)
    int msgpack_pack_map(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw_body(msgpack_packer* pk, char* body, size_t l)
    int msgpack_pack_unicode(msgpack_packer* pk, object o, long long limit)

cdef extern from "buff_converter.h":
    object buff_to_buff(char *, Py_ssize_t)


cdef long long ITEM_LIMIT = (2**32)-1


cdef inline int PyBytesLike_Check(object o):
    return PyBytes_Check(o) or PyByteArray_Check(o)


cdef inline int pack_bytes(msgpack_packer *pk, char *bytes, Py_ssize_t l):
    cdef int ret
    cdef dict d
    ret = msgpack_pack_raw(pk, l)
    if ret == 0:
        ret = msgpack_pack_raw_body(pk, bytes, l)
    return ret


cdef class Packer(object):
    """
    MessagePack Packer

    usage::

        packer = Packer()
        astream.write(packer.pack(a))
        astream.write(packer.pack(b))

    Packer's constructor has some keyword arguments:

    :param callable default:
        Convert user type to builtin type that Packer supports.
        See also simplejson's document.
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
        cdef long longval
        cdef float fval
        cdef double dval
        cdef char* rawval
        cdef int ret
        cdef dict d
        cdef Py_ssize_t L
        cdef int default_used = 0
        cdef Py_buffer view
        cdef long i

        while True:
            if o is None:
                ret = msgpack_pack_nil(&self.pk)
            elif PyLong_Check(o):
                # PyInt_Check(long) is True for Python 3.
                # So we should test long before int.
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
                        raise OverflowError("Integer value out of range")
            elif PyInt_Check(o):
                longval = o
                ret = msgpack_pack_long(&self.pk, longval)
            elif PyFloat_Check(o):
                dval = o
                ret = msgpack_pack_double(&self.pk, dval)
            elif PyBytesLike_Check(o):
                L = len(o)
                if L > ITEM_LIMIT:
                    PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(o).tp_name)
                rawval = o
                ret = msgpack_pack_raw(&self.pk, L)
                if ret == 0:
                    ret = msgpack_pack_raw_body(&self.pk, rawval, L)
            elif PyUnicode_Check(o):
                if self.encoding == NULL:
                    ret = msgpack_pack_unicode(&self.pk, o, ITEM_LIMIT)
                    if ret == -2:
                        raise ValueError("unicode string is too large")
                else:
                    o = PyUnicode_AsEncodedString(o, self.encoding, self.unicode_errors)
                    L = len(o)
                    if L > ITEM_LIMIT:
                        raise ValueError("unicode string is too large")
                    ret = msgpack_pack_raw(&self.pk, L)
                    if ret == 0:
                        rawval = o
                        ret = msgpack_pack_raw_body(&self.pk, rawval, L)
            elif PyDict_CheckExact(o):
                d = <dict>o
                L = len(d)
                if L > ITEM_LIMIT:
                    raise ValueError("dict is too large")
                ret = msgpack_pack_map(&self.pk, L)
                if ret == 0:
                    for k, v in d.items():
                       ret = self._pack(k)
                       if ret != 0: break
                       ret = self._pack(v)
                       if ret != 0: break
            elif PyList_CheckExact(o):
                # Expect a list of traces or a list of spans
                L = len(o)
                if L > ITEM_LIMIT:
                    raise ValueError("list is too large")

                ret = msgpack_pack_array(&self.pk, L)
                if ret != 0:
                    break

                if L > 0 and PyList_CheckExact(o[0]):
                    # List of lists of spans (a list of traces)
                    for i in range(L):
                        span = o[i]
                        ret = self._pack(span)
                        if ret != 0: break
                else:
                    # List of spans
                    for i in range(L):
                        ret = self._pack(o[i])
                        if ret != 0: break

            elif isinstance(o, Span):
                has_span_type = <bint>(o.span_type is not None)
                has_meta = <bint>(len(o.meta) > 0)
                has_metrics = <bint>(len(o.metrics) > 0)

                L = 12 - (1 - has_span_type) - (1 - has_meta) - (1 - has_metrics)

                ret = msgpack_pack_map(&self.pk, L)

                if ret == 0:
                    ret = pack_bytes(&self.pk, <char *>b"trace_id", 8)
                    if ret != 0: return ret
                    ret = self._pack(o.trace_id)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"parent_id", 9)
                    if ret != 0: return ret
                    ret = self._pack(o.parent_id)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"span_id", 7)
                    if ret != 0: return ret
                    ret = self._pack(o.span_id)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"service", 7)
                    if ret != 0: return ret
                    ret = self._pack(o.service)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"resource", 8)
                    if ret != 0: return ret
                    ret = self._pack(o.resource)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"name", 4)
                    if ret != 0: return ret
                    ret = self._pack(o.name)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"error", 5)
                    if ret != 0: return ret
                    ret = self._pack(1 if o.error else 0)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"start", 5)
                    if ret != 0: return ret
                    ret = self._pack(o.start_ns)
                    if ret != 0: return ret

                    ret = pack_bytes(&self.pk, <char *>b"duration", 8)
                    if ret != 0: return ret
                    ret = self._pack(o.duration_ns)
                    if ret != 0: return ret

                    if has_span_type:
                        ret = pack_bytes(&self.pk, <char *>b"type", 4)
                        if ret != 0: return ret
                        ret = self._pack(o.span_type)
                        if ret != 0: return ret

                    if has_meta:
                        ret = pack_bytes(&self.pk, <char *>b"meta", 4)
                        if ret != 0: return ret
                        ret = self._pack(o.meta)
                        if ret != 0: return ret

                    if has_metrics:
                        ret = pack_bytes(&self.pk, <char *>b"metrics", 7)
                        if ret != 0: return ret
                        ret = self._pack(o.metrics)
                        if ret != 0: return ret
            else:
                PyErr_Format(TypeError, b"can not serialize '%.200s' object", Py_TYPE(o).tp_name)
            return ret

    cpdef pack(self, object obj):
        cdef int ret
        try:
            ret = self._pack(obj)
        except:
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

    def getbuffer(self):
        """Return view of internal buffer."""
        return buff_to_buff(self.pk.buf, self.pk.length)


cdef class MsgpackEncoder(object):
    content_type = "application/msgpack"

    cpdef _decode(self, data):
        import msgpack
        if msgpack.version[:2] < (0, 6):
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=True)

    cpdef encode_trace(self, list trace):
        return Packer().pack(trace)

    cpdef encode_traces(self, traces):
        return Packer().pack(traces)

    cpdef join_encoded(self, objs):
        """Join a list of encoded objects together as a msgpack array"""
        cdef Py_ssize_t count
        buf = b''.join(objs)

        count = len(objs)
        if count <= 0xf:
            return struct.pack("B", 0x90 + count) + buf
        elif count <= 0xffff:
            return struct.pack(">BH", 0xdc, count) + buf
        else:
            return struct.pack(">BI", 0xdd, count) + buf
