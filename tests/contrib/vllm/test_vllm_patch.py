import importlib

import pytest

from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.contrib.internal.vllm._constants import PROCESSOR_METHOD
from ddtrace.contrib.internal.vllm.patch import _resolve_processor_target
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch


pytestmark = pytest.mark.no_gpu


def test_resolve_processor_target_module_importable():
    """The resolved processor module must actually import on the installed vLLM version.

    vLLM >= 0.14.0 removed vllm.v1.engine.processor and moved Processor to
    vllm.v1.engine.input_processor.InputProcessor. Regression guard for the
    resolver picking a module path that no longer exists.
    """
    module_path, target = _resolve_processor_target()
    module = importlib.import_module(module_path)
    class_name, method_name = target.split(".")
    assert hasattr(module, class_name)
    assert method_name == PROCESSOR_METHOD


def test_patch_wraps_processor_process_inputs():
    """patch()/unpatch() must wrap/unwrap process_inputs on whichever
    processor class the installed vLLM version exposes.

    Before the fix, patch() hard-referenced vllm.v1.engine.processor and
    raised ModuleNotFoundError on vLLM >= 0.14.0.
    """
    module_path, target = _resolve_processor_target()
    class_name, method_name = target.split(".")
    processor_cls = getattr(importlib.import_module(module_path), class_name)

    patch()
    try:
        assert iswrapped(processor_cls, method_name)
    finally:
        unpatch()
    assert not iswrapped(processor_cls, method_name)
