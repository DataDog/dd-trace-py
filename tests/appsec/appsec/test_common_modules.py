import copy
import types

from wrapt import FunctionWrapper

import ddtrace.appsec._common_module_patches as cmp


def test_patch_read():
    copy_open = copy.deepcopy(open)

    assert copy_open is open
    assert type(open) == types.BuiltinFunctionType
    assert not isinstance(open, FunctionWrapper)
    assert not isinstance(copy_open, FunctionWrapper)
    assert isinstance(open, types.BuiltinFunctionType)


def test_patch_read_enabled():
    original_open = open
    try:
        cmp.patch_common_modules()
        copy_open = copy.deepcopy(open)

        assert type(open) == cmp.DataDogFunctionWrapper
        assert isinstance(copy_open, cmp.DataDogFunctionWrapper)
        assert isinstance(open, cmp.DataDogFunctionWrapper)
        assert hasattr(open, "__wrapped__")
        assert open.__wrapped__ is original_open
    finally:
        cmp.unpatch_common_modules()
