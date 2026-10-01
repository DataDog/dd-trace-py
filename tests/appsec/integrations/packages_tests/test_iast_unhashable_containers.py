"""Regression tests for APPSEC-70496: unhashable objects reusing the address of a freed tainted string.

The taint map is keyed by object address, so a freed tainted string or bytes object leaves a stale entry
behind. Before dd-trace-py 4.15 (#19749), an aspect that met an unhashable object at that address tried to
hash it and returned with the TypeError still set, surfacing in application code as "unhashable type:
'google._upb._message.RepeatedCompositeContainer'" / "'numpy.ndarray'" or as "SystemError: ... returned a
result with an exception set". Each test forces real allocator reuse and checks every colliding object.
"""

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


def _plant_stale_taint_entries():
    # Sources stay alive until the request ends, so stale entries come from strings propagated from them.
    # Bytes too: on Python < 3.12 no str fits the 48-byte size class of the upb containers, but short bytes do.
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
    # Keep every candidate alive so the colliding ones cannot be freed and reused again mid-test.
    candidates = []
    colliding = []
    for _ in range(_ROUNDS):
        stale_ids = _plant_stale_taint_entries()
        new_candidates = [factory() for _ in range(_ALLOCATIONS)]
        candidates.extend(new_candidates)
        colliding.extend(obj for obj in new_candidates if id(obj) in stale_ids)
        if colliding:
            break
    assert colliding, "the allocator reused no freed tainted-string address, so nothing was exercised"
    return candidates, colliding


def _repeated_composite_container():
    return struct_pb2.ListValue().values


def _repeated_scalar_container():
    return field_mask_pb2.FieldMask(paths=["a", "b"]).paths


def _protobuf_message():
    return struct_pb2.Value()


def _numpy_str_array():
    return np.array(["a", "b"])


UNHASHABLE_FACTORIES = [
    pytest.param(_repeated_composite_container, id="protobuf-RepeatedCompositeContainer"),
    pytest.param(_repeated_scalar_container, id="protobuf-RepeatedScalarContainer"),
    pytest.param(_protobuf_message, id="protobuf-Message"),
    pytest.param(_numpy_str_array, id="numpy-ndarray"),
]


@pytest.mark.parametrize("factory", UNHASHABLE_FACTORIES)
def test_subscript_returning_unhashable_value_at_stale_address(factory):
    _, colliding = _objects_on_stale_addresses(factory)

    for obj in colliding:
        assert mod.subscript({"key": obj}, "key") is obj
        assert mod.enumerate_pairs([("key", obj)]) == ["key"]


@pytest.mark.parametrize(
    "factory,expected",
    [
        pytest.param(_repeated_composite_container, "", id="protobuf-RepeatedCompositeContainer"),
        pytest.param(_repeated_scalar_container, "a,b", id="protobuf-RepeatedScalarContainer"),
        pytest.param(_numpy_str_array, "a,b", id="numpy-ndarray"),
    ],
)
def test_join_over_unhashable_iterable_at_stale_address(factory, expected):
    _, colliding = _objects_on_stale_addresses(factory)

    for obj in colliding:
        assert mod.join_items(",", obj) == expected
        assert mod.enumerate_pairs([("key", obj)]) == ["key"]
