# cython: boundscheck=False, wraparound=False, nonecheck=False

from cpython cimport *
from cpython.bytearray cimport PyByteArray_Check, PyByteArray_CheckExact

from ddtrace import Span

cdef extern from "Python.h":
    int PyMemoryView_Check(object obj)
    char* PyUnicode_AsUTF8AndSize(object obj, Py_ssize_t *l) except NULL


cdef extern from "pack.h":
    struct msgpack_packer:
        char* buf
        size_t length
        size_t buf_size
        bint use_bin_type

    int msgpack_pack_int(msgpack_packer* pk, int d)
    int msgpack_pack_nil(msgpack_packer* pk)
    int msgpack_pack_true(msgpack_packer* pk)
    int msgpack_pack_false(msgpack_packer* pk)
    int msgpack_pack_long(msgpack_packer* pk, long d)
    int msgpack_pack_long_long(msgpack_packer* pk, long long d)
    int msgpack_pack_unsigned_long_long(msgpack_packer* pk, unsigned long long d)
    int msgpack_pack_float(msgpack_packer* pk, float d)
    int msgpack_pack_double(msgpack_packer* pk, double d)
    int msgpack_pack_array(msgpack_packer* pk, size_t l)
    int msgpack_pack_map(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw(msgpack_packer* pk, size_t l)
    int msgpack_pack_bin(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw_body(msgpack_packer* pk, char* body, size_t l)
    int msgpack_pack_unicode(msgpack_packer* pk, object o, long long limit)

cdef extern from "buff_converter.h":
    object buff_to_buff(char *, Py_ssize_t)

cdef int DEFAULT_RECURSE_LIMIT=511
cdef long long ITEM_LIMIT = (2**32)-1


cdef inline int PyBytesLike_Check(object o):
    return PyBytes_Check(o) or PyByteArray_Check(o)


cdef inline int PyBytesLike_CheckExact(object o):
    return PyBytes_CheckExact(o) or PyByteArray_CheckExact(o)


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

    :param bool use_single_float:
        Use single precision float type for float. (default: False)

    :param bool autoreset:
        Reset buffer after each pack and return its content as `bytes`. (default: True).
        If set this to false, use `bytes()` to get content and `.reset()` to clear buffer.

    :param bool use_bin_type:
        Use bin type introduced in msgpack spec 2.0 for bytes.
        It also enables str8 type for unicode.
        Current default value is false, but it will be changed to true
        in future version.  You should specify it explicitly.

    :param bool strict_types:
        If set to true, types will be checked to be exact. Derived classes
        from serializeable types will not be serialized and will be
        treated as unsupported type and forwarded to default.
        Additionally tuples will not be serialized as lists.
        This is useful when trying to implement accurate serialization
        for python types.
    """
    cdef msgpack_packer pk
    cdef object _default
    cdef object _berrors
    cdef const char *encoding
    cdef const char *unicode_errors
    cdef bool use_float
    cdef bint autoreset

    def __cinit__(self):
        cdef int buf_size = 2**23 # 1024*1024
        self.pk.buf = <char*> PyMem_Malloc(buf_size)
        if self.pk.buf == NULL:
            raise MemoryError("Unable to allocate internal buffer.")
        self.pk.buf_size = buf_size
        self.pk.length = 0

    def __init__(self, default=None,
                 bint use_single_float=False, bint autoreset=True, bint use_bin_type=False):
        self.use_float = use_single_float
        self.autoreset = autoreset
        self.pk.use_bin_type = use_bin_type
        if default is not None:
            if not PyCallable_Check(default):
                raise TypeError("default must be a callable.")
        self._default = default

        if PY_MAJOR_VERSION < 3:
            self.encoding = 'utf-8'
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

        while True:
            if o is None:
                ret = msgpack_pack_nil(&self.pk)
            # elif PyBool_Check(o) if strict_types else isinstance(o, bool):
            #     if o:
            #         ret = msgpack_pack_true(&self.pk)
            #     else:
            #         ret = msgpack_pack_false(&self.pk)
            elif PyLong_CheckExact(o):
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
            elif PyInt_CheckExact(o):
                longval = o
                ret = msgpack_pack_long(&self.pk, longval)
            elif PyFloat_CheckExact(o):
                if self.use_float:
                   fval = o
                   ret = msgpack_pack_float(&self.pk, fval)
                else:
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
            elif PyUnicode_CheckExact(o):  #  if strict_types else PyUnicode_Check(o):
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
            elif PyList_CheckExact(o):  # if strict_types else (PyTuple_Check(o) or PyList_Check(o)):
                # List of traces
                L = len(o)
                if L > ITEM_LIMIT:
                    raise ValueError("list is too large")

                ret = msgpack_pack_array(&self.pk, L)
                if ret != 0:
                    break

                for trace in o:
                    L = len(trace)
                    if L > ITEM_LIMIT:
                        raise ValueError("list is too large")

                    ret = msgpack_pack_array(&self.pk, L)
                    if ret == 0:
                        for span in trace:
                            # ret = self._pack_span(span)
                            ret = self._pack(span)
                            if ret != 0: break
            elif type(o) is Span:
                L = 12
                if L > ITEM_LIMIT:
                    raise ValueError("list is too large")

                ret = msgpack_pack_map(&self.pk, L)

                if ret == 0:
                    ret = self._pack_bytes(b"trace_id")
                    if ret != 0: return ret
                    ret = self._pack(o.trace_id)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"parent_id")
                    if ret != 0: return ret
                    ret = self._pack(o.parent_id)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"span_id")
                    if ret != 0: return ret
                    ret = self._pack(o.span_id)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"service")
                    if ret != 0: return ret
                    ret = self._pack(o.service)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"resource")
                    if ret != 0: return ret
                    ret = self._pack(o.resource)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"name")
                    if ret != 0: return ret
                    ret = self._pack(o.name)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"error")
                    if ret != 0: return ret
                    ret = self._pack(1 if o.error else 0)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"start")
                    if ret != 0: return ret
                    ret = self._pack(o.start_ns)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"duration")
                    if ret != 0: return ret
                    ret = self._pack(o.duration_ns)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"type")
                    if ret != 0: return ret
                    ret = self._pack(o.span_type)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"meta")
                    if ret != 0: return ret
                    ret = self._pack(o.meta)
                    if ret != 0: return ret

                    ret = self._pack_bytes(b"metrics")
                    if ret != 0: return ret
                    ret = self._pack(o.metrics)
                    if ret != 0: return ret

            # elif PyMemoryView_Check(o):
            #     if PyObject_GetBuffer(o, &view, PyBUF_SIMPLE) != 0:
            #         raise ValueError("could not get buffer for memoryview")
            #     L = view.len
            #     if L > ITEM_LIMIT:
            #         PyBuffer_Release(&view);
            #         raise ValueError("memoryview is too large")
            #     ret = msgpack_pack_bin(&self.pk, L)
            #     if ret == 0:
            #         ret = msgpack_pack_raw_body(&self.pk, <char*>view.buf, L)
            #     PyBuffer_Release(&view);
            # elif not default_used and self._default:
            #     o = self._default(o)
            #     default_used = 1
            #     continue
            else:
                PyErr_Format(TypeError, b"can not serialize '%.200s' object", Py_TYPE(o).tp_name)
            return ret

    cdef int _pack_str_tags(self, dict tags):
        cdef int ret
        cdef int i
        cdef Py_ssize_t L
        L = len(tags)
        ret = msgpack_pack_map(&self.pk, L)
        for k, v in tags.items():
            ret = self._pack_str(k)
            if ret != 0: break
            ret = self._pack_str(v)
            if ret != 0: break
        return ret

    cdef int _pack_tags(self, dict tags):
        cdef int ret
        cdef int i
        cdef Py_ssize_t L
        L = len(tags)
        ret = msgpack_pack_map(&self.pk, L)
        for k, v in tags.items():
            ret = self._pack_str(k)
            if ret != 0: break
            ret = self._pack(v)
            if ret != 0: break
        return ret

    cdef int _pack_span(self, o):
        cdef int ret
        cdef Py_ssize_t L
        L = 12
        if L > ITEM_LIMIT:
            raise ValueError("list is too large")

        ret = msgpack_pack_map(&self.pk, L)
        if ret == 0:
            ret = self._pack_bytes(b"trace_id")
            if ret != 0: return ret
            ret = self._pack(o.trace_id)
            # ret = self._pack(12312312131121233)
            if ret != 0: return ret

            ret = self._pack_bytes(b"parent_id")
            if ret != 0: return ret
            ret = self._pack(o.parent_id)
            # ret = self._pack(12312312131123)
            if ret != 0: return ret

            ret = self._pack_bytes(b"span_id")
            if ret != 0: return ret
            ret = self._pack(o.span_id)
            # ret = self._pack(12312312131123)
            if ret != 0: return ret

            ret = self._pack_bytes(b"service")
            if ret != 0: return ret
            # ret = self._pack_str(o.service)
            ret = self._pack(o.service)
            # ret = self._pack_str("service")
            if ret != 0: return ret

            ret = self._pack_bytes(b"resource")
            if ret != 0: return ret
            # ret = self._pack_str(o.resource)
            ret = self._pack(o.resource)
            # ret = self._pack_str("resource")
            if ret != 0: return ret

            ret = self._pack_bytes(b"name")
            if ret != 0: return ret
            # ret = self._pack_str(o.name)
            ret = self._pack(o.name)
            # ret = self._pack_str("name")
            if ret != 0: return ret

            ret = self._pack_bytes(b"error")
            if ret != 0: return ret
            ret = self._pack(1 if o.error else 0)
            # ret = self._pack(0)
            if ret != 0: return ret

            ret = self._pack_bytes(b"start")
            if ret != 0: return ret
            ret = self._pack(o.start_ns)
            # ret = self._pack(12312312321)
            if ret != 0: return ret

            ret = self._pack_bytes(b"duration")
            if ret != 0: return ret
            ret = self._pack(o.duration_ns)
            # ret = self._pack(12312312321)
            if ret != 0: return ret

            ret = self._pack_bytes(b"type")
            if ret != 0: return ret
            ret = self._pack(o.span_type)
            # ret = self._pack_str("flaskjfdaklj")
            if ret != 0: return ret

            ret = self._pack_bytes(b"meta")
            if ret != 0: return ret
            # ret = self._pack_tags(o.meta)
            # ret = self._pack_str_tags(o.meta)
            ret = self._pack(o.meta)
            if ret != 0: return ret

            ret = self._pack_bytes(b"metrics")
            if ret != 0: return ret
            # ret = self._pack_tags(o.metrics)
            ret = self._pack(o.metrics)
            if ret != 0: return ret
        return ret

    cdef int _pack_bytes(self, char *rawval):
        cdef int ret
        cdef dict d
        cdef Py_ssize_t L
        L = len(rawval)
        if L > ITEM_LIMIT:
            PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(rawval).tp_name)
        ret = msgpack_pack_bin(&self.pk, L)
        if ret == 0:
            ret = msgpack_pack_raw_body(&self.pk, rawval, L)
        return ret

    cdef int _pack_str(self, o):
        cdef int ret
        cdef Py_ssize_t L
        cdef char* rawval

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
        if self.autoreset:
            buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
            self.pk.length = 0
            return buf

    def pack_array_header(self, long long size):
        if size > ITEM_LIMIT:
            raise ValueError
        cdef int ret = msgpack_pack_array(&self.pk, size)
        if ret == -1:
            raise MemoryError
        elif ret:  # should not happen
            raise TypeError
        if self.autoreset:
            buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
            self.pk.length = 0
            return buf

    def pack_map_header(self, long long size):
        if size > ITEM_LIMIT:
            raise ValueError
        cdef int ret = msgpack_pack_map(&self.pk, size)
        if ret == -1:
            raise MemoryError
        elif ret:  # should not happen
            raise TypeError
        if self.autoreset:
            buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
            self.pk.length = 0
            return buf

    def pack_map_pairs(self, object pairs):
        """
        Pack *pairs* as msgpack map type.

        *pairs* should be a sequence of pairs.
        (`len(pairs)` and `for k, v in pairs:` should be supported.)
        """
        cdef int ret = msgpack_pack_map(&self.pk, len(pairs))
        if ret == 0:
            for k, v in pairs:
                ret = self._pack(k)
                if ret != 0: break
                ret = self._pack(v)
                if ret != 0: break
        if ret == -1:
            raise MemoryError
        elif ret:  # should not happen
            raise TypeError
        if self.autoreset:
            buf = PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)
            self.pk.length = 0
            return buf

    def reset(self):
        """Reset internal buffer.

        This method is usaful only when autoreset=False.
        """
        self.pk.length = 0

    def bytes(self):
        """Return internal buffer contents as bytes object"""
        return PyBytes_FromStringAndSize(self.pk.buf, self.pk.length)

    def getbuffer(self):
        """Return view of internal buffer."""
        return buff_to_buff(self.pk.buf, self.pk.length)





cpdef span_to_dict(span):
    d = {
        b"trace_id": span.trace_id,
        b"parent_id": span.parent_id,
        b"span_id": span.span_id,
        b"service": span.service,
        b"resource": span.resource,
        b"name": span.name,
    }

    # error has to be an int
    d[b"error"] = 1 if span.error else 0

    if span.start_ns:
        d[b"start"] = span.start_ns

    if span.duration_ns:
        d[b"duration"] = span.duration_ns

    if span.meta:
        d[b"meta"] = span.meta

    if span.metrics:
        d[b"metrics"] = span.metrics

    if span.span_type:
        d[b"type"] = span.span_type

    return d



cdef class TraceMsgPackEncoder(object):
    @staticmethod
    def encode_trace(trace):
        # TODO this is broken atm
        return TraceMsgPackEncoder.encode_traces([trace])

    @staticmethod
    def encode_traces(traces):
        return Packer().pack(traces)

'''

from cpython cimport *
from libc.stdint cimport uint32_t, uint16_t
from libc.string cimport memcpy


cdef extern from "pack.h":
    struct msgpack_packer:
        char* buf
        size_t length
        size_t buf_size
        bint use_bin_type

    int msgpack_pack_int(msgpack_packer* pk, int d)
    int msgpack_pack_long(msgpack_packer* pk, long d)
    int msgpack_pack_long_long(msgpack_packer* pk, long long d)
    int msgpack_pack_unsigned_long_long(msgpack_packer* pk, unsigned long long d)
    int msgpack_pack_float(msgpack_packer* pk, float d)
    int msgpack_pack_double(msgpack_packer* pk, double d)
    int msgpack_pack_array(msgpack_packer* pk, size_t l)
    int msgpack_pack_map(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw(msgpack_packer* pk, size_t l)
    int msgpack_pack_bin(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw_body(msgpack_packer* pk, char* body, size_t l)
    int msgpack_pack_unicode(msgpack_packer* pk, object o, long long limit)


cdef size_t pack_numeric(msgpack_packer *pk, object v):
    # v can be either an int or a float
    cdef size_t len = 0
    cdef long longval

    if PyLong_Check(v):
        # PyInt_Check(long) is True for Python 3.
        # So we should test long before int.
        try:
            if v > 0:
                ullval = v
                ret = msgpack_pack_unsigned_long_long(&pk, ullval)
            else:
                llval = o
                ret = msgpack_pack_long_long(&pk, llval)
        except OverflowError as oe:
            raise OverflowError("Integer value out of range")
    elif PyInt_Check(v):
        longval = v
        ret = msgpack_pack_long(&pk, longval)
        buf[len] = 0xcf
        len += 1
    elif PyFloat_Check(v):
        buf[len] = 0xcf
        len += 1

    return len


cdef class TraceMsgPackEncoder(object):

    # TODO: cache common strings
    def __cinit__(self):
        # A very large span (20 tags, 15 character keys, 20 character values, 10 metrics) is, as of, a6df2696bc
        # around 33,560 bytes in memory and 960 bytes when serialized with msgpack.

        # The agent supports payload sizes up to 10MB. Let's be conservative and do 8MB.
        # This should be around 8,333 _large_ spans.
        pass

    def __dealloc__(self):
        pass
        # PyMem_Free(self.buf)
        # self.buf = NULL

    @staticmethod
    def encode_trace(trace):
        return TraceMsgPackEncoder.encode_traces([trace])

    @staticmethod
    def encode_traces(traces):
        cdef msgpack_packer pk
        cdef uint32_t i, j, ntraces, nspans
        cdef uint16_t nentries

        buf_size = 2**13  # TODO find "reasonable" number for this to minimize # allocations, memory overhead
        buf = <char *>PyMem_Malloc(buf_size)
        if buf == NULL:
            raise MemoryError("Could not allocate memory for trace")
        pk.buf_size = buf_size
        pk.length = 0

        ntraces = <uint32_t>len(traces)

        # First add the array header for the traces array
        # unsigned char buf[3];
        # buf[0] = 0xdc; _msgpack_store16(&buf[1], (uint16_t)n);
        # msgpack_pack_append_buffer(x, buf, 3);
        # Optimization: msgpack supports 3 different array sizes, 2^4-1, 2^16-1 and 2^32-1 since it's not
        # totally unreasonable that we could have over 2^16-1 traces, let's play it safe by picking the
        # largest.
        pk.buf[0] = 0xdc
        pk.buf_len += 1
        # TODO: we have to ensure we pack the correct endianness
        memcpy(&buf[pk.buf_len], &ntraces, 4)
        pk.buf_len += 4

        # Optimization: skip all the checking that Python does in a for..in loop
        # By typing i as uint32_t Cython will generate a C for loop
        for i in range(0, ntraces):
            trace = traces[i]
            nspans = <uint32_t>len(trace)

            # Add the trace header:
            pk.buf[pk.buf_len] = 0xdc
            pk.buf_len += 1
            memcpy(&pk.buf[pk.buf_len], &nspans, 4)
            pk.buf_len += 4

            for j in range(0, nspans):
                span = <uint32_t>trace[j]
                # Number of entries in the span map
                nentries = 0
                nentries += 7  # trace_id, parent_id, span_id, service, resource, name, error


                # Pack the span
                pk.buf[pk.buf_len] = 0xde
                pk.buf_len += 1

                # Save the map size offset in the buffer since we calculate how many entries on the fly
                map_len_offset = pk.buf_len


                memcpy(&pk.buf[map_len_offset], &nentries, 2)

        # Note that this will result in a call to PyBytes_FromString to create a PyObject
        # to be returned. This results in the buffer being copied entirely.
        # TODO? we could potentially return a pyobject directly to avoid this?
        r = PyBytes_FromStringAndSize(pk.buf, pk.buf_len)
        PyMem_Free(pk.buf)
        pk.buf = NULL
        return r

    @staticmethod
    def join_encoded(encoded_traces):
        pass

'''
