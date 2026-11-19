from unittest import mock

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


def test_patch_wraps_async_llm_add_request():
    """patch()/unpatch() must wrap/unwrap AsyncLLM.add_request.

    Newer vLLM versions (0.27) run process_inputs for raw prompts in a thread pool, where the active span is not
    visible. The add_request wrapper injects the trace context while it is still in the event loop.
    """
    from vllm.v1.engine.async_llm import AsyncLLM

    patch()
    try:
        assert iswrapped(AsyncLLM, "add_request")
    finally:
        unpatch()
    assert not iswrapped(AsyncLLM, "add_request")


@pytest.mark.parametrize("version,injected", [("0.32.0", True), ("0.33.0", False)])
def test_add_request_trace_headers_injection_by_version(version, injected):
    """vLLM >= 0.33 drops add_request trace_headers (and warns), so the wrapper must not inject there."""
    import asyncio

    from ddtrace.contrib.internal.vllm import patch as vllm_patch

    seen = {}

    async def func(*args, **kwargs):
        seen.update(kwargs)

    with (
        mock.patch.object(vllm_patch.vllm, "__version__", version),
        mock.patch.object(vllm_patch, "inject_trace_context", return_value={"x-datadog-trace-id": "1"}),
    ):
        asyncio.run(vllm_patch.traced_async_llm_add_request(func, None, (), {}))

    assert ("trace_headers" in seen) is injected
