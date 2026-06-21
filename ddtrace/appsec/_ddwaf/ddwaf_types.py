from collections.abc import Mapping
from collections.abc import Sequence
import ctypes
import ctypes.util
from enum import IntEnum
from platform import system
from typing import Any
from typing import Optional

from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_builder_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_handle_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_subcontext_capsule
from ddtrace.appsec._utils import _observator
from ddtrace.appsec._utils import unpatching_popen
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

#
# Dynamic loading of libddwaf. For now it requires the file or a link to be in current directory
#

if system() == "Linux":
    try:
        with unpatching_popen():
            ctypes.CDLL(ctypes.util.find_library("rt"), mode=ctypes.RTLD_GLOBAL)
    except Exception:  # nosec
        pass

with unpatching_popen():
    ddwaf = ctypes.CDLL(asm_config._asm_libddwaf)
#
# Constants
#

DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
DDWAF_MAX_CONTAINER_SIZE = 256
DDWAF_NO_LIMIT = 1 << 31
DDWAF_DEPTH_NO_LIMIT = 1000

# Containers store their size/capacity in a uint16, so they cannot hold more than this.
DDWAF_OBJ_MAX_CAPACITY = 0xFFFF


# Type tags are not a bitfield: UNSIGNED (0x06) overlaps BOOL (0x02) | SIGNED (0x04), so compare
# with exact equality, never bitmasks. Strings have 3 variants (heap, literal, inline "small").
class DDWAF_OBJ_TYPE(IntEnum):
    DDWAF_OBJ_INVALID = 0
    # Null type, only used for its semantical value
    DDWAF_OBJ_NULL = 0x01
    # Value shall be decoded as a bool
    DDWAF_OBJ_BOOL = 0x02
    # Value shall be decoded as an int64_t
    DDWAF_OBJ_SIGNED = 0x04
    # Value shall be decoded as a uint64_t
    DDWAF_OBJ_UNSIGNED = 0x06
    # 64-bit float (or double) type
    DDWAF_OBJ_FLOAT = 0x08
    # Value shall be decoded as a UTF-8 string of length size, stored on the heap
    DDWAF_OBJ_STRING = 0x10
    # Read-only C string that is never freed on destruction
    DDWAF_OBJ_LITERAL_STRING = 0x12
    # UTF-8 string of at most 14 bytes stored inline in the object (no allocation)
    DDWAF_OBJ_SMALL_STRING = 0x14
    # Array of ddwaf_object of length size
    DDWAF_OBJ_ARRAY = 0x20
    # Map: array of ddwaf_object_kv of length size
    DDWAF_OBJ_MAP = 0x40


# Set of all the string variants for quick membership tests
_STRING_TYPES = frozenset(
    (
        DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING,
        DDWAF_OBJ_TYPE.DDWAF_OBJ_LITERAL_STRING,
    )
)


class DDWAF_RET_CODE(IntEnum):
    DDWAF_ERR_INTERNAL = -3
    DDWAF_ERR_INVALID_OBJECT = -2
    DDWAF_ERR_INVALID_ARGUMENT = -1
    DDWAF_OK = 0
    DDWAF_MATCH = 1


class DDWAF_LOG_LEVEL(IntEnum):
    DDWAF_LOG_TRACE = 0
    DDWAF_LOG_DEBUG = 1
    DDWAF_LOG_INFO = 2
    DDWAF_LOG_WARN = 3
    DDWAF_LOG_ERROR = 4
    DDWAF_LOG_OFF = 5


# An allocator is an opaque pointer (`typedef struct _ddwaf_allocator *ddwaf_allocator;`)
ddwaf_allocator = ctypes.c_void_p
ddwaf_handle = ctypes.c_void_p  # mainly an abstract type in the interface
ddwaf_context = ctypes.c_void_p  # mainly an abstract type in the interface
ddwaf_subcontext = ctypes.c_void_p  # mainly an abstract type in the interface
ddwaf_builder = ctypes.c_void_p  # mainly an abstract type in the interface


#
# Objects Definitions
#


# The ddwaf_object is a 16-byte tagged union. Field definitions are wired up after the
# class bodies to allow the cyclic references (array -> object, map -> object_kv -> object).
class ddwaf_object(ctypes.Union):
    """ctypes view of the libddwaf 2.0 ``ddwaf_object`` 16-byte tagged union."""

    def __init__(
        self,
        struct: Optional[DDWafRulesType] = None,
        observator: Optional[_observator] = None,
        max_objects: int = DDWAF_MAX_CONTAINER_SIZE,
        max_depth: int = DDWAF_MAX_CONTAINER_DEPTH,
        max_string_length: int = DDWAF_MAX_STRING_LENGTH,
    ) -> None:
        if observator is None:
            observator = _observator()
        # Build through a pointer (uniform with the slots returned by insert/insert_key).
        _build_ddwaf_object(ctypes.pointer(self), struct, observator, max_objects, max_depth, max_string_length)

    @classmethod
    def create_without_limits(cls, struct: DDWafRulesType) -> "ddwaf_object":
        return cls(struct, max_objects=DDWAF_NO_LIMIT, max_depth=DDWAF_DEPTH_NO_LIMIT, max_string_length=DDWAF_NO_LIMIT)

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Optional["ddwaf_object"]:
        """Create a ddwaf_object from a JSON string as bytes."""
        obj = cls.__new__(cls)
        if not ddwaf_object_from_json(obj, data, len(data), DEFAULT_ALLOCATOR):
            log.debug("Failed to create ddwaf_object from JSON: %s", data)
            # Free any partial allocation (no-op on the zero-initialised INVALID object).
            ddwaf_object_destroy(obj, DEFAULT_ALLOCATOR)
            return None
        return obj

    @property
    def struct(self) -> DDWafRulesType:
        """Generate a python structure from ddwaf_object"""
        t = self.type
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_SMALL_STRING:
            sstr = self.value.sstr
            if not sstr.size:
                return ""
            # Length-based read: slicing the c_char array would truncate at an embedded NUL.
            return ctypes.string_at(ctypes.addressof(sstr) + _SMALL_STRING_DATA_OFFSET, sstr.size).decode(
                "UTF-8", errors="ignore"
            )
        if t in _STRING_TYPES:
            s = self.value.str
            if not s.ptr or not s.size:
                return ""
            return ctypes.string_at(s.ptr, s.size).decode("UTF-8", errors="ignore")
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP:
            m = self.value.map
            return {m.ptr[i].key.struct: m.ptr[i].val.struct for i in range(m.size)}
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY:
            a = self.value.array
            return [a.ptr[i].struct for i in range(a.size)]
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_SIGNED:
            return self.value.i64.val
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_UNSIGNED:
            return self.value.u64.val
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_BOOL:
            return self.value.b8.val
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_FLOAT:
            return self.value.f64.val
        if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_NULL or t == DDWAF_OBJ_TYPE.DDWAF_OBJ_INVALID:
            return None
        log.debug("ddwaf_object struct: unknown object type: %s", repr(t))
        return None

    def __repr__(self) -> str:
        return repr(self.struct)


ddwaf_object_p = ctypes.POINTER(ddwaf_object)


class ddwaf_object_kv(ctypes.Structure):
    """A key/value pair stored in a ddwaf_object map. Fields are wired up below."""


ddwaf_object_kv_p = ctypes.POINTER(ddwaf_object_kv)


class _ddwaf_object_bool(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("val", ctypes.c_bool)]


class _ddwaf_object_signed(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("val", ctypes.c_int64)]


class _ddwaf_object_unsigned(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("val", ctypes.c_uint64)]


class _ddwaf_object_float(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("val", ctypes.c_double)]


class _ddwaf_object_string(ctypes.Structure):
    # ``ptr`` is a raw pointer read with ctypes.string_at using the explicit size
    # (the buffer is not necessarily NUL-terminated in libddwaf 2.0).
    _fields_ = [("type", ctypes.c_uint8), ("size", ctypes.c_uint32), ("ptr", ctypes.c_void_p)]


class _ddwaf_object_small_string(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("size", ctypes.c_uint8), ("data", ctypes.c_char * 14)]


# Byte offset of the inline data buffer within a small string, used for length-based reads.
_SMALL_STRING_DATA_OFFSET = _ddwaf_object_small_string.data.offset


class _ddwaf_object_array(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("capacity", ctypes.c_uint16),
        ("ptr", ddwaf_object_p),
    ]


class _ddwaf_object_map(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("capacity", ctypes.c_uint16),
        ("ptr", ddwaf_object_kv_p),
    ]


class _ddwaf_object_value(ctypes.Union):
    _fields_ = [
        ("b8", _ddwaf_object_bool),
        ("i64", _ddwaf_object_signed),
        ("u64", _ddwaf_object_unsigned),
        ("f64", _ddwaf_object_float),
        ("str", _ddwaf_object_string),
        ("sstr", _ddwaf_object_small_string),
        ("array", _ddwaf_object_array),
        ("map", _ddwaf_object_map),
    ]


ddwaf_object._fields_ = [
    ("type", ctypes.c_uint8),
    ("value", _ddwaf_object_value),
]

ddwaf_object_kv._fields_ = [
    ("key", ddwaf_object),
    ("val", ddwaf_object),
]


#
# Recursive builder for ddwaf_object (serialization of python structures).
#
# Each ``self`` is either a freshly allocated ``ddwaf_object`` (top level) or a pointer to a
# slot inside a parent container (returned by ddwaf_object_insert / ddwaf_object_insert_key).
# ``self`` is always a pointer to the ddwaf_object being filled (ctypes.pointer(top_level) or a
# slot from ddwaf_object_insert/insert_key); it is only forwarded to the set_*/insert C functions.
#


def _build_sequence(
    self: "ctypes._Pointer[ddwaf_object]",
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    if max_depth <= 0:
        observator.set_container_depth(DDWAF_MAX_CONTAINER_DEPTH)
        max_objects = 0
    elif max_objects > DDWAF_OBJ_MAX_CAPACITY:
        # Container size is a uint16; cap inserts (even on the no-limit path) so it can't wrap.
        max_objects = DDWAF_OBJ_MAX_CAPACITY
    # Reserve only what we will actually insert (bounded by max_objects, always <= uint16 max).
    ddwaf_object_set_array(self, min(len(struct), max_objects), DEFAULT_ALLOCATOR)
    for counter_object, elt in enumerate(struct):
        if counter_object >= max_objects:
            observator.set_container_size(len(struct))
            break
        slot = ddwaf_object_insert(self, DEFAULT_ALLOCATOR)
        _build_ddwaf_object(slot, elt, observator, max_objects, max_depth - 1, max_string_length)


def _build_mapping(
    self: "ctypes._Pointer[ddwaf_object]",
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    if max_depth <= 0:
        observator.set_container_depth(DDWAF_MAX_CONTAINER_DEPTH)
        max_objects = 0
    elif max_objects > DDWAF_OBJ_MAX_CAPACITY:
        # Container size is a uint16; cap inserts (even on the no-limit path) so it can't wrap.
        max_objects = DDWAF_OBJ_MAX_CAPACITY
    # Reserve only what we will actually insert (bounded by max_objects, always <= uint16 max).
    ddwaf_object_set_map(self, min(len(struct), max_objects), DEFAULT_ALLOCATOR)
    # order is unspecified and could lead to problems if max_objects is reached
    counter_object = 0
    for key, val in struct.items():
        kt = type(key)
        if kt is str:
            res_key = key.encode("UTF-8", errors="ignore")
        elif kt is bytes:
            res_key = key
        else:  # discards non string keys
            continue
        if counter_object >= max_objects:
            observator.set_container_size(len(struct))
            break
        if len(res_key) > max_string_length:
            observator.set_string_length(len(res_key))
            res_key = res_key[:max_string_length]
        slot = ddwaf_object_insert_key(self, res_key, len(res_key), DEFAULT_ALLOCATOR)
        _build_ddwaf_object(slot, val, observator, max_objects, max_depth - 1, max_string_length)
        counter_object += 1


def _build_ddwaf_object(
    self: "ctypes._Pointer[ddwaf_object]",
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    """Recursive builder for ddwaf_object.

    Uses type identity checks ordered by frequency (dict/str/list dominate WAF payloads)
    with leaf handlers inlined to avoid function call overhead. Container types delegate
    to separate functions since their logic is more complex. Falls back to isinstance for
    Sequence/Mapping subclasses.
    """
    t = type(struct)
    if t is dict:
        _build_mapping(self, struct, observator, max_objects, max_depth, max_string_length)
    elif t is str:
        encoded = struct.encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        ddwaf_object_set_string(self, encoded, len(encoded), DEFAULT_ALLOCATOR)
    elif t is list:
        _build_sequence(self, struct, observator, max_objects, max_depth, max_string_length)
    elif t is int:
        ddwaf_object_set_signed(self, struct)
    elif t is bool:
        ddwaf_object_set_bool(self, struct)
    elif t is float:
        ddwaf_object_set_float(self, struct)
    elif t is bytes:
        if len(struct) > max_string_length:
            observator.set_string_length(len(struct))
            struct = struct[:max_string_length]
        ddwaf_object_set_string(self, struct, len(struct), DEFAULT_ALLOCATOR)
    elif struct is None:
        ddwaf_object_set_null(self)
    elif isinstance(struct, Sequence):
        _build_sequence(self, struct, observator, max_objects, max_depth, max_string_length)
    elif isinstance(struct, Mapping):
        _build_mapping(self, struct, observator, max_objects, max_depth, max_string_length)
    else:
        encoded = str(struct).encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        ddwaf_object_set_string(self, encoded, len(encoded), DEFAULT_ALLOCATOR)


ddwaf_log_cb = ctypes.POINTER(
    ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint64
    )
)


#
# Allocators
#


ddwaf_get_default_allocator = ctypes.CFUNCTYPE(ddwaf_allocator)(
    ("ddwaf_get_default_allocator", ddwaf),
    (),
)

# The default allocator is a process-wide singleton, always valid and never destroyed.
DEFAULT_ALLOCATOR = ddwaf_get_default_allocator()


ddwaf_object_destroy = ctypes.CFUNCTYPE(None, ddwaf_object_p, ddwaf_allocator)(
    ("ddwaf_object_destroy", ddwaf),
    (
        (1, "object"),
        (1, "alloc"),
    ),
)


def ddwaf_object_free(obj: ddwaf_object) -> None:
    """Compatibility helper: destroy an object using the default allocator."""
    ddwaf_object_destroy(obj, DEFAULT_ALLOCATOR)


#
# Functions Prototypes (creating python counterpart function from C function)
#

ddwaf_init = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_init", ddwaf),
    (
        (1, "ruleset_map"),
        (1, "diagnostics", None),
    ),
)


def py_ddwaf_init(ruleset_map: ddwaf_object, info: ddwaf_object) -> ddwaf_handle_capsule:
    return ddwaf_handle_capsule(ddwaf_init(ruleset_map, info), ddwaf_destroy)


ddwaf_destroy = ctypes.CFUNCTYPE(None, ddwaf_handle)(
    ("ddwaf_destroy", ddwaf),
    ((1, "handle"),),
)

ddwaf_known_addresses = ctypes.CFUNCTYPE(
    ctypes.POINTER(ctypes.c_char_p), ddwaf_handle, ctypes.POINTER(ctypes.c_uint32)
)(
    ("ddwaf_known_addresses", ddwaf),
    (
        (1, "handle"),
        (1, "size"),
    ),
)


def py_ddwaf_known_addresses(handle: ddwaf_handle_capsule) -> list[str]:
    size = ctypes.c_uint32()
    obj = ddwaf_known_addresses(handle.handle, size)
    return [obj[i].decode("UTF-8") for i in range(size.value)]


ddwaf_context_init = ctypes.CFUNCTYPE(ddwaf_context, ddwaf_handle, ddwaf_allocator)(
    ("ddwaf_context_init", ddwaf),
    (
        (1, "handle"),
        (1, "output_alloc"),
    ),
)


def py_ddwaf_context_init(handle: ddwaf_handle_capsule) -> ddwaf_context_capsule:
    return ddwaf_context_capsule(ddwaf_context_init(handle.handle, DEFAULT_ALLOCATOR), ddwaf_context_destroy)


ddwaf_context_eval = ctypes.CFUNCTYPE(
    ctypes.c_int, ddwaf_context, ddwaf_object_p, ddwaf_allocator, ddwaf_object_p, ctypes.c_uint64
)(
    ("ddwaf_context_eval", ddwaf),
    (
        (1, "context"),
        (1, "data"),
        (1, "alloc"),
        (1, "result"),
        (1, "timeout"),
    ),
)

ddwaf_context_destroy = ctypes.CFUNCTYPE(None, ddwaf_context)(
    ("ddwaf_context_destroy", ddwaf),
    ((1, "context"),),
)


# ddwaf_subcontext (libddwaf 2.0 replacement for ephemeral data)


ddwaf_subcontext_init = ctypes.CFUNCTYPE(ddwaf_subcontext, ddwaf_context)(
    ("ddwaf_subcontext_init", ddwaf),
    ((1, "context"),),
)


def py_ddwaf_subcontext_init(ctx: ddwaf_context_capsule) -> ddwaf_subcontext_capsule:
    return ddwaf_subcontext_capsule(ddwaf_subcontext_init(ctx.ctx), ddwaf_subcontext_destroy)


ddwaf_subcontext_eval = ctypes.CFUNCTYPE(
    ctypes.c_int, ddwaf_subcontext, ddwaf_object_p, ddwaf_allocator, ddwaf_object_p, ctypes.c_uint64
)(
    ("ddwaf_subcontext_eval", ddwaf),
    (
        (1, "subcontext"),
        (1, "data"),
        (1, "alloc"),
        (1, "result"),
        (1, "timeout"),
    ),
)

ddwaf_subcontext_destroy = ctypes.CFUNCTYPE(None, ddwaf_subcontext)(
    ("ddwaf_subcontext_destroy", ddwaf),
    ((1, "subcontext"),),
)


# ddwaf_builder


ddwaf_builder_init = ctypes.CFUNCTYPE(ddwaf_builder)(
    ("ddwaf_builder_init", ddwaf),
    (),
)


def py_ddwaf_builder_init() -> ddwaf_builder_capsule:
    return ddwaf_builder_capsule(ddwaf_builder_init(), ddwaf_builder_destroy)


ddwaf_builder_add_or_update_config = ctypes.CFUNCTYPE(
    ctypes.c_bool, ddwaf_builder, ctypes.c_char_p, ctypes.c_uint32, ddwaf_object_p, ddwaf_object_p
)(
    ("ddwaf_builder_add_or_update_config", ddwaf),
    (
        (1, "builder"),
        (1, "path"),
        (1, "path_len"),
        (1, "config"),
        (1, "diagnostics"),
    ),
)


def py_add_or_update_config(
    builder: ddwaf_builder_capsule, path: str, config: ddwaf_object, diagnostics: ddwaf_object
) -> bool:
    bin_path = path.encode()
    return ddwaf_builder_add_or_update_config(builder.builder, bin_path, len(bin_path), config, diagnostics)


ddwaf_builder_remove_config = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_builder, ctypes.c_char_p, ctypes.c_uint32)(
    ("ddwaf_builder_remove_config", ddwaf),
    (
        (1, "builder"),
        (1, "path"),
        (1, "path_len"),
    ),
)


def py_remove_config(builder: ddwaf_builder_capsule, path: str) -> bool:
    bin_path = path.encode()
    return ddwaf_builder_remove_config(builder.builder, bin_path, len(bin_path))


ddwaf_builder_build_instance = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_builder)(
    ("ddwaf_builder_build_instance", ddwaf),
    ((1, "builder"),),
)


def py_ddwaf_builder_build_instance(builder: ddwaf_builder_capsule) -> ddwaf_handle_capsule:
    return ddwaf_handle_capsule(ddwaf_builder_build_instance(builder.builder), ddwaf_destroy)


ddwaf_builder_get_config_paths = ctypes.CFUNCTYPE(
    ctypes.c_uint32, ddwaf_builder, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32
)(
    ("ddwaf_builder_get_config_paths", ddwaf),
    (
        (1, "builder"),
        (1, "paths"),
        (1, "filter"),
        (1, "filter_len"),
    ),
)


def py_ddwaf_builder_get_config_paths(builder: ddwaf_builder_capsule, filter_str: str) -> int:
    return ddwaf_builder_get_config_paths(builder.builder, None, filter_str.encode(), len(filter_str))


ddwaf_builder_destroy = ctypes.CFUNCTYPE(None, ddwaf_builder)(
    ("ddwaf_builder_destroy", ddwaf),
    ((1, "builder"),),
)


# ddwaf_object creation / serialization (libddwaf 2.0 set_* and insert API)


ddwaf_object_set_string = ctypes.CFUNCTYPE(
    ddwaf_object_p, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32, ddwaf_allocator
)(
    ("ddwaf_object_set_string", ddwaf),
    (
        (1, "object"),
        (1, "string"),
        (1, "length"),
        (1, "alloc"),
    ),
)

ddwaf_object_set_signed = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_int64)(
    ("ddwaf_object_set_signed", ddwaf),
    (
        (1, "object"),
        (1, "value"),
    ),
)

ddwaf_object_set_unsigned = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_uint64)(
    ("ddwaf_object_set_unsigned", ddwaf),
    (
        (1, "object"),
        (1, "value"),
    ),
)

ddwaf_object_set_bool = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_bool)(
    ("ddwaf_object_set_bool", ddwaf),
    (
        (1, "object"),
        (1, "value"),
    ),
)

ddwaf_object_set_float = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_double)(
    ("ddwaf_object_set_float", ddwaf),
    (
        (1, "object"),
        (1, "value"),
    ),
)

ddwaf_object_set_null = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_set_null", ddwaf),
    ((1, "object"),),
)

ddwaf_object_set_array = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_uint16, ddwaf_allocator)(
    ("ddwaf_object_set_array", ddwaf),
    (
        (1, "object"),
        (1, "capacity"),
        (1, "alloc"),
    ),
)

ddwaf_object_set_map = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_uint16, ddwaf_allocator)(
    ("ddwaf_object_set_map", ddwaf),
    (
        (1, "object"),
        (1, "capacity"),
        (1, "alloc"),
    ),
)

# Returns a pointer to the newly inserted (uninitialized) element to be built into.
ddwaf_object_insert = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ddwaf_allocator)(
    ("ddwaf_object_insert", ddwaf),
    (
        (1, "array"),
        (1, "alloc"),
    ),
)

ddwaf_object_insert_key = ctypes.CFUNCTYPE(
    ddwaf_object_p, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32, ddwaf_allocator
)(
    ("ddwaf_object_insert_key", ddwaf),
    (
        (1, "map"),
        (1, "key"),
        (1, "length"),
        (1, "alloc"),
    ),
)

ddwaf_object_from_json = ctypes.CFUNCTYPE(
    ctypes.c_bool, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32, ddwaf_allocator
)(
    ("ddwaf_object_from_json", ddwaf),
    (
        (1, "output"),
        (1, "json_str"),
        (1, "length"),
        (1, "alloc"),
    ),
)


ddwaf_get_version = ctypes.CFUNCTYPE(ctypes.c_char_p)(
    ("ddwaf_get_version", ddwaf),
    (),
)

asm_config._ddwaf_version = ddwaf_get_version().decode()


ddwaf_set_log_cb = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_log_cb, ctypes.c_int)(
    ("ddwaf_set_log_cb", ddwaf),
    (
        (1, "cb"),
        (1, "min_level"),
    ),
)
