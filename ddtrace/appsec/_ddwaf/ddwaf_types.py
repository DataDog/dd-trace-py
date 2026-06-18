from collections.abc import Mapping
from collections.abc import Sequence
from enum import IntEnum
from typing import Any
from typing import Optional

from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_builder_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_handle_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_subcontext_capsule
from ddtrace.appsec._utils import _observator  # noqa: F401  (re-exported; imported by tests)
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ddwaf as _native
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

#
# libddwaf is statically linked into the native extension (ddtrace.internal.native._native.ddwaf);
# importing this module no longer loads an external shared library.
#

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

# libddwaf stores integers in a signed 64-bit field. The previous ctypes layer relied on
# ``ctypes.c_int64`` silently masking out-of-range Python ints to 64-bit two's complement; the
# native binding extracts a strict ``i64`` instead, so we reproduce that wrap-around here.
_INT64_MASK = 0xFFFFFFFFFFFFFFFF
_INT64_SIGN_BIT = 0x8000000000000000
_INT64_MODULUS = 0x10000000000000000


def _to_int64(value: int) -> int:
    """Reduce an arbitrary-precision Python int to a signed 64-bit value (two's complement)."""
    value &= _INT64_MASK
    if value >= _INT64_SIGN_BIT:
        value -= _INT64_MODULUS
    return value


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


# Set of all the string variants for quick membership tests. ``ddwaf_object_get_bytes`` handles all
# three transparently, so the reader only needs to know "is this any kind of string".
_STRING_TYPES = frozenset(
    (
        DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING,
        DDWAF_OBJ_TYPE.DDWAF_OBJ_LITERAL_STRING,
        DDWAF_OBJ_TYPE.DDWAF_OBJ_SMALL_STRING,
    )
)


class DDWAF_RET_CODE(IntEnum):
    DDWAF_ERR_INTERNAL = -3
    DDWAF_ERR_INVALID_OBJECT = -2
    DDWAF_ERR_INVALID_ARGUMENT = -1
    DDWAF_OK = 0
    DDWAF_MATCH = 1


# The default allocator is a process-wide singleton, always valid and never destroyed.
DEFAULT_ALLOCATOR = _native.ddwaf_get_default_allocator()


def ddwaf_object_free(obj: "ddwaf_object") -> None:
    """Compatibility helper: free an object's inner heap data using the default allocator."""
    _native.ddwaf_object_destroy(obj._native, DEFAULT_ALLOCATOR)


#
# Objects Definitions
#


class ddwaf_object:
    """Python view over a native ``ddwaf_object`` (``ddtrace.internal.native._native.ddwaf``).

    The recursive serialization (``__init__``) and deserialization (``struct``) logic live here; the
    native module only provides the typed wrapper and the per-primitive set_/insert/read functions.
    """

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
        self._native = _native.DDWafObject()
        _build_ddwaf_object(self._native, struct, observator, max_objects, max_depth, max_string_length)

    @classmethod
    def create_without_limits(cls, struct: DDWafRulesType) -> "ddwaf_object":
        return cls(struct, max_objects=DDWAF_NO_LIMIT, max_depth=DDWAF_DEPTH_NO_LIMIT, max_string_length=DDWAF_NO_LIMIT)

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Optional["ddwaf_object"]:
        """Create a ddwaf_object from a JSON string as bytes."""
        obj = cls.__new__(cls)
        obj._native = _native.DDWafObject()
        if not _native.ddwaf_object_from_json(obj._native, data, len(data), DEFAULT_ALLOCATOR):
            log.debug("Failed to create ddwaf_object from JSON: %s", data)
            # Free any partial allocation (no-op on the zero-initialised INVALID object).
            _native.ddwaf_object_destroy(obj._native, DEFAULT_ALLOCATOR)
            return None
        return obj

    @property
    def struct(self) -> DDWafRulesType:
        """Generate a python structure from the native ddwaf_object."""
        return _read_struct(self._native)

    def __bool__(self) -> bool:
        return bool(self._native)

    def __repr__(self) -> str:
        return repr(self.struct)


def _read_struct(obj: Any) -> Any:
    """Recursively convert a native ddwaf_object (or one of its slot views) into a Python value."""
    t = obj.type
    if t in _STRING_TYPES:
        return _native.ddwaf_object_get_bytes(obj).decode("UTF-8", errors="ignore")
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP:
        return {
            _read_struct(_native.ddwaf_object_map_key(obj, i)): _read_struct(_native.ddwaf_object_map_value(obj, i))
            for i in range(_native.ddwaf_object_map_len(obj))
        }
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY:
        return [
            _read_struct(_native.ddwaf_object_array_get(obj, i)) for i in range(_native.ddwaf_object_array_len(obj))
        ]
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_SIGNED:
        return _native.ddwaf_object_get_signed(obj)
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_UNSIGNED:
        return _native.ddwaf_object_get_unsigned(obj)
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_BOOL:
        return _native.ddwaf_object_get_bool(obj)
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_FLOAT:
        return _native.ddwaf_object_get_float(obj)
    if t == DDWAF_OBJ_TYPE.DDWAF_OBJ_NULL or t == DDWAF_OBJ_TYPE.DDWAF_OBJ_INVALID:
        return None
    log.debug("ddwaf_object struct: unknown object type: %s", repr(t))
    return None


#
# Recursive builder for ddwaf_object (serialization of python structures).
#
# ``obj`` is either a freshly allocated native ``DDWafObject`` (top level) or a non-owning slot view
# into a parent container (returned by ddwaf_object_insert / ddwaf_object_insert_key). It is only
# forwarded to the set_*/insert native functions.
#


def _build_sequence(
    obj: Any,
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
    _native.ddwaf_object_set_array(obj, min(len(struct), max_objects), DEFAULT_ALLOCATOR)
    for counter_object, elt in enumerate(struct):
        if counter_object >= max_objects:
            observator.set_container_size(len(struct))
            break
        slot = _native.ddwaf_object_insert(obj, DEFAULT_ALLOCATOR)
        _build_ddwaf_object(slot, elt, observator, max_objects, max_depth - 1, max_string_length)


def _build_mapping(
    obj: Any,
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
    _native.ddwaf_object_set_map(obj, min(len(struct), max_objects), DEFAULT_ALLOCATOR)
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
        slot = _native.ddwaf_object_insert_key(obj, res_key, len(res_key), DEFAULT_ALLOCATOR)
        _build_ddwaf_object(slot, val, observator, max_objects, max_depth - 1, max_string_length)
        counter_object += 1


def _build_ddwaf_object(
    obj: Any,
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
        _build_mapping(obj, struct, observator, max_objects, max_depth, max_string_length)
    elif t is str:
        encoded = struct.encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        _native.ddwaf_object_set_string(obj, encoded, len(encoded), DEFAULT_ALLOCATOR)
    elif t is list:
        _build_sequence(obj, struct, observator, max_objects, max_depth, max_string_length)
    elif t is int:
        _native.ddwaf_object_set_signed(obj, _to_int64(struct))
    elif t is bool:
        _native.ddwaf_object_set_bool(obj, struct)
    elif t is float:
        _native.ddwaf_object_set_float(obj, struct)
    elif t is bytes:
        if len(struct) > max_string_length:
            observator.set_string_length(len(struct))
            struct = struct[:max_string_length]
        _native.ddwaf_object_set_string(obj, struct, len(struct), DEFAULT_ALLOCATOR)
    elif struct is None:
        _native.ddwaf_object_set_null(obj)
    elif isinstance(struct, Sequence):
        _build_sequence(obj, struct, observator, max_objects, max_depth, max_string_length)
    elif isinstance(struct, Mapping):
        _build_mapping(obj, struct, observator, max_objects, max_depth, max_string_length)
    else:
        encoded = str(struct).encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        _native.ddwaf_object_set_string(obj, encoded, len(encoded), DEFAULT_ALLOCATOR)


#
# WAF lifecycle helpers (mirror the previous ctypes ``py_ddwaf_*`` wrappers).
#


def py_ddwaf_known_addresses(handle: ddwaf_handle_capsule) -> list[str]:
    return _native.ddwaf_known_addresses(handle.handle)


def py_ddwaf_context_init(handle: ddwaf_handle_capsule) -> ddwaf_context_capsule:
    return ddwaf_context_capsule(_native.ddwaf_context_init(handle.handle, DEFAULT_ALLOCATOR))


def py_ddwaf_subcontext_init(ctx: ddwaf_context_capsule) -> ddwaf_subcontext_capsule:
    return ddwaf_subcontext_capsule(_native.ddwaf_subcontext_init(ctx.ctx))


def ddwaf_context_eval(target: Any, data: ddwaf_object, alloc: Any, result: ddwaf_object, timeout_us: int) -> int:
    return _native.ddwaf_context_eval(target, data._native, alloc, result._native, timeout_us)


def ddwaf_subcontext_eval(target: Any, data: ddwaf_object, alloc: Any, result: ddwaf_object, timeout_us: int) -> int:
    return _native.ddwaf_subcontext_eval(target, data._native, alloc, result._native, timeout_us)


def py_ddwaf_builder_init() -> ddwaf_builder_capsule:
    return ddwaf_builder_capsule(_native.ddwaf_builder_init())


def py_add_or_update_config(
    builder: ddwaf_builder_capsule, path: str, config: ddwaf_object, diagnostics: ddwaf_object
) -> bool:
    bin_path = path.encode()
    return _native.ddwaf_builder_add_or_update_config(
        builder.builder, bin_path, len(bin_path), config._native, diagnostics._native
    )


def py_remove_config(builder: ddwaf_builder_capsule, path: str) -> bool:
    bin_path = path.encode()
    return _native.ddwaf_builder_remove_config(builder.builder, bin_path, len(bin_path))


def py_ddwaf_builder_build_instance(builder: ddwaf_builder_capsule) -> ddwaf_handle_capsule:
    return ddwaf_handle_capsule(_native.ddwaf_builder_build_instance(builder.builder))


def py_ddwaf_builder_get_config_paths(builder: ddwaf_builder_capsule, filter_str: str) -> int:
    bin_filter = filter_str.encode()
    return _native.ddwaf_builder_get_config_paths(builder.builder, bin_filter, len(bin_filter))


asm_config._ddwaf_version = _native.ddwaf_get_version()
