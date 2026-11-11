"""Regression tests for APPSEC-70496: unhashable objects reusing the address of a freed tainted string.

The taint map is keyed by object address, so a freed tainted string or bytes object leaves a stale entry
behind. Before dd-trace-py 4.15 (#19749), an aspect that met an unhashable object at that address tried to
hash it and returned with the TypeError still set, surfacing in application code as "unhashable type:
'google._upb._message.RepeatedCompositeContainer'" / "'numpy.ndarray'" or as "SystemError: ... returned a
result with an exception set". Each test forces real allocator reuse and checks every colliding object.
"""

import os
import sys
import sysconfig

from google.protobuf import field_mask_pb2
from google.protobuf import struct_pb2
import numpy as np
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_unhashable_containers", should_patch_iast=True)

# Enough allocations to reuse thousands of freed addresses locally; varied lengths cover several size classes.
_ALLOCATIONS = 10_000
# Allocator reuse depends on the heap state earlier tests leave behind, so retry before declaring a miss.
_ROUNDS = 5
# Only pymalloc's per-size-class free lists hand freed addresses back predictably; elsewhere a miss is not a failure.
_PREDICTABLE_REUSE = (
    os.environ.get("PYTHONMALLOC", "pymalloc") in ("pymalloc", "default")
    and not sysconfig.get_config_var("Py_GIL_DISABLED")
    and not hasattr(sys, "gettotalrefcount")
)


def _plant_stale_taint_entries():
    # Sources stay alive until the request ends, so stale entries come from strings propagated from them.
    # Bytes too: every planted str is over 48 bytes, so only short bytes share a size class with the upb containers.
    str_source = taint_pyobject("source", "param", "value", OriginType.PARAMETER)
    bytes_source = taint_pyobject(b"source", "param", "value", OriginType.PARAMETER)
    tainted = []
    for i in range(_ALLOCATIONS):
        suffix = f"{i}:" + "x" * (i % 200)
        tainted.append(mod.concat(str_source, suffix) if i % 2 else mod.concat(bytes_source, suffix.encode()))
    # Without an active IAST context nothing is tainted and every test below would pass vacuously.
    assert is_pyobject_tainted(tainted[-1]) and is_pyobject_tainted(tainted[-2])
    stale_ids = {id(s) for s in tainted}
    del tainted
    return stale_ids


def _objects_on_stale_addresses(factory):
    for _ in range(_ROUNDS):
        stale_ids = _plant_stale_taint_entries()
        # Hold every candidate during the scan so each allocation takes a new slot, not the one just freed.
        candidates = [factory() for _ in range(_ALLOCATIONS)]
        colliding = [obj for obj in candidates if id(obj) in stale_ids]
        if colliding:
            return colliding
    if not _PREDICTABLE_REUSE:
        pytest.skip("this allocator reused no freed tainted-string address")
    pytest.fail("the allocator reused no freed tainted-string address, so nothing was exercised")


def _repeated_composite_container():
    values = struct_pb2.ListValue().values
    values.add(string_value="a")
    return values


def _empty_repeated_composite_container():
    # Its items are messages, so str.join only succeeds on an empty one.
    return struct_pb2.ListValue().values


def _repeated_scalar_container():
    return field_mask_pb2.FieldMask(paths=["a", "b"]).paths


def _protobuf_message():
    message = struct_pb2.Struct()
    message["key"] = "a"
    return message


def _numpy_str_array():
    return np.array(["a", "b"])


SUBSCRIPT_CASES = [
    pytest.param(
        _repeated_composite_container, 0, struct_pb2.Value(string_value="a"), id="protobuf-RepeatedCompositeContainer"
    ),
    pytest.param(_repeated_scalar_container, 0, "a", id="protobuf-RepeatedScalarContainer"),
    pytest.param(_protobuf_message, "key", "a", id="protobuf-Message"),
    pytest.param(_numpy_str_array, 0, "a", id="numpy-ndarray"),
]
JOIN_CASES = [
    pytest.param(_empty_repeated_composite_container, "", id="protobuf-RepeatedCompositeContainer-empty"),
    pytest.param(_repeated_scalar_container, "a,b", id="protobuf-RepeatedScalarContainer"),
    pytest.param(_numpy_str_array, "a,b", id="numpy-ndarray"),
]


@pytest.mark.parametrize("factory,key,item", SUBSCRIPT_CASES)
def test_subscript_on_unhashable_object_at_stale_address(factory, key, item):
    for obj in _objects_on_stale_addresses(factory):
        # Indexed directly, the object is index_aspect's lookup candidate; as a dict value, the dict branch reaches it.
        assert mod.subscript(obj, key) == item
        assert mod.subscript({"key": obj}, "key") is obj


@pytest.mark.parametrize("factory,joined", JOIN_CASES)
def test_join_over_unhashable_iterable_at_stale_address(factory, joined):
    for obj in _objects_on_stale_addresses(factory):
        assert mod.join_items(",", obj) == joined
