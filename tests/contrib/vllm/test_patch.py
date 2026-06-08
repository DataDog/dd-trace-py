import importlib

import pytest

from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.contrib.internal.vllm._constants import PROCESSOR_METHOD
from ddtrace.contrib.internal.vllm.patch import _processor_class
from ddtrace.contrib.internal.vllm.patch import _resolve_processor_target
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch


@pytest.fixture(autouse=True)
def require_gpu():
    """Shadow the conftest session-scoped GPU gate.

    These patch/unpatch resolution tests exercise pure-Python module/class
    resolution and wrapt wrapping; they need no CUDA device, so they must run
    in the non-GPU CI environment instead of being skipped wholesale.
    """
    yield


def test_resolve_processor_target_module_importable():
    """The resolved processor module imports on the installed vLLM version.

    vLLM >= 0.14.0 renamed ``vllm.v1.engine.processor.Processor`` to
    ``vllm.v1.engine.input_processor.InputProcessor``. The resolver must point
    at a module that actually exists so ``patch()`` no longer raises
    ``ModuleNotFoundError: No module named 'vllm.v1.engine.processor'``.
    """
    module_path, target = _resolve_processor_target()
    module = importlib.import_module(module_path)  # must not raise ModuleNotFoundError
    class_name = target.split(".")[0]
    assert hasattr(module, class_name)
    assert target.endswith("." + PROCESSOR_METHOD)


def test_patch_wraps_processor_process_inputs():
    """patch() wraps the processor's ``process_inputs`` across vLLM versions.

    Regression guard for the vLLM >= 0.14.0 module rename: before the fix,
    ``patch()`` resolved the hard-coded ``vllm.v1.engine.processor`` module and
    crashed the whole integration with ``ModuleNotFoundError``.
    """
    processor_cls = _processor_class()
    patch()
    try:
        assert iswrapped(processor_cls, PROCESSOR_METHOD)
    finally:
        unpatch()
    assert not iswrapped(processor_cls, PROCESSOR_METHOD)
