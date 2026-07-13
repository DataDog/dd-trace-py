from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.appsec._utils import _observator
from ddtrace.internal.native._native import appsec as native_appsec
from ddtrace.internal.settings.asm import config as asm_config


DDWafRulesType = Union[None, int, str, list[Any], dict[str, Any]]

DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
DDWAF_MAX_CONTAINER_SIZE = 256
DDWAF_NO_LIMIT = 1 << 31
DDWAF_DEPTH_NO_LIMIT = 1000

# Containers store their size/capacity in a uint16, so they cannot hold more than this.
DDWAF_OBJ_MAX_CAPACITY = 0xFFFF

_UINT64_MASK = (1 << 64) - 1
_INT64_SIGN_BIT = 1 << 63

_native_libddwaf = native_appsec.libddwaf

# AIDEV-NOTE: Object storage, ownership, and conversion back to Python live in Rust. Recursive
# serialization intentionally remains here so this migration does not move DDWaf business logic.


def build_ddwaf_object(
    struct: DDWafRulesType,
    observator: Optional[_observator] = None,
    max_objects: int = DDWAF_MAX_CONTAINER_SIZE,
    max_depth: int = DDWAF_MAX_CONTAINER_DEPTH,
    max_string_length: int = DDWAF_MAX_STRING_LENGTH,
) -> Any:
    """Serialize a Python value into a Rust-owned ``DDWafObject``."""
    if observator is None:
        observator = _observator()
    obj = _native_libddwaf.DDWafObject()
    _build_ddwaf_object(obj, struct, observator, max_objects, max_depth, max_string_length)
    return obj


def build_ddwaf_object_without_limits(struct: DDWafRulesType) -> Any:
    return build_ddwaf_object(
        struct,
        max_objects=DDWAF_NO_LIMIT,
        max_depth=DDWAF_DEPTH_NO_LIMIT,
        max_string_length=DDWAF_NO_LIMIT,
    )


def _build_sequence(
    target: Any,
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
        max_objects = DDWAF_OBJ_MAX_CAPACITY
    capacity = min(len(struct), max_objects)
    if not _native_libddwaf.ddwaf_object_set_array(target, capacity):
        raise ValueError("failed to create a libddwaf array")
    for counter_object, element in enumerate(struct):
        if counter_object >= max_objects:
            observator.set_container_size(len(struct))
            break
        slot = _native_libddwaf.ddwaf_object_insert(target)
        if slot is None:
            raise ValueError("failed to insert a libddwaf array element")
        _build_ddwaf_object(slot, element, observator, max_objects, max_depth - 1, max_string_length)


def _build_mapping(
    target: Any,
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
        max_objects = DDWAF_OBJ_MAX_CAPACITY
    capacity = min(len(struct), max_objects)
    if not _native_libddwaf.ddwaf_object_set_map(target, capacity):
        raise ValueError("failed to create a libddwaf map")
    counter_object = 0
    for key, value in struct.items():
        key_type = type(key)
        if key_type is str:
            encoded_key = key.encode("UTF-8", errors="ignore")
        elif key_type is bytes:
            encoded_key = key
        else:
            continue
        if counter_object >= max_objects:
            observator.set_container_size(len(struct))
            break
        if len(encoded_key) > max_string_length:
            observator.set_string_length(len(encoded_key))
            encoded_key = encoded_key[:max_string_length]
        slot = _native_libddwaf.ddwaf_object_insert_key(target, encoded_key)
        if slot is None:
            raise ValueError("failed to insert a libddwaf map element")
        _build_ddwaf_object(slot, value, observator, max_objects, max_depth - 1, max_string_length)
        counter_object += 1


def _to_int64(value: int) -> int:
    value &= _UINT64_MASK
    return value - (1 << 64) if value & _INT64_SIGN_BIT else value


def _build_ddwaf_object(
    target: Any,
    struct: Any,
    observator: _observator,
    max_objects: int,
    max_depth: int,
    max_string_length: int,
) -> None:
    """Populate a native DDWafObject while preserving the existing Python serialization rules."""
    value_type = type(struct)
    if value_type is dict:
        _build_mapping(target, struct, observator, max_objects, max_depth, max_string_length)
    elif value_type is str:
        encoded = struct.encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        _native_libddwaf.ddwaf_object_set_string(target, encoded)
    elif value_type is list:
        _build_sequence(target, struct, observator, max_objects, max_depth, max_string_length)
    elif value_type is int:
        _native_libddwaf.ddwaf_object_set_signed(target, _to_int64(struct))
    elif value_type is bool:
        _native_libddwaf.ddwaf_object_set_bool(target, struct)
    elif value_type is float:
        _native_libddwaf.ddwaf_object_set_float(target, struct)
    elif value_type is bytes:
        if len(struct) > max_string_length:
            observator.set_string_length(len(struct))
            struct = struct[:max_string_length]
        _native_libddwaf.ddwaf_object_set_string(target, struct)
    elif struct is None:
        _native_libddwaf.ddwaf_object_set_null(target)
    elif isinstance(struct, Sequence):
        _build_sequence(target, struct, observator, max_objects, max_depth, max_string_length)
    elif isinstance(struct, Mapping):
        _build_mapping(target, struct, observator, max_objects, max_depth, max_string_length)
    else:
        encoded = str(struct).encode("UTF-8", errors="ignore")
        if len(encoded) > max_string_length:
            observator.set_string_length(len(encoded))
            encoded = encoded[:max_string_length]
        _native_libddwaf.ddwaf_object_set_string(target, encoded)


asm_config._ddwaf_version = _native_libddwaf.ddwaf_get_version()
