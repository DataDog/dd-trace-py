import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

from ddtrace.contrib.internal import vllm as vllm_integration
from ddtrace.propagation.http import HTTPPropagator


@pytest.fixture(scope="module")
def vllm_patch():
    # Load a private copy so the fake module cannot persist in the real integration.
    path = Path(vllm_integration.__file__).with_name("patch.py")
    spec = importlib.util.spec_from_file_location("ddtrace.contrib.internal.vllm._mocked_patch", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(sys.modules, "vllm", ModuleType("vllm"))
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version,inject", [("0.32.0", True), ("0.33.0", False)])
def test_add_request_injects_trace_headers_by_version(vllm_patch, tracer, monkeypatch, version, inject):
    monkeypatch.setattr(vllm_patch, "tracer", tracer)
    monkeypatch.setattr(vllm_patch.vllm, "__version__", version, raising=False)

    async def add_request(**kwargs):
        return kwargs["trace_headers"]

    incoming = {"x-request-id": "test-request"}
    with tracer.trace("caller") as parent:
        headers = asyncio.run(
            vllm_patch.traced_async_llm_add_request(add_request, None, (), {"trace_headers": incoming})
        )

    assert headers["x-request-id"] == "test-request"
    assert incoming == {"x-request-id": "test-request"}
    context = HTTPPropagator.extract(headers)
    assert context.trace_id == (parent.trace_id if inject else None)
    assert context.span_id == (parent.span_id if inject else None)
