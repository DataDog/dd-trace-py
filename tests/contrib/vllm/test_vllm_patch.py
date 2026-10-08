import pytest

from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.contrib.internal.vllm.patch import _uses_input_processor
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch


pytestmark = pytest.mark.no_gpu


def _installed_processor_cls():
    import vllm

    if _uses_input_processor():
        return vllm.v1.engine.input_processor.InputProcessor
    return vllm.v1.engine.processor.Processor


def test_patch_wraps_processor_process_inputs():
    """patch()/unpatch() must wrap/unwrap process_inputs on whichever
    processor class the installed vLLM version exposes.

    Before the fix, patch() hard-referenced vllm.v1.engine.processor and
    raised ModuleNotFoundError on vLLM >= 0.14.0.
    """
    patch()
    try:
        processor_cls = _installed_processor_cls()
        assert iswrapped(processor_cls, "process_inputs")
    finally:
        unpatch()
    assert not iswrapped(processor_cls, "process_inputs")
