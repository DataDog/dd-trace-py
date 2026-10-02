from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.contrib.internal.vllm.patch import _uses_input_processor
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import traced_output_processor_process_outputs
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


@pytest.mark.parametrize(
    "failing_helper",
    ["_capture_request_states", "_create_finished_spans"],
)
def test_process_outputs_instrumentation_errors_do_not_propagate(failing_helper):
    """process_outputs runs in vLLM's engine output loop, where an exception kills the engine.
    Instrumentation failures must be swallowed and the original result returned (MLOS-950).
    """
    instance = SimpleNamespace(request_states={})
    outputs = [SimpleNamespace(request_id="req-0")]
    func = mock.Mock(return_value="result")

    with (
        mock.patch("ddtrace.contrib.internal.vllm.patch.vllm") as vllm_mod,
        mock.patch("ddtrace.contrib.internal.vllm.patch." + failing_helper, side_effect=AttributeError("boom")),
    ):
        vllm_mod._datadog_integration = mock.Mock()
        assert traced_output_processor_process_outputs(func, instance, (outputs,), {}) == "result"

    func.assert_called_once_with(outputs)
