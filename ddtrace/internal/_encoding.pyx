from cpython cimport *
from cpython.bytearray cimport PyByteArray_Check
from libc cimport stdint
from libc.string cimport strlen
import threading


DEF MSGPACK_ARRAY_LENGTH_PREFIX_SIZE = 5


cdef extern from "Python.h":
    const char* PyUnicode_AsUTF8(object o)

cdef extern from "pack.h":
    struct msgpack_packer:
        char* buf
        size_t length
        size_t buf_size

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


cdef long long ITEM_LIMIT = (2**32)-1


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


cdef inline int PyBytesLike_Check(object o):
    return PyBytes_Check(o) or PyByteArray_Check(o)


cdef inline int array_prefix_size(int l):
    if l < 16:
        return 1
    elif l < (2<<16):
        return 3
    return MSGPACK_ARRAY_LENGTH_PREFIX_SIZE


cdef inline int pack_bytes(msgpack_packer *pk, char *bytes, Py_ssize_t l):
    cdef int ret

    ret = msgpack_pack_raw(pk, l)
    if ret == 0:
        ret = msgpack_pack_raw_body(pk, bytes, l)
    return ret


cdef inline int pack_number(msgpack_packer *pk, object n):
    if n is None:
        return msgpack_pack_nil(pk)

    if PyLong_Check(n):
        # PyInt_Check(long) is True for Python 3.
        # So we should test long before int.
        try:
            if n > 0:
                return msgpack_pack_unsigned_long_long(pk, <unsigned long long> n)
            return msgpack_pack_long_long(pk, <long long> n)
        except OverflowError as oe:
            raise OverflowError("Integer value out of range")

    if PyInt_Check(n):
        return msgpack_pack_long(pk, <long> n)

    if PyFloat_Check(n):
        return msgpack_pack_double(pk, <double> n)

    raise TypeError("Unhandled numeric type: %r" % type(n))


cdef inline int pack_text(msgpack_packer *pk, object text):
    cdef Py_ssize_t L
    cdef int ret

    if text is None:
        return msgpack_pack_nil(pk)

    if PyBytesLike_Check(text):
        L = len(text)
        if L > ITEM_LIMIT:
            PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(text).tp_name)
        ret = msgpack_pack_raw(pk, L)
        if ret == 0:
            ret = msgpack_pack_raw_body(pk, <char *> text, L)
        return ret

    if PyUnicode_Check(text):
        IF PY_MAJOR_VERSION >= 3:
            ret = msgpack_pack_unicode(pk, text, ITEM_LIMIT)
            if ret == -2:
                raise ValueError("unicode string is too large")
        ELSE:
            text = PyUnicode_AsEncodedString(text, "utf-8", NULL)
            L = len(text)
            if L > ITEM_LIMIT:
                raise ValueError("unicode string is too large")
            ret = msgpack_pack_raw(pk, L)
            if ret == 0:
                ret = msgpack_pack_raw_body(pk, <char *> text, L)
        return ret

    raise TypeError("Unhandled text type: %r" % type(text))


cdef class BufferedEncoder(object):
    content_type: str = None

    cdef public int max_size
    cdef public int max_item_size
    cdef object _lock

    def __cinit__(self, max_size, max_item_size):
        self.max_size = max_size
        self.max_item_size = max_item_size
        self._lock = threading.Lock()

    # ---- Abstract methods ----

    def put(self, item):
        raise NotImplementedError()

    def encode(self):
        raise NotImplementedError()


cdef class MsgpackEncoderBase(BufferedEncoder):
    content_type = "application/msgpack"

    cdef msgpack_packer pk
    cdef stdint.uint32_t _count

    def __cinit__(self, max_size, max_item_size):
        cdef int buf_size = 1024*1024
        self.pk.buf = <char*> PyMem_Malloc(buf_size)
        if self.pk.buf == NULL:
            raise MemoryError("Unable to allocate internal buffer.")

        self.max_size = max_size
        self.pk.buf_size = buf_size
        self.max_item_size = max_item_size if max_item_size < max_size else max_size
        self._lock = threading.Lock()
        self._reset_buffer()

    def __dealloc__(self):
        PyMem_Free(self.pk.buf)
        self.pk.buf = NULL

    def __len__(self):  # TODO: Use a better name?
        return self._count

    cpdef _decode(self, data):
        import msgpack
        if msgpack.version[:2] < (0, 6):
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=True)

    cdef _reset_buffer(self):
        self._count = 0
        self.pk.length = MSGPACK_ARRAY_LENGTH_PREFIX_SIZE  # Leave room for array length prefix

    cpdef encode(self):
        if not self._count:
            return None

        return self.flush()

    cdef inline int _update_array_len(self):
        """Update traces array size prefix"""
        cdef int offset = MSGPACK_ARRAY_LENGTH_PREFIX_SIZE - array_prefix_size(self._count)
        cdef int old_pos = self.pk.length

        with self._lock:
            self.pk.length = offset
            msgpack_pack_array(&self.pk, self._count)
            self.pk.length = old_pos
            return offset

    cpdef get_bytes(self):
        """Return internal buffer contents as bytes object"""
        cdef int offset = self._update_array_len()
        with self._lock:
            return PyBytes_FromStringAndSize(self.pk.buf + offset, self.pk.length - offset)

    cpdef char * get_buffer(self):
        """Return internal buffer."""
        return self.pk.buf + self._update_array_len()

    cdef inline int _pack_trace(self, list trace):
        cdef int ret
        cdef Py_ssize_t L
        cdef char *dd_origin = NULL

        L = len(trace)
        if L > ITEM_LIMIT:
            raise ValueError("list is too large")

        ret = msgpack_pack_array(&self.pk, L)
        if ret != 0: raise RuntimeError("Couldn't pack trace")

        if L > 0 and trace[0].context is not None and trace[0].context.dd_origin is not None:
            IF PY_MAJOR_VERSION >= 3:
                dd_origin = PyUnicode_AsUTF8(trace[0].context.dd_origin)
            ELSE:
                dd_origin = trace[0].context.dd_origin

        with self._lock:
            for span in trace:
                ret = self.pack_span(span, dd_origin)
                if ret != 0: raise RuntimeError("Couldn't pack span")

        return ret

    cpdef put(self, list trace):
        """Put a trace (i.e. a list of spans) in the buffer."""
        cdef int ret

        len_before = self.pk.length
        size_before = self.size
        try:
            ret = self._pack_trace(trace)
            if ret:  # should not happen.
                raise RuntimeError("internal error")

            # DEV: msgpack avoids buffer overflows by calling PyMem_Realloc so
            # we must check sizes manually.
            # TODO: We should probably ensure that the buffer size doesn't
            # grow arbitrarily because of the PyMem_Realloc and if it does then
            # free and reallocate with the appropriate size.
            if self.size - size_before > self.max_item_size:
                raise BufferItemTooLarge(self.size - size_before)

            if self.size > self.max_size:
                raise BufferFull(self.size - size_before)

            self._count += 1
        except:
            # rollback
            self.pk.length = len_before
            raise

    @property
    def size(self):
        """Return the size in bytes of the encoder buffer."""
        return self.pk.length + array_prefix_size(self._count) - MSGPACK_ARRAY_LENGTH_PREFIX_SIZE

    # ---- Abstract methods ----

    cpdef flush(self):
        raise NotImplementedError()

    cdef pack_span(self, object span, char *dd_origin):
        raise NotImplementedError()


cdef class MsgpackEncoder(MsgpackEncoderBase):
    cpdef flush(self):
        try:
            return self.get_bytes()
        finally:
            self._reset_buffer()

    cdef inline int _pack_meta(self, object meta, char *dd_origin):
        cdef Py_ssize_t L
        cdef int ret
        cdef dict d

        if PyDict_CheckExact(meta):
            d = <dict> meta
            L = len(d)
            if dd_origin is not NULL:
                L += 1
            if L > ITEM_LIMIT:
                raise ValueError("dict is too large")

            ret = msgpack_pack_map(&self.pk, L)
            if ret == 0:
                for k, v in d.items():
                    ret = pack_text(&self.pk, k)
                    if ret != 0: break
                    ret = pack_text(&self.pk, v)
                    if ret != 0: break
                if dd_origin is not NULL:
                    ret = pack_bytes(&self.pk, <char *> b"_dd.origin", 10)
                    if ret == 0:
                        ret = pack_bytes(&self.pk, dd_origin, strlen(dd_origin))
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
                    ret = pack_text(&self.pk, k)
                    if ret != 0: break
                    ret = pack_number(&self.pk, v)
                    if ret != 0: break
            return ret

        raise TypeError("Unhandled metrics type: %r" % type(metrics))

    cdef pack_span(self, object span, char *dd_origin):
        cdef int ret
        cdef Py_ssize_t L
        cdef int has_span_type
        cdef int has_meta
        cdef int has_metrics

        has_span_type = <bint> (span.span_type is not None)
        has_meta = <bint> (len(span.meta) > 0 or dd_origin is not NULL)
        has_metrics = <bint> (len(span.metrics) > 0)

        L = 9 + has_span_type + has_meta + has_metrics

        ret = msgpack_pack_map(&self.pk, L)

        if ret == 0:
            ret = pack_bytes(&self.pk, <char *> b"trace_id", 8)
            if ret != 0: return ret
            ret = pack_number(&self.pk, span.trace_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"parent_id", 9)
            if ret != 0: return ret
            ret = pack_number(&self.pk, span.parent_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"span_id", 7)
            if ret != 0: return ret
            ret = pack_number(&self.pk, span.span_id)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"service", 7)
            if ret != 0: return ret
            ret = pack_text(&self.pk, span.service)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"resource", 8)
            if ret != 0: return ret
            ret = pack_text(&self.pk, span.resource)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"name", 4)
            if ret != 0: return ret
            ret = pack_text(&self.pk, span.name)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"error", 5)
            if ret != 0: return ret
            ret = msgpack_pack_long(&self.pk, <long> (1 if span.error else 0))
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"start", 5)
            if ret != 0: return ret
            ret = pack_number(&self.pk, span.start_ns)
            if ret != 0: return ret

            ret = pack_bytes(&self.pk, <char *> b"duration", 8)
            if ret != 0: return ret
            ret = pack_number(&self.pk, span.duration_ns)
            if ret != 0: return ret

            if has_span_type:
                ret = pack_bytes(&self.pk, <char *> b"type", 4)
                if ret != 0: return ret
                ret = pack_text(&self.pk, span.span_type)
                if ret != 0: return ret

            if has_meta:
                ret = pack_bytes(&self.pk, <char *> b"meta", 4)
                if ret != 0: return ret
                ret = self._pack_meta(span.meta, dd_origin)
                if ret != 0: return ret

            if has_metrics:
                ret = pack_bytes(&self.pk, <char *> b"metrics", 7)
                if ret != 0: return ret
                ret = self._pack_metrics(span.metrics)
                if ret != 0: return ret

        return ret
