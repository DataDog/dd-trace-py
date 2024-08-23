import sys
from typing import AsyncIterable
from typing import Coroutine

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.asyncio_functions")


@pytest.mark.asyncio
async def test_async_function():
    obj1 = taint_pyobject(
        pyobject="my_string_1",
        source_name="test_add_inplace_aspect_tainting_right_hand",
        source_value="my_string_1",
        source_origin=OriginType.PARAMETER,
    )
    obj2 = "my_string_2"

    result = mod.async_function(obj1, obj2)
    assert isinstance(result, Coroutine)

    result = await mod.async_function(obj1, obj2)
    assert isinstance(result, str)

    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 11


def test_no_async_function():
    obj1 = taint_pyobject(
        pyobject="my_string_1",
        source_name="test_add_inplace_aspect_tainting_right_hand",
        source_value="my_string_1",
        source_origin=OriginType.PARAMETER,
    )
    obj2 = "my_string_2"

    result = mod.no_async_function(obj1, obj2)
    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 11


@pytest.mark.skipif(sys.version_info < (3, 10), reason="anext was introduced in version 3.10")
@pytest.mark.asyncio
async def test_async_yield_function():
    obj1 = taint_pyobject(
        pyobject="my_string_1",
        source_name="test_add_inplace_aspect_tainting_right_hand",
        source_value="my_string_1",
        source_origin=OriginType.PARAMETER,
    )

    result = mod.async_yield_function(obj1)

    assert isinstance(result, AsyncIterable)
    async_iterator = mod.async_yield_function(obj1)

    result = await anext(async_iterator)
    assert isinstance(result, str)
    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 1
    assert ranges_result[0].length == 11

    result = await anext(async_iterator)
    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 2
    assert ranges_result[0].length == 11

    with pytest.raises(StopAsyncIteration):
        _ = await anext(async_iterator)


@pytest.mark.skipif(sys.version_info < (3, 10), reason="anext was introduced in version 3.10")
@pytest.mark.asyncio
async def test_async_yield_function_2():
    obj1 = taint_pyobject(
        pyobject="my_string_3",
        source_name="test_add_inplace_aspect_tainting_right_hand",
        source_value="my_string_3",
        source_origin=OriginType.PARAMETER,
    )

    list_1 = ["my_string_1", "my_string_2"]

    async_iterator = mod.async_yield_function_list(list_1)

    result = await anext(async_iterator)
    assert result == ["my_string_1", "my_string_2", "a"]

    list_1.append(obj1)

    result = await anext(async_iterator)
    assert result == ["my_string_1", "my_string_2", "a", obj1, "b"]

    assert is_pyobject_tainted(result[3]) is True
    ranges_result = get_tainted_ranges(result[3])
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 11
