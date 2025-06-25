from cpython cimport *
from cpython.bytearray cimport PyByteArray_CheckExact
from libc cimport stdint
from libc.string cimport strlen

from json import dumps as json_dumps
import threading
from json import dumps as json_dumps

from ._utils cimport PyBytesLike_Check


# Do not use an absolute import here Cython<3.0.0 will
#   import `ddtrace.internal.constants` instead when this
#   package is installed in editable mode
# See the following for more details
#   https://github.com/DataDog/dd-trace-py/pull/4085
#   https://github.com/brettlangdon/shadow-import-issue
# DEV: This only occurs because there is a `constants.py` module
#   in both `ddtrace` and `ddtrace.internal`

from ..constants import _ORIGIN_KEY as ORIGIN_KEY
from .constants import SPAN_LINKS_KEY
from .constants import SPAN_EVENTS_KEY
from .constants import MAX_UINT_64BITS
from .._trace._limits import MAX_SPAN_META_VALUE_LEN
from .._trace._limits import TRUNCATED_SPAN_ATTRIBUTE_LEN
from ..settings._agent import config as agent_config


DEF MSGPACK_ARRAY_LENGTH_PREFIX_SIZE = 5
DEF MSGPACK_STRING_TABLE_LENGTH_PREFIX_SIZE = 6


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
    int msgpack_pack_bin(msgpack_packer* pk, size_t l)
    int msgpack_pack_raw_body(msgpack_packer* pk, char* body, size_t l)
    int msgpack_pack_unicode(msgpack_packer* pk, object o, long long limit)
    int msgpack_pack_uint8(msgpack_packer* pk, stdint.uint8_t d)
    int msgpack_pack_uint32(msgpack_packer* pk, stdint.uint32_t d)
    int msgpack_pack_uint64(msgpack_packer* pk, stdint.uint64_t d)
    int msgpack_pack_int32(msgpack_packer* pk, stdint.int32_t d)
    int msgpack_pack_int64(msgpack_packer* pk, stdint.int64_t d)
    int msgpack_pack_true(msgpack_packer* pk)
    int msgpack_pack_false(msgpack_packer* pk)


cdef long long ITEM_LIMIT = (2**32)-1


cdef inline int PyBytesLike_CheckExact(object o):
    return PyBytes_CheckExact(o) or PyByteArray_CheckExact(o)


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


cdef inline const char * string_to_buff(str s):
    IF PY_MAJOR_VERSION >= 3:
        return PyUnicode_AsUTF8(s)
    ELSE:
        return <const char *> s


# This is a borrowed reference but should be fine as we don't expect ORIGIN_KEY
# to get GC'd.
cdef const char * _ORIGIN_KEY = string_to_buff(ORIGIN_KEY)
cdef size_t _ORIGIN_KEY_LEN = <size_t> len(ORIGIN_KEY)


cdef inline int array_prefix_size(stdint.uint32_t l):
    if l < 16:
        return 1
    elif l < (2<<16):
        return 3
    return MSGPACK_ARRAY_LENGTH_PREFIX_SIZE

cdef inline object truncate_string(object string):
    if string and len(string) > MAX_SPAN_META_VALUE_LEN:
        return string[:TRUNCATED_SPAN_ATTRIBUTE_LEN - 14] + "<truncated>..."
    return string

cdef inline int pack_bytes(msgpack_packer *pk, char *bs, Py_ssize_t l):
    cdef int ret

    ret = msgpack_pack_raw(pk, l)
    if ret == 0:
        ret = msgpack_pack_raw_body(pk, bs, l)
    return ret

cdef inline int pack_bool(msgpack_packer *pk, bint n) except? -1:
    if n:
        return msgpack_pack_true(pk)
    return msgpack_pack_false(pk)

cdef inline int pack_number(msgpack_packer *pk, object n) except? -1:
    if n is None:
        return msgpack_pack_nil(pk)

    if PyLong_Check(n):
        try:
            if n > 0:
                return msgpack_pack_unsigned_long_long(pk, <unsigned long long> n)
            return msgpack_pack_long_long(pk, <long long> n)
        except OverflowError as oe:
            raise OverflowError("Integer value out of range")

    if PyFloat_Check(n):
        return msgpack_pack_double(pk, <double> n)

    raise TypeError("Unhandled numeric type: %r" % type(n))


cdef inline int pack_text(msgpack_packer *pk, object text) except? -1:
    cdef Py_ssize_t L
    cdef int ret

    if text is None:
        return msgpack_pack_nil(pk)

    if PyBytesLike_Check(text):
        L = len(text)
        if L > MAX_SPAN_META_VALUE_LEN:
            PyErr_Format(ValueError, b"%.200s object is too large", Py_TYPE(text).tp_name)
            text = truncate_string(text)
            L = len(text)
        ret = msgpack_pack_raw(pk, L)
        if ret == 0:
            ret = msgpack_pack_raw_body(pk, <char *> text, L)
        return ret

    if PyUnicode_Check(text):
        if len(text) > MAX_SPAN_META_VALUE_LEN:
            text = truncate_string(text)
        IF PY_MAJOR_VERSION >= 3:
            ret = msgpack_pack_unicode(pk, text, ITEM_LIMIT)
            if ret == -2:
                raise ValueError("unicode string is too large")
        ELSE:
            text = PyUnicode_AsEncodedString(text, "utf-8", NULL)
            L = len(text)
            if L > MAX_SPAN_META_VALUE_LEN:
                raise ValueError("unicode string is too large")
            ret = msgpack_pack_raw(pk, L)
            if ret == 0:
                ret = msgpack_pack_raw_body(pk, <char *> text, L)

        return ret

    raise TypeError("Unhandled text type: %r" % type(text))

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


cdef class MsgpackStringTable(StringTable):
    cdef msgpack_packer pk
    cdef int max_size
    cdef int _sp_len
    cdef stdint.uint32_t _sp_id
    cdef object _lock
    cdef size_t _reset_size

    def __init__(self, max_size):
        self.pk.buf_size = min(max_size, 1 << 20)
        self.pk.buf = <char*> PyMem_Malloc(self.pk.buf_size)
        if self.pk.buf == NULL:
            raise MemoryError("Unable to allocate internal buffer.")
        self.max_size = max_size
        self.pk.length = MSGPACK_STRING_TABLE_LENGTH_PREFIX_SIZE
        self._sp_len = 0
        self._lock = threading.RLock()
        super(MsgpackStringTable, self).__init__()

        self.index(ORIGIN_KEY)
        self._reset_size = self.pk.length

    def __dealloc__(self):
        PyMem_Free(self.pk.buf)
        self.pk.buf = NULL

    cdef insert(self, object string):
        cdef int ret

        # Before inserting, truncate the string if it is greater than MAX_SPAN_META_VALUE_LEN
        string = truncate_string(string)

        if self.pk.length + len(string) > self.max_size:
            raise ValueError(
                "Cannot insert '%s': string table is full (current size: %d, size after insert: %d, max size: %d)." % (
                    string, self.pk.length, (self.pk.length + len(string)), self.max_size
                )
            )

        ret = pack_text(&self.pk, string)
        if ret != 0:
            raise RuntimeError("Failed to add string to msgpack string table")

    cdef savepoint(self):
        self._sp_len = self.pk.length
        self._sp_id = self._next_id

    cdef rollback(self):
        if self._sp_len > 0:
            self.pk.length = self._sp_len
            self._next_id = self._sp_id

        # After rolling back the string table next_id we must remove all stale string -> _id pairs
        # This will resolve two classes of encoding errors:
        #  - multiple strings referencing the same string table index. In this scenario
        #    two different strings in the encoded trace could be serialized with the same _id. In this scenario
        #    two different strings could reference one string in the encoded trace (string swapping).
        # - when the string table references an index of the string table that is not serialized. The encoded
        #    trace can not be decoded without accessing an invalid index. In this scenario the agent will
        #    return a 400 status code.
        self._table = {s: idx for s, idx in self._table.items() if idx < self._next_id}

    cdef get_bytes(self):
        cdef int ret
        cdef stdint.uint32_t table_size
        cdef int offset
        cdef int old_pos
        with self._lock:
            table_size = self._next_id
            offset = MSGPACK_STRING_TABLE_LENGTH_PREFIX_SIZE - array_prefix_size(table_size)
            old_pos = self.pk.length

            # Update table size prefix
            self.pk.length = offset
            ret = msgpack_pack_array(&self.pk, table_size)
            if ret:
                return None
            # Add root array size prefix
            self.pk.length = offset = offset - 1
            ret = msgpack_pack_array(&self.pk, 2)
            if ret:
                return None
            self.pk.length = old_pos

            return PyBytes_FromStringAndSize(self.pk.buf + offset, self.pk.length - offset)

    @property
    def size(self):
        with self._lock:
            return self.pk.length - MSGPACK_ARRAY_LENGTH_PREFIX_SIZE + array_prefix_size(self._next_id)

    cdef append_raw(self, long src, Py_ssize_t size):
        cdef int res
        with self._lock:
            if self.size + size > self.max_size:
                raise BufferFull(
                    "Cannot append raw bytes: string table is full (current size: %d, max size: %d)." % (
                        self.size, self.max_size
                    )
                )
            res = msgpack_pack_raw_body(&self.pk, <char *>PyLong_AsLong(src), size)
            if res != 0:
                raise RuntimeError("Failed to append raw bytes to msgpack string table")

    cdef reset(self):
        StringTable.reset(self)
        assert self._next_id == 1

        PyDict_SetItem(self._table, ORIGIN_KEY, 1)
        self._next_id = 2
        self.pk.length = self._reset_size
        self._sp_len = 0

    cpdef flush(self):
        with self._lock:
            try:
                return self.get_bytes()
            finally:
                self.reset()


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


cdef class ListBufferedEncoder(BufferedEncoder):
    cdef list _buffer
    cdef Py_ssize_t _size

    def __cinit__(self, size_t max_size, size_t max_item_size):
        self._buffer = []
        self._size = 0

    def __len__(self):
        return len(self._buffer)

    @property
    def size(self):
        with self._lock:
            return self._size

    cpdef put(self, item):
        """Put an item to be serialized in the buffer."""
        cdef int item_len

        encoded_item = self.encode_item(item)
        item_len = len(encoded_item)

        if item_len > self.max_item_size or item_len > self.max_size:
            raise BufferItemTooLarge(item_len)

        with self._lock:
            if self._size + item_len <= self.max_size:
                self._buffer.append(encoded_item)
                self._size += item_len
            else:
                raise BufferFull(item_len)

    cpdef get(self):
        """Get a copy of the buffer and clear it."""
        with self._lock:
            try:
                return list(self._buffer)
            finally:
                self._buffer[:] = []
                self._size = 0

    def encode_item(self, item):
        raise NotImplementedError()


cdef class MsgpackEncoderBase(BufferedEncoder):
    content_type = "application/msgpack"

    cdef msgpack_packer pk
    cdef stdint.uint32_t _count

    def __cinit__(self, size_t max_size, size_t max_item_size):
        cdef int buf_size = 1024*1024
        self.pk.buf = <char*> PyMem_Malloc(buf_size)
        if self.pk.buf == NULL:
            raise MemoryError("Unable to allocate internal buffer.")

        self.max_size = max_size
        self.pk.buf_size = buf_size
        self.max_item_size = max_item_size if max_item_size < max_size else max_size
        self._lock = threading.RLock()
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
        with self._lock:
            if not self._count:
                return None, 0

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

    cdef get_bytes(self):
        """Return internal buffer contents as bytes object"""
        cdef int offset = self._update_array_len()
        with self._lock:
            return PyBytes_FromStringAndSize(self.pk.buf + offset, self.pk.length - offset)

    cdef char * get_buffer(self):
        """Return internal buffer."""
        return self.pk.buf + self._update_array_len()

    cdef void * get_dd_origin_ref(self, str dd_origin):
        raise NotImplementedError()

    cdef inline int _pack_trace(self, list trace) except? -1:
        cdef int ret
        cdef Py_ssize_t L
        cdef void * dd_origin = NULL

        L = len(trace)
        if L > ITEM_LIMIT:
            raise ValueError("list is too large")

        ret = msgpack_pack_array(&self.pk, L)
        if ret != 0:
            raise RuntimeError("Couldn't pack trace")

        if L > 0 and trace[0].context is not None and trace[0].context.dd_origin is not None:
            dd_origin = self.get_dd_origin_ref(trace[0].context.dd_origin)

        for span in trace:
            try:
                ret = self.pack_span(span, dd_origin)
            except Exception as e:
                raise RuntimeError("failed to pack span: {!r}. Exception: {}".format(span, e))

            # No exception was raised, but we got an error code from msgpack
            if ret != 0:
                raise RuntimeError("couldn't pack span: {!r}".format(span))

        return ret

    cpdef put(self, list trace):
        """Put a trace (i.e. a list of spans) in the buffer."""
        cdef int ret

        with self._lock:
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
            except Exception:
                # rollback
                self.pk.length = len_before
                raise

    @property
    def size(self):
        """Return the size in bytes of the encoder buffer."""
        with self._lock:
            return self.pk.length + array_prefix_size(self._count) - MSGPACK_ARRAY_LENGTH_PREFIX_SIZE

    # ---- Abstract methods ----

    cpdef flush(self):
        raise NotImplementedError()

    cdef int pack_span(self, object span, void *dd_origin) except? -1:
        raise NotImplementedError()


cdef class MsgpackEncoderV04(MsgpackEncoderBase):
    cdef bint top_level_span_event_encoding

    def __cinit__(self, size_t max_size, size_t max_item_size):
        self.top_level_span_event_encoding = agent_config.trace_native_span_events

    cpdef flush(self):
        with self._lock:
            try:
                return self.get_bytes(), len(self)
            finally:
                self._reset_buffer()

    cdef void * get_dd_origin_ref(self, str dd_origin):
        return string_to_buff(dd_origin)

    cdef inline int _pack_links(self, list span_links):
        ret = msgpack_pack_array(&self.pk, len(span_links))
        if ret != 0:
            return ret

        for link in span_links:
            # SpanLink.to_dict() returns all serializable span link fields
            # v0.4 encoding is disabled by default. SpanLinks.to_dict() is optimized for the v0.5 format.
            d = link.to_dict()
            # Encode 128 bit trace ids usings two 64bit integers
            tid = int(d["trace_id"][:16], 16)
            if tid > 0:
                d["trace_id_high"] = tid
            d["trace_id"] = int(d["trace_id"][16:], 16)
            # span id should be uint64 in v0.4 (it is hex in v0.5)
            d["span_id"] = int(d["span_id"], 16)
            if "flags" in d:
                # If traceflags set, the high bit (bit 31) should be set to 1 (uint32).
                # This helps us distinguish between when the sample decision is zero or not set
                d["flags"] = d["flags"] | (1 << 31)

            ret = msgpack_pack_map(&self.pk, len(d))
            if ret != 0:
                return ret

            for k, v in d.items():
                # pack the name of a span link field (ex: trace_id, span_id, flags, ...)
                ret = pack_text(&self.pk, k)
                if ret != 0:
                    return ret
                # pack the value of a span link field (values can be number, string or dict)
                if isinstance(v, (int, float)):
                    ret = pack_number(&self.pk, v)
                elif isinstance(v, str):
                    ret = pack_text(&self.pk, v)
                elif k == "attributes":
                    # span links can contain attributes, this is analougous to span tags
                    # attributes are serialized as a nested dict with string keys and values
                    attributes = v.items()
                    ret = msgpack_pack_map(&self.pk, len(attributes))
                    for attr_k, attr_v in attributes:
                        ret = pack_text(&self.pk, attr_k)
                        if ret != 0:
                            return ret
                        ret = pack_text(&self.pk, attr_v)
                        if ret != 0:
                            return ret
                else:
                    raise ValueError(f"Failed to encode SpanLink. {k}={v} contains an unsupported type, {type(v)}")
                if ret != 0:
                    return ret
        return 0

    cdef inline int _pack_span_events(self, list span_events) except? -1:
        cdef int ret
        cdef int L
        cdef str attr_k
        cdef object attr_v
        cdef object event
        ret = msgpack_pack_array(&self.pk, len(span_events))
        if ret != 0:
            return ret

        for event in span_events:
            L = 2 + bool(event.attributes)
            ret = msgpack_pack_map(&self.pk, L)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char*> b"name", 4)
            if ret != 0:
                return ret

            ret = pack_text(&self.pk, event.name)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char*> b"time_unix_nano", 14)
            if ret != 0:
                return ret

            ret = pack_number(&self.pk, event.time_unix_nano)
            if ret != 0:
                return ret

            if event.attributes:
                ret = pack_bytes(&self.pk, <char*> b"attributes", 10)
                if ret != 0:
                    return ret

                ret = msgpack_pack_map(&self.pk, len(event.attributes))
                if ret != 0:
                    return ret

                for attr_k, attr_v in event.attributes.items():
                    ret = pack_text(&self.pk, attr_k)
                    if ret != 0:
                        return ret

                    ret = self.pack_span_event_attributes(attr_v)
                    if ret != 0:
                        return ret
        return ret

    cdef inline int _pack_meta(self, object meta, char *dd_origin, str span_events) except? -1:
        cdef Py_ssize_t L
        cdef int ret
        cdef dict d

        if PyDict_CheckExact(meta):
            d = <dict> meta
            L = len(d) + (dd_origin is not NULL) + (len(span_events) > 0)
            if L > ITEM_LIMIT:
                raise ValueError("dict is too large")

            ret = msgpack_pack_map(&self.pk, L)
            if ret == 0:
                for k, v in d.items():
                    ret = pack_text(&self.pk, k)
                    if ret != 0:
                        break
                    ret = pack_text(&self.pk, v)
                    if ret != 0:
                        break
                if dd_origin is not NULL:
                    ret = pack_bytes(&self.pk, _ORIGIN_KEY, _ORIGIN_KEY_LEN)
                    if ret == 0:
                        ret = pack_bytes(&self.pk, dd_origin, strlen(dd_origin))
                    if ret != 0:
                        return ret
                if span_events:
                    ret = pack_text(&self.pk, SPAN_EVENTS_KEY)
                    if ret == 0:
                        ret = pack_text(&self.pk, span_events)
            return ret

        raise TypeError("Unhandled meta type: %r" % type(meta))

    cdef inline int _pack_metrics(self, object metrics) except? -1:
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
                    if ret != 0:
                        break
                    ret = pack_number(&self.pk, v)
                    if ret != 0:
                        break
            return ret

        raise TypeError("Unhandled metrics type: %r" % type(metrics))

    cdef int pack_span(self, object span, void *dd_origin) except? -1:
        cdef int ret
        cdef Py_ssize_t L
        cdef int has_span_type
        cdef int has_meta
        cdef int has_metrics

        has_error = <bint> (span.error != 0)
        has_span_type = <bint> (span.span_type is not None)
        has_span_events = <bint> (len(span._events) > 0)
        has_metrics = <bint> (len(span._metrics) > 0)
        has_parent_id = <bint> (span.parent_id is not None)
        has_links = <bint> (len(span._links) > 0)
        has_meta_struct = <bint> (len(span._meta_struct) > 0)
        has_meta = <bint> (
            len(span._meta) > 0
            or dd_origin is not NULL
            or (not self.top_level_span_event_encoding and has_span_events)
        )

        # do not include in meta
        L = 7 + has_span_type + has_meta + has_metrics + has_error + has_parent_id + has_links + has_meta_struct
        if self.top_level_span_event_encoding:
            L += has_span_events

        ret = msgpack_pack_map(&self.pk, L)

        if ret == 0:
            ret = pack_bytes(&self.pk, <char *> b"trace_id", 8)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, span._trace_id_64bits)
            if ret != 0:
                return ret

            if has_parent_id:
                ret = pack_bytes(&self.pk, <char *> b"parent_id", 9)
                if ret != 0:
                    return ret
                ret = pack_number(&self.pk, span.parent_id)
                if ret != 0:
                    return ret

            ret = pack_bytes(&self.pk, <char *> b"span_id", 7)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, span.span_id)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char *> b"service", 7)
            if ret != 0:
                return ret
            ret = pack_text(&self.pk, span.service)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char *> b"resource", 8)
            if ret != 0:
                return ret
            ret = pack_text(&self.pk, span.resource)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char *> b"name", 4)
            if ret != 0:
                return ret
            ret = pack_text(&self.pk, span.name)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char *> b"start", 5)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, span.start_ns)
            if ret != 0:
                return ret

            ret = pack_bytes(&self.pk, <char *> b"duration", 8)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, span.duration_ns)
            if ret != 0:
                return ret

            if has_error:
                ret = pack_bytes(&self.pk, <char *> b"error", 5)
                if ret != 0:
                    return ret
                ret = msgpack_pack_long(&self.pk, <long> 1)
                if ret != 0:
                    return ret

            if has_span_type:
                ret = pack_bytes(&self.pk, <char *> b"type", 4)
                if ret != 0:
                    return ret
                ret = pack_text(&self.pk, span.span_type)
                if ret != 0:
                    return ret

            if has_links:
                ret = pack_bytes(&self.pk, <char *> b"span_links", 10)
                if ret != 0:
                    return ret
                ret = self._pack_links(span._links)
                if ret != 0:
                    return ret

            if has_span_events and self.top_level_span_event_encoding:
                ret = pack_bytes(&self.pk, <char *> b"span_events", 11)
                if ret != 0:
                    return ret
                ret = self._pack_span_events(span._events)
                if ret != 0:
                    return ret

            if has_meta:
                ret = pack_bytes(&self.pk, <char *> b"meta", 4)
                if ret != 0:
                    return ret

                span_events = ""
                if has_span_events and not self.top_level_span_event_encoding:
                    span_events = json_dumps([vars(event)()  for event in span._events])
                ret = self._pack_meta(span._meta, <char *> dd_origin, span_events)
                if ret != 0:
                    return ret

            if has_meta_struct:
                ret = pack_bytes(&self.pk, <char *> b"meta_struct", 11)
                if ret != 0:
                    return ret

                ret = msgpack_pack_map(&self.pk, len(span._meta_struct))
                if ret != 0:
                    return ret
                for k, v in span._meta_struct.items():
                    ret = pack_text(&self.pk, k)
                    if ret != 0:
                        return ret
                    value_packed = packb(v)
                    ret = msgpack_pack_bin(&self.pk, len(value_packed))
                    if ret == 0:
                        ret = msgpack_pack_raw_body(&self.pk, <char *> value_packed, len(value_packed))
                    if ret != 0:
                        return ret

            if has_metrics:
                ret = pack_bytes(&self.pk, <char *> b"metrics", 7)
                if ret != 0:
                    return ret
                ret = self._pack_metrics(span._metrics)
                if ret != 0:
                    return ret

        return ret

    cdef int pack_span_event_attributes(self, object attr, int depth=0) except ? -1:
        cdef int ret
        cdef object elt

        ret = msgpack_pack_map(&self.pk, 2)
        if ret != 0:
            return ret
        ret = pack_bytes(&self.pk, <char*> b"type", 4)
        if ret != 0:
            return ret

        if isinstance(attr, str):
            ret = msgpack_pack_uint8(&self.pk, 0)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"string_value", 12)
            if ret != 0:
                return ret
            ret = pack_text(&self.pk, attr)
            if ret != 0:
                return ret
        elif isinstance(attr, bool):
            ret = msgpack_pack_uint8(&self.pk, 1)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"bool_value", 10)
            if ret != 0:
                return ret
            ret = pack_bool(&self.pk, attr)
            if ret != 0:
                return ret
        elif isinstance(attr, int):
            ret = msgpack_pack_uint8(&self.pk, 2)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"int_value", 9)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, attr)
            if ret != 0:
                return ret
        elif isinstance(attr, float):
            ret = msgpack_pack_uint8(&self.pk, 3)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"double_value", 12)
            if ret != 0:
                return ret
            ret = pack_number(&self.pk, attr)
            if ret != 0:
                return ret
        elif isinstance(attr, list):
            if depth != 0:
                raise ValueError("Nested list found; cannot encode")
            ret = msgpack_pack_uint8(&self.pk, 4)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"array_value", 11)
            if ret != 0:
                return ret
            ret = msgpack_pack_map(&self.pk, 1)
            if ret != 0:
                return ret
            ret = pack_bytes(&self.pk, <char*> b"values", 6)
            if ret != 0:
                return ret
            ret = msgpack_pack_array(&self.pk, len(attr))
            if ret != 0:
                return ret

            for elt in attr:
                ret = self.pack_span_event_attributes(elt, depth+1)
                if ret != 0:
                    return ret
        else:
            raise ValueError(f"Unsupported type for SpanEvent attribute: {type(attr)}")

        return ret

cdef class MsgpackEncoderV05(MsgpackEncoderBase):
    cdef MsgpackStringTable _st

    def __cinit__(self, size_t max_size, size_t max_item_size):
        self._st = MsgpackStringTable(max_size)

    cpdef flush(self):
        with self._lock:
            try:
                self._st.append_raw(
                    PyLong_FromLong(<long> self.get_buffer()),
                    <Py_ssize_t> super(MsgpackEncoderV05, self).size,
                )
                return self._st.flush(), len(self)
            finally:
                self._reset_buffer()

    @property
    def size(self):
        """Return the size in bytes of the encoder buffer."""
        with self._lock:
            return self._st.size + super(MsgpackEncoderV05, self).size

    cpdef put(self, list trace):
        with self._lock:
            try:
                self._st.savepoint()
                super(MsgpackEncoderV05, self).put(trace)
            except Exception:
                self._st.rollback()
                raise

    cdef inline int _pack_string(self, object string) except? -1:
        string = truncate_string(string)
        return msgpack_pack_uint32(&self.pk, self._st._index(string))

    cdef void * get_dd_origin_ref(self, str dd_origin):
        return <void *> PyLong_AsLong(self._st._index(dd_origin))

    cdef int pack_span(self, object span, void *dd_origin) except? -1:
        cdef int ret

        ret = msgpack_pack_array(&self.pk, 12)
        if ret != 0:
            return ret

        ret = self._pack_string(span.service)
        if ret != 0:
            return ret
        ret = self._pack_string(span.name)
        if ret != 0:
            return ret
        ret = self._pack_string(span.resource)
        if ret != 0:
            return ret

        _ = span._trace_id_64bits
        ret = msgpack_pack_uint64(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        _ = span.span_id
        ret = msgpack_pack_uint64(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        _ = span.parent_id
        ret = msgpack_pack_uint64(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        _ = span.start_ns
        ret = msgpack_pack_int64(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        _ = span.duration_ns
        ret = msgpack_pack_int64(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        _ = span.error
        ret = msgpack_pack_int32(&self.pk, _ if _ is not None else 0)
        if ret != 0:
            return ret

        span_links = ""
        if span._links:
            span_links = json_dumps([link.to_dict() for link in span._links])

        span_events = ""
        if span._events:
            span_events = json_dumps([vars(event)() for event in span._events])

        ret = msgpack_pack_map(
            &self.pk,
            len(span._meta) + (dd_origin is not NULL) + (len(span_links) > 0) + (len(span_events) > 0)
        )
        if ret != 0:
            return ret
        if span._meta:
            for k, v in span._meta.items():
                ret = self._pack_string(k)
                if ret != 0:
                    return ret
                ret = self._pack_string(v)
                if ret != 0:
                    return ret
        if dd_origin is not NULL:
            ret = msgpack_pack_uint32(&self.pk, <stdint.uint32_t> 1)
            if ret != 0:
                return ret
            ret = msgpack_pack_uint32(&self.pk, <stdint.uint32_t> dd_origin)
            if ret != 0:
                return ret
        if span_links:
            ret = self._pack_string(SPAN_LINKS_KEY)
            if ret != 0:
                return ret
            ret = self._pack_string(span_links)
            if ret != 0:
                return ret
        if span_events:
            ret = self._pack_string(SPAN_EVENTS_KEY)
            if ret != 0:
                return ret
            ret = self._pack_string(span_events)
            if ret != 0:
                return ret

        ret = msgpack_pack_map(&self.pk, len(span._metrics))
        if ret != 0:
            return ret
        if span._metrics:
            for k, v in span._metrics.items():
                ret = self._pack_string(k)
                if ret != 0:
                    return ret
                ret = pack_number(&self.pk, v)
                if ret != 0:
                    return ret

        ret = self._pack_string(span.span_type)
        if ret != 0:
            return ret

        return 0


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
        cdef long longval
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
                        raise OverflowError("Integer value out of range")
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
                        if ret != 0:
                            break
                        ret = self._pack(v)
                        if ret != 0:
                            break
            elif PyList_CheckExact(o):
                L = Py_SIZE(o)
                if L > ITEM_LIMIT:
                    raise ValueError("list is too large")
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
                PyErr_Format(TypeError, b"can not serialize '%.200s' object", Py_TYPE(o).tp_name)
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
