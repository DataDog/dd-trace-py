import builtins
import copy
import types

import pytest
from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import DataDogFunctionWrapper
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import unpatch_common_modules


def test_patch_read():
    unpatch_common_modules()
    copy_open = copy.deepcopy(open)

    assert copy_open is open
    assert type(open) == types.BuiltinFunctionType
    assert not isinstance(open, FunctionWrapper)
    assert not isinstance(copy_open, FunctionWrapper)
    assert isinstance(open, types.BuiltinFunctionType)


def test_patch_read_enabled():
    unpatch_common_modules()
    original_open = open
    try:
        patch_common_modules()
        copy_open = copy.deepcopy(open)

        assert type(open) == DataDogFunctionWrapper
        assert isinstance(copy_open, DataDogFunctionWrapper)
        assert isinstance(open, DataDogFunctionWrapper)
        assert hasattr(open, "__wrapped__")
        assert open.__wrapped__ is original_open
    finally:
        unpatch_common_modules()


@pytest.mark.parametrize(
    "builtin_function_name",
    [
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "breakpoint",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "copyright",
        "credits",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "exit",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "help",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "license",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "quit",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "vars",
        "zip",
    ],
)
def test_other_builtin_functions(builtin_function_name):
    def dummywrapper(callable, instance, args, kwargs):  # noqa: A002
        return callable(*args, **kwargs)

    try:
        try_wrap_function_wrapper("builtins", builtin_function_name, dummywrapper)

        original_func = getattr(builtins, builtin_function_name)
        copy_func = copy.deepcopy(original_func)

        assert type(original_func) == DataDogFunctionWrapper
        assert isinstance(copy_func, DataDogFunctionWrapper)
        assert isinstance(original_func, DataDogFunctionWrapper)
        assert hasattr(original_func, "__wrapped__")
    finally:
        try_unwrap("builtins", builtin_function_name)
