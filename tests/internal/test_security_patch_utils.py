import types

import pytest
from wrapt import FunctionWrapper

from ddtrace.internal.security._patch_utils import _DD_ORIGINAL_ATTRIBUTES
from ddtrace.internal.security._patch_utils import try_unwrap
from ddtrace.internal.security._patch_utils import wrap_object


def _module_with_target():
    class Target:
        def method(self):
            return "method"

        @classmethod
        def class_method(cls):
            return cls.__name__

        @staticmethod
        def static_method():
            return "static"

    def function():
        return "function"

    module = types.ModuleType("security_patch_utils_target")
    module.Target = Target
    module.function = function
    return module


def _counting_hook(calls):
    def hook(wrapped, instance, args, kwargs):
        calls.append(wrapped)
        return wrapped(*args, **kwargs)

    return hook


@pytest.mark.parametrize(
    "path,call,expected",
    [
        ("Target.method", lambda module: module.Target().method(), "method"),
        ("Target.class_method", lambda module: module.Target.class_method(), "Target"),
        ("Target.static_method", lambda module: module.Target.static_method(), "static"),
        ("function", lambda module: module.function(), "function"),
    ],
)
def test_unwrap_after_double_patch_restores_original(path, call, expected):
    module = _module_with_target()
    parent = module.Target if path.startswith("Target.") else module
    attribute = path.split(".")[-1]
    original = vars(parent)[attribute]
    calls = []
    hook = _counting_hook(calls)

    wrap_object(module, path, FunctionWrapper, (hook,))
    wrap_object(module, path, FunctionWrapper, (hook,))
    assert _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] is original
    assert call(module) == expected
    assert len(calls) == 1

    try_unwrap(module, path)

    assert vars(parent)[attribute] is original
    assert (parent, attribute) not in _DD_ORIGINAL_ATTRIBUTES
    calls.clear()
    assert call(module) == expected
    assert calls == []


def test_unwrap_after_patching_with_two_hooks_restores_original():
    module = _module_with_target()
    original = vars(module.Target)["method"]
    calls = []

    wrap_object(module, "Target.method", FunctionWrapper, (_counting_hook(calls),))
    wrap_object(module, "Target.method", FunctionWrapper, (_counting_hook(calls),))
    assert module.Target().method() == "method"
    assert len(calls) == 2

    try_unwrap(module, "Target.method")

    assert vars(module.Target)["method"] is original
    calls.clear()
    assert module.Target().method() == "method"
    assert calls == []
