import os

import pytest


_CALLS = {
    "openai": """
import openai

response = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o-mini",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
client = openai.OpenAI(api_key="<not-a-real-key>", http_client=mock_http_client(response))
client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
""",
    "anthropic": """
import anthropic

try:
    # Recent anthropic releases require clients from the httpx2 fork.
    import httpx2 as sdk_http
except ImportError:
    import httpx as sdk_http

response = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-haiku-20240307",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
client = anthropic.Anthropic(api_key="<not-a-real-key>", http_client=mock_http_client(response, sdk_http))
client.messages.create(model="claude-3-haiku-20240307", max_tokens=8, messages=[{"role": "user", "content": "hi"}])
""",
}

_TEST_CODE = """
import sys

import httpx
from wrapt.importer import ImportHookFinder

import ddtrace
from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import _UniversalModuleWatchdog
from ddtrace.llmobs import LLMObs
from ddtrace.trace import TraceFilter


def mock_http_client(response, http=httpx):
    return http.Client(transport=http.MockTransport(lambda request: http.Response(200, json=response)))


enable_iast_propagation()
ddtrace.patch({sdk}=True)
assert "{sdk}" not in sys.modules


# A watchdog registered after the integration hook (e.g. a product enabled through Remote
# Configuration) moves the watchdog finder ahead of wrapt's finder.
class LateWatchdog(ModuleWatchdog):
    pass


LateWatchdog.install()
wrapt_finder = next(f for f in sys.meta_path if isinstance(f, ImportHookFinder))
assert sys.meta_path.index(_UniversalModuleWatchdog._instance) < sys.meta_path.index(wrapt_finder)

spans = []


class CaptureSpans(TraceFilter):
    def process_trace(self, trace):
        spans.extend((span.name, span.get_tag("_dd.llmobs.span_kind")) for span in trace)
        return None


ddtrace.tracer.configure(trace_processors=[CaptureSpans()])
LLMObs.enable(ml_app="iast-llmobs", agentless_enabled=True, api_key="<not-a-real-key>")

{call}

# IAST executed its rewritten code for the SDK, so its pre-exec hook was in the import path.
assert hasattr(sys.modules["{sdk}"], "_ddtrace_aspects"), "{sdk} was not rewritten by IAST"

LLMObs.disable()
assert spans == [("{sdk}.request", "llm")], spans
"""


@pytest.mark.parametrize("sdk", sorted(_CALLS))
def test_llmobs_with_iast_after_watchdog_reorder(sdk, python_process):
    """LLM Obs must trace an SDK that IAST rewrites on import.

    The SDK is allowlisted for IAST, as happens when it is classified as first-party code.
    """
    code = _TEST_CODE.format(sdk=sdk, call=_CALLS[sdk])
    env = os.environ | {"DD_IAST_ENABLED": "true", "DD_LLMOBS_ENABLED": "false", "_DD_IAST_PATCH_MODULES": f"{sdk}."}
    out, err, status, _ = python_process(code, env=env)
    assert status == 0, (out.decode(), err.decode())
