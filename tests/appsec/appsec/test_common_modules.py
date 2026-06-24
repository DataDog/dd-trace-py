import builtins
import copy
import types

import pytest
from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._common_module_patches import wrapped_urllib3_urlopen
from ddtrace.internal import core


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

        assert type(open) == FunctionWrapper
        assert isinstance(copy_open, FunctionWrapper)
        assert isinstance(open, FunctionWrapper)
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

        assert type(original_func) == FunctionWrapper
        assert isinstance(copy_func, FunctionWrapper)
        assert isinstance(original_func, FunctionWrapper)
        assert hasattr(original_func, "__wrapped__")
    finally:
        try_unwrap("builtins", builtin_function_name)


@pytest.mark.parametrize(
    "args,kwargs,expected_url",
    [
        # urllib3 internal redirect handling re-invokes urlopen(method, url, body, ...) positionally:
        # the redirected target URL must be captured from positional arg 1, not the body at arg 2.
        (("GET", "http://attacker.example/redirected", b"the-body"), {}, "http://attacker.example/redirected"),
        (("POST", "http://attacker.example/redirected"), {}, "http://attacker.example/redirected"),
        # libraries such as requests call urlopen with keyword arguments only.
        ((), {"method": "GET", "url": "http://attacker.example/kw", "body": b"the-body"}, "http://attacker.example/kw"),
    ],
)
def test_wrapped_urllib3_urlopen_captures_redirect_url(args, kwargs, expected_url):
    """Regression test for APPSEC-68569: the SSRF/API10 wrapper must store the request URL
    (positional arg 1 or the ``url`` kwarg) in core, never the request body.
    """
    captured = {}

    def fake_urlopen(*a, **k):
        captured["full_url"] = core.find_item("full_url")
        return "response"

    core.discard_item("full_url")
    try:
        wrapped_urllib3_urlopen(fake_urlopen, None, args, kwargs)
        assert captured["full_url"] == expected_url
    finally:
        core.discard_item("full_url")
