from collections.abc import Mapping
from collections.abc import Sequence
import ctypes
import ctypes.util
from enum import IntEnum
from platform import system
from typing import Any
from typing import Optional

from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._utils import _observator
from ddtrace.appsec._utils import unpatching_popen
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

#
# Dynamic loading of libddwaf
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
# Python-side truncation constants (v2 removed WAF-side limits; caller enforces truncation)
#

DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
DDWAF_MAX_CONTAINER_SIZE = 256
DDWAF_NO_LIMIT = 1 << 31
DDWAF_DEPTH_NO_LIMIT = 1000


#
# Enumerations (v2 values)
#


class DDWAF_OBJ_TYPE(IntEnum):
    DDWAF_OBJ_INVALID = 0x00
    DDWAF_OBJ_NULL = 0x01
    DDWAF_OBJ_BOOL = 0x02
    DDWAF_OBJ_SIGNED = 0x04
    DDWAF_OBJ_UNSIGNED = 0x06
    DDWAF_OBJ_FLOAT = 0x08
    DDWAF_OBJ_STRING = 0x10
    DDWAF_OBJ_LITERAL_STRING = 0x12
    DDWAF_OBJ_SMALL_STRING = 0x14
    DDWAF_OBJ_ARRAY = 0x20
    DDWAF_OBJ_MAP = 0x40


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


#
# Opaque types
#

ddwaf_handle = ctypes.c_void_p
ddwaf_context = ctypes.c_void_p
ddwaf_subcontext = ctypes.c_void_p
ddwaf_builder = ctypes.c_void_p
ddwaf_allocator = ctypes.c_void_p


# ddwaf_object is now an opaque 16-byte union in v2.
# All access goes through C getter/setter functions — no direct field access.
class ddwaf_object(ctypes.Structure):
    _fields_ = [("_opaque", ctypes.c_ubyte * 16)]


ddwaf_object_p = ctypes.POINTER(ddwaf_object)


#
# Allocator
#

ddwaf_get_default_allocator = ctypes.CFUNCTYPE(ddwaf_allocator)(
    ("ddwaf_get_default_allocator", ddwaf), ()
)

DEFAULT_ALLOCATOR = ddwaf_get_default_allocator()


#
# Logging callback type
#

ddwaf_log_cb = ctypes.POINTER(
    ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint64
    )
)


# ============================================================================
# C Function Bindings — WAF lifecycle
# ============================================================================

# ddwaf_init(ruleset, diagnostics) -> handle  (v2: no config param)
ddwaf_init = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_init", ddwaf),
    (
        (1, "ruleset_map"),
        (1, "diagnostics", None),
    ),
)

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

# ============================================================================
# C Function Bindings — Builder (v2: no config param)
# ============================================================================

ddwaf_builder_init = ctypes.CFUNCTYPE(ddwaf_builder)(
    ("ddwaf_builder_init", ddwaf),
    (),
)

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

ddwaf_builder_remove_config = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_builder, ctypes.c_char_p, ctypes.c_uint32)(
    ("ddwaf_builder_remove_config", ddwaf),
    (
        (1, "builder"),
        (1, "path"),
        (1, "path_len"),
    ),
)

ddwaf_builder_build_instance = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_builder)(
    ("ddwaf_builder_build_instance", ddwaf),
    ((1, "builder"),),
)

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

ddwaf_builder_destroy = ctypes.CFUNCTYPE(None, ddwaf_builder)(
    ("ddwaf_builder_destroy", ddwaf),
    ((1, "builder"),),
)

# ============================================================================
# C Function Bindings — Context (v2: output allocator required)
# ============================================================================

ddwaf_context_init = ctypes.CFUNCTYPE(ddwaf_context, ddwaf_handle, ddwaf_allocator)(
    ("ddwaf_context_init", ddwaf),
    (
        (1, "handle"),
        (1, "output_alloc"),
    ),
)

# v2: ddwaf_context_eval replaces ddwaf_run; single data param + input allocator
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

# ============================================================================
# C Function Bindings — Subcontext (v2: replaces ephemerals)
# ============================================================================

ddwaf_subcontext_init = ctypes.CFUNCTYPE(ddwaf_subcontext, ddwaf_context)(
    ("ddwaf_subcontext_init", ddwaf),
    ((1, "context"),),
)

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

# ============================================================================
# C Function Bindings — Object creation (v2: set_* naming, allocator params)
# ============================================================================

ddwaf_object_set_invalid = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_set_invalid", ddwaf),
    ((1, "object"),),
)

ddwaf_object_set_null = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_set_null", ddwaf),
    ((1, "object"),),
)

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

ddwaf_object_set_string_literal = ctypes.CFUNCTYPE(
    ddwaf_object_p, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32
)(
    ("ddwaf_object_set_string_literal", ddwaf),
    (
        (1, "object"),
        (1, "string"),
        (1, "length"),
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

# ============================================================================
# C Function Bindings — Object container insertion (v2: returns pointer to slot)
# ============================================================================

# Insert into array, returns pointer to the new element to fill
ddwaf_object_insert = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ddwaf_allocator)(
    ("ddwaf_object_insert", ddwaf),
    (
        (1, "array"),
        (1, "alloc"),
    ),
)

# Insert into map with key (key is copied), returns pointer to the value slot
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

# Insert into map with literal key (key is NOT copied)
ddwaf_object_insert_literal_key = ctypes.CFUNCTYPE(
    ddwaf_object_p, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32, ddwaf_allocator
)(
    ("ddwaf_object_insert_literal_key", ddwaf),
    (
        (1, "map"),
        (1, "key"),
        (1, "length"),
        (1, "alloc"),
    ),
)

# ============================================================================
# C Function Bindings — Object inspection (v2: required since struct is opaque)
# ============================================================================

ddwaf_object_get_type = ctypes.CFUNCTYPE(ctypes.c_int, ddwaf_object_p)(
    ("ddwaf_object_get_type", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_size = ctypes.CFUNCTYPE(ctypes.c_size_t, ddwaf_object_p)(
    ("ddwaf_object_get_size", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_length = ctypes.CFUNCTYPE(ctypes.c_size_t, ddwaf_object_p)(
    ("ddwaf_object_get_length", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_string = ctypes.CFUNCTYPE(ctypes.c_char_p, ddwaf_object_p, ctypes.POINTER(ctypes.c_size_t))(
    ("ddwaf_object_get_string", ddwaf),
    (
        (1, "object"),
        (1, "length"),
    ),
)

ddwaf_object_get_unsigned = ctypes.CFUNCTYPE(ctypes.c_uint64, ddwaf_object_p)(
    ("ddwaf_object_get_unsigned", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_signed = ctypes.CFUNCTYPE(ctypes.c_int64, ddwaf_object_p)(
    ("ddwaf_object_get_signed", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_float = ctypes.CFUNCTYPE(ctypes.c_double, ddwaf_object_p)(
    ("ddwaf_object_get_float", ddwaf),
    ((1, "object"),),
)

ddwaf_object_get_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_object_p)(
    ("ddwaf_object_get_bool", ddwaf),
    ((1, "object"),),
)

# Access array/map element by index (returns value object)
ddwaf_object_at_value = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_size_t)(
    ("ddwaf_object_at_value", ddwaf),
    (
        (1, "object"),
        (1, "index"),
    ),
)

# Access map key object by index (returns key as a ddwaf_object — read via ddwaf_object_get_string)
ddwaf_object_at_key = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_size_t)(
    ("ddwaf_object_at_key", ddwaf),
    (
        (1, "object"),
        (1, "index"),
    ),
)

# ============================================================================
# C Function Bindings — Object destruction & JSON parsing
# ============================================================================

ddwaf_object_destroy = ctypes.CFUNCTYPE(None, ddwaf_object_p, ddwaf_allocator)(
    ("ddwaf_object_destroy", ddwaf),
    (
        (1, "object"),
        (1, "alloc"),
    ),
)

ddwaf_object_from_json = ctypes.CFUNCTYPE(
    ctypes.c_bool, ddwaf_object_p, ctypes.c_char_p, ctypes.c_uint32, ddwaf_allocator
)(
    ("ddwaf_object_from_json", ddwaf),
    (
        (3, "output"),
        (1, "json_str"),
        (1, "length"),
        (1, "alloc"),
    ),
)

# ============================================================================
# C Function Bindings — Utility
# ============================================================================

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


# ============================================================================
# Python wrapper functions (keep ctypes details out of waf.py)
# ============================================================================

from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_builder_capsule  # noqa: E402
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule  # noqa: E402
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_handle_capsule  # noqa: E402
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_subcontext_capsule  # noqa: E402


def py_ddwaf_known_addresses(handle: ddwaf_handle_capsule) -> list[str]:
    size = ctypes.c_uint32()
    obj = ddwaf_known_addresses(handle.handle, size)
    return [obj[i].decode("UTF-8") for i in range(size.value)]


def py_ddwaf_builder_init() -> ddwaf_builder_capsule:
    return ddwaf_builder_capsule(ddwaf_builder_init(), ddwaf_builder_destroy)


def py_add_or_update_config(
    builder: ddwaf_builder_capsule, path: str, config: "ddwaf_object", diagnostics: "ddwaf_object"
) -> bool:
    bin_path = path.encode()
    return ddwaf_builder_add_or_update_config(builder.builder, bin_path, len(bin_path), config, diagnostics)


def py_remove_config(builder: ddwaf_builder_capsule, path: str) -> bool:
    bin_path = path.encode()
    return ddwaf_builder_remove_config(builder.builder, bin_path, len(bin_path))


def py_ddwaf_builder_build_instance(builder: ddwaf_builder_capsule) -> ddwaf_handle_capsule:
    return ddwaf_handle_capsule(ddwaf_builder_build_instance(builder.builder), ddwaf_destroy)


def py_ddwaf_builder_get_config_paths(builder: ddwaf_builder_capsule, filter_str: str) -> int:
    return ddwaf_builder_get_config_paths(builder.builder, None, filter_str.encode(), len(filter_str))


def py_ddwaf_context_init(handle: ddwaf_handle_capsule) -> ddwaf_context_capsule:
    return ddwaf_context_capsule(ddwaf_context_init(handle.handle, DEFAULT_ALLOCATOR), ddwaf_context_destroy)


def py_ddwaf_subcontext_init(ctx: ddwaf_context_capsule) -> ddwaf_subcontext_capsule:
    return ddwaf_subcontext_capsule(ddwaf_subcontext_init(ctx.ctx), ddwaf_subcontext_destroy)


# ============================================================================
# ddwaf_object: Python ↔ C conversion
# ============================================================================

_STRING_TYPES = frozenset({
    DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING,
    DDWAF_OBJ_TYPE.DDWAF_OBJ_LITERAL_STRING,
    DDWAF_OBJ_TYPE.DDWAF_OBJ_SMALL_STRING,
})


def _build_sequence(
    target: ddwaf_object_p,
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    if max_depth <= 0:
        observator.set_container_depth(DDWAF_MAX_CONTAINER_DEPTH)
        max_objects = 0
    length = len(struct)
    capacity = min(length, max_objects, 0xFFFF)
    ddwaf_object_set_array(target, capacity, DEFAULT_ALLOCATOR)
    for counter_object, elt in enumerate(struct):
        if counter_object >= max_objects:
            observator.set_container_size(length)
            break
        child_ptr = ddwaf_object_insert(target, DEFAULT_ALLOCATOR)
        _build_ddwaf_object(child_ptr, elt, observator, max_objects, max_depth - 1, max_string_length)


def _build_mapping(
    target: ddwaf_object_p,
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    if max_depth <= 0:
        observator.set_container_depth(DDWAF_MAX_CONTAINER_DEPTH)
        max_objects = 0
    length = len(struct)
    capacity = min(length, max_objects, 0xFFFF)
    ddwaf_object_set_map(target, capacity, DEFAULT_ALLOCATOR)
    counter_object = 0
    for key, val in struct.items():
        kt = type(key)
        if kt is str:
            res_key = key.encode("UTF-8", errors="ignore")
        elif kt is bytes:
            res_key = key
        else:
            continue
        if counter_object >= max_objects:
            observator.set_container_size(length)
            break
        if len(res_key) > max_string_length:
            observator.set_string_length(len(res_key))
            res_key = res_key[:max_string_length]
        child_ptr = ddwaf_object_insert_key(target, res_key, len(res_key), DEFAULT_ALLOCATOR)
        _build_ddwaf_object(child_ptr, val, observator, max_objects, max_depth - 1, max_string_length)
        counter_object += 1


def _build_ddwaf_object(
    target: ddwaf_object_p,
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
        _build_mapping(target, struct, observator, max_objects, max_depth, max_string_length)
    elif t is str:
        encoded = struct.encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        ddwaf_object_set_string(target, encoded, len(encoded), DEFAULT_ALLOCATOR)
    elif t is list:
        _build_sequence(target, struct, observator, max_objects, max_depth, max_string_length)
    elif t is int:
        ddwaf_object_set_signed(target, struct)
    elif t is bool:
        ddwaf_object_set_bool(target, struct)
    elif t is float:
        ddwaf_object_set_float(target, struct)
    elif t is bytes:
        if len(struct) > max_string_length:
            observator.set_string_length(len(struct))
            struct = struct[:max_string_length]
        ddwaf_object_set_string(target, struct, len(struct), DEFAULT_ALLOCATOR)
    elif struct is None:
        ddwaf_object_set_null(target)
    elif isinstance(struct, Sequence):
        _build_sequence(target, struct, observator, max_objects, max_depth, max_string_length)
    elif isinstance(struct, Mapping):
        _build_mapping(target, struct, observator, max_objects, max_depth, max_string_length)
    else:
        encoded = str(struct).encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        ddwaf_object_set_string(target, encoded, len(encoded), DEFAULT_ALLOCATOR)


def _object_to_python(obj_ptr: ddwaf_object_p) -> DDWafRulesType:
    """Convert a ddwaf_object pointer to a Python structure using v2 getter functions."""
    obj_type = ddwaf_object_get_type(obj_ptr)
    if obj_type in _STRING_TYPES:
        length = ctypes.c_size_t()
        s = ddwaf_object_get_string(obj_ptr, ctypes.byref(length))
        if s is None:
            return None
        return s[: length.value].decode("UTF-8", errors="ignore")
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP:
        size = ddwaf_object_get_size(obj_ptr)
        result = {}
        for i in range(size):
            key_obj = ddwaf_object_at_key(obj_ptr, i)
            val_obj = ddwaf_object_at_value(obj_ptr, i)
            if key_obj and val_obj:
                key_len = ctypes.c_size_t()
                key_str = ddwaf_object_get_string(key_obj, ctypes.byref(key_len))
                if key_str is not None:
                    key = key_str[: key_len.value].decode("UTF-8", errors="ignore")
                    result[key] = _object_to_python(val_obj)
        return result
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY:
        size = ddwaf_object_get_size(obj_ptr)
        return [_object_to_python(ddwaf_object_at_value(obj_ptr, i)) for i in range(size)]
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_SIGNED:
        return ddwaf_object_get_signed(obj_ptr)
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_UNSIGNED:
        return ddwaf_object_get_unsigned(obj_ptr)
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_BOOL:
        return ddwaf_object_get_bool(obj_ptr)
    if obj_type == DDWAF_OBJ_TYPE.DDWAF_OBJ_FLOAT:
        return ddwaf_object_get_float(obj_ptr)
    if obj_type in (DDWAF_OBJ_TYPE.DDWAF_OBJ_NULL, DDWAF_OBJ_TYPE.DDWAF_OBJ_INVALID):
        return None
    log.debug("ddwaf_object struct: unknown object type: %s", obj_type)
    return None


# Add methods to ddwaf_object after all C bindings are defined

def _ddwaf_object_init(
    self: ddwaf_object,
    struct: Optional[DDWafRulesType] = None,
    observator: Optional[_observator] = None,
    max_objects: int = DDWAF_MAX_CONTAINER_SIZE,
    max_depth: int = DDWAF_MAX_CONTAINER_DEPTH,
    max_string_length: int = DDWAF_MAX_STRING_LENGTH,
) -> None:
    if observator is None:
        observator = _observator()
    _build_ddwaf_object(ctypes.pointer(self), struct, observator, max_objects, max_depth, max_string_length)


@classmethod  # type: ignore[misc]
def _ddwaf_object_create_without_limits(cls, struct: DDWafRulesType) -> ddwaf_object:
    return cls(struct, max_objects=DDWAF_NO_LIMIT, max_depth=DDWAF_DEPTH_NO_LIMIT, max_string_length=DDWAF_NO_LIMIT)


@classmethod  # type: ignore[misc]
def _ddwaf_object_from_json_bytes(cls, data: bytes) -> Optional[ddwaf_object]:
    """Create a ddwaf_object from a JSON string as bytes."""
    obj = cls.__new__(cls)
    if not ddwaf_object_from_json(obj, data, len(data), DEFAULT_ALLOCATOR):
        log.debug("Failed to create ddwaf_object from JSON: %s", data)
        return None
    return obj


@property
def _ddwaf_object_struct(self: ddwaf_object) -> DDWafRulesType:
    """Generate a Python structure from ddwaf_object."""
    return _object_to_python(ctypes.pointer(self))


def _ddwaf_object_repr(self: ddwaf_object) -> str:
    return repr(self.struct)


ddwaf_object.__init__ = _ddwaf_object_init  # type: ignore[misc]
ddwaf_object.create_without_limits = _ddwaf_object_create_without_limits  # type: ignore[attr-defined]
ddwaf_object.from_json_bytes = _ddwaf_object_from_json_bytes  # type: ignore[attr-defined]
ddwaf_object.struct = _ddwaf_object_struct  # type: ignore[assignment]
ddwaf_object.__repr__ = _ddwaf_object_repr  # type: ignore[method-assign]
