from cpython cimport *
from cpython.bytearray cimport PyByteArray_Check
import struct


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
        astream.write(packer.pack_trace(trace))
        astream.write(packer.pack_traces(traces))

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

    cdef inline object _flush_buffer(self):
        buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
        # Reset the buffer.
        self.pk.length = 0
        return buf

    cdef inline int _pack_number(self, object n):
        if n is None:
            return msgpack_pack_nil(&self.pk)

        if PyLong_Check(n):
            # PyInt_Check(long) is True for Python 3.
            # So we should test long before int.
            try:
                if n > 0:
                    return msgpack_pack_unsigned_long_long(&self.pk, <unsigned long long> n)
                return msgpack_pack_long_long(&self.pk, <long long> n)
            except OverflowError as oe:
                if n is not self._default:
                    return self._pack_number(self._default)
                raise OverflowError("Integer value out of range")

        elif PyInt_Check(n):
            return msgpack_pack_long(&self.pk, <long> n)

        elif PyFloat_Check(n):
            return msgpack_pack_double(&self.pk, <double> n)

        raise TypeError("Unhandled numeric type: %r" % type(n))

    cdef inline int _pack_text(self, object text):
        cdef Py_ssize_t L
        cdef int ret

        if text is None:
            return msgpack_pack_nil(&self.pk)

        if PyBytesLike_Check(text):
            L = len(text)
            if L > ITEM_LIMIT:
                PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(text).tp_name)
            ret = msgpack_pack_raw(&self.pk, L)
            if ret == 0:
                ret = msgpack_pack_raw_body(&self.pk, <char *> text, L)
            return ret

        if PyUnicode_Check(text):
            if self.encoding == NULL:
                ret = msgpack_pack_unicode(&self.pk, text, ITEM_LIMIT)
                if ret == -2:
                    raise ValueError("unicode string is too large")
            else:
                text = PyUnicode_AsEncodedString(text, self.encoding, self.unicode_errors)
                L = len(text)
                if L > ITEM_LIMIT:
                    raise ValueError("unicode string is too large")
                ret = msgpack_pack_raw(&self.pk, L)
                if ret == 0:
                    ret = msgpack_pack_raw_body(&self.pk, <char *> text, L)
            return ret

        raise TypeError("Unhandled text type: %r" % type(text))

    cdef inline int _pack_meta(self, object meta):
        cdef Py_ssize_t L
        cdef int ret
        cdef dict d

        if PyDict_CheckExact(meta):
            d = <dict> meta
            L = len(d)
            if L > ITEM_LIMIT:
                raise ValueError("dict is too large")

            ret = msgpack_pack_map(&self.pk, L)
            if ret == 0:
                for k, v in d.items():
                    ret = self._pack_text(k)
                    if ret != 0: break
                    ret = self._pack_text(v)
                    if ret != 0: break
            return ret

        raise TypeError("Unhandled meta type: %r" % type(meta))

    cdef inline int _pack_metrics(self, object metrics):
        cdef Py_ssize_t L
        cdef int ret
        cdef dict d

        if PyDict_CheckExact(metrics):
            d = <dict> metrics
            L = len(d)
            if L > ITEM_LIMIT:
                raise ValueError("dict is too large")

            ret = msgpack_pack_map(&self.pk, L)
            if ret == 0:
                for k, v in d.items():
                    ret = self._pack_text(k)
                    if ret != 0: break
                    ret = self._pack_number(v)
                    if ret != 0: break
            return ret

        raise TypeError("Unhandled metrics type: %r" % type(metrics))

    cdef inline int _pack_span(self, object span):
        cdef int ret
        cdef Py_ssize_t L
        cdef int has_span_type
        cdef int has_meta
        cdef int has_metrics

        has_span_type = <bint> (span.span_type is not None)
        has_meta = <bint> (len(span.meta) > 0)
        has_metrics = <bint> (len(span.metrics) > 0)

        L = 9 + has_span_type + has_meta + has_metrics

        ret = msgpack_pack_map(&self.pk, L)

        if ret == 0:
            ret = pack_bytes(&self.pk, <char *> b"trace_id", 8)
            if ret != 0: return ret
            ret = self._pack_number(span.trace_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"parent_id", 9)
            if ret != 0: return ret
            ret = self._pack_number(span.parent_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"span_id", 7)
            if ret != 0: return ret
            ret = self._pack_number(span.span_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"service", 7)
            if ret != 0: return ret
            ret = self._pack_text(span.service)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"resource", 8)
            if ret != 0: return ret
            ret = self._pack_text(span.resource)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"name", 4)
            if ret != 0: return ret
            ret = self._pack_text(span.name)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"error", 5)
            if ret != 0: return ret
            ret = msgpack_pack_long(&self.pk, <long> (1 if span.error else 0))
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"start", 5)
            if ret != 0: return ret
            ret = self._pack_number(span.start_ns)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"duration", 8)
            if ret != 0: return ret
            ret = self._pack_number(span.duration_ns)
            if ret != 0: return ret

            if has_span_type:
                ret = pack_bytes(&self.pk, <char *> b"type", 4)
                if ret != 0: return ret
                ret = self._pack_text(span.span_type)
                if ret != 0: return ret

            if has_meta:
                ret = pack_bytes(&self.pk, <char *> b"meta", 4)
                if ret != 0: return ret
                ret = self._pack_meta(span.meta)
                if ret != 0: return ret

            if has_metrics:
                ret = pack_bytes(&self.pk, <char *> b"metrics", 7)
                if ret != 0: return ret
                ret = self._pack_metrics(span.metrics)
                if ret != 0: return ret

        return ret

    cdef inline int _pack_trace(self, list trace):
        cdef int ret
        cdef Py_ssize_t L

        L = len(trace)
        if L > ITEM_LIMIT:
            raise ValueError("list is too large")

        ret = msgpack_pack_array(&self.pk, L)
        if ret != 0: raise RuntimeError("Couldn't pack trace")

        for span in trace:
            ret = self._pack_span(span)
            if ret != 0: raise RuntimeError("Couldn't pack span")
        return ret

    cpdef pack_trace(self, list trace):
        cdef int ret

        try:
            ret = self._pack_trace(trace)
        except:
            self.pk.length = 0
            raise
        if ret:  # should not happen.
            raise RuntimeError("internal error")

        return self._flush_buffer()

    cpdef pack_traces(self, list traces):
        cdef int ret
        cdef Py_ssize_t L

        L = len(traces)
        if L > ITEM_LIMIT:
            raise ValueError("list is too large")

        try:
            ret = msgpack_pack_array(&self.pk, L)
            if ret != 0: raise RuntimeError("Couldn't pack traces")

            for trace in traces:
                ret = self._pack_trace(trace)
                if ret != 0: raise RuntimeError("Couldn't pack trace")
        except:
            self.pk.length = 0
            raise
        if ret:  # should not happen.
            raise RuntimeError("internal error")

        return self._flush_buffer()

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
        return Packer().pack_trace(trace)

    cpdef encode_traces(self, list traces):
        return Packer().pack_traces(traces)

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
