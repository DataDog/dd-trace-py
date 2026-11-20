import sys
from typing import Any

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.botocore.constants import BEDROCK_AGENT_TRACES
from ddtrace.contrib.internal.botocore.constants import BEDROCK_COMPONENT
from ddtrace.contrib.internal.botocore.services.bedrock import _end_request
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.wrapt_typing import ObjectProxy


class TracedBotocoreEventStream(ObjectProxy):
    """This class wraps the stream response returned by invoke_agent."""

    def __init__(self, wrapped, ctx: core.ExecutionContext[LlmRequestEvent], event: LlmRequestEvent):
        super().__init__(wrapped)
        self._stream_chunks: list[dict[str, Any]] = []
        self._dd_ctx = ctx
        self._dd_event = event
        self._dd_span = span_from_context(ctx)

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                self._stream_chunks.append(chunk)
                yield chunk
        except (GeneratorExit, Exception):
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if not self._dd_span.finished:
                traces, chunks = _extract_traces_response_from_chunks(self._stream_chunks)
                # Products translate the agent's trace events into child spans when the span finishes.
                self._dd_span._set_ctx_item(BEDROCK_AGENT_TRACES, traces)
                _end_request(self._dd_ctx, self._dd_event, _process_streamed_response_chunks(chunks))


def _extract_traces_response_from_chunks(chunks):
    traces = []
    response = []
    if not chunks or not isinstance(chunks, list):
        return traces, response
    for chunk in chunks:
        if "chunk" in chunk:
            response.append(chunk["chunk"])
        elif "trace" in chunk:
            traces.append(chunk["trace"])
    return traces, response


def _process_streamed_response_chunks(chunks):
    if not chunks:
        return ""
    resp = ""
    for chunk in chunks:
        if not isinstance(chunk, dict) or "bytes" not in chunk:
            continue
        parsed_chunk = chunk["bytes"].decode("utf-8")
        resp += str(parsed_chunk)
    return resp


def patched_bedrock_agents_api_call(original_func, instance, args, kwargs, function_vars):
    agent_id = function_vars.get("params", {}).get("agentId", "")
    event = LlmRequestEvent(
        component=BEDROCK_COMPONENT,
        integration_config=config.botocore,
        service=int_service(None, config.botocore),
        resource=schematize_service_name(
            "{}.{}".format(ext_service(None, int_config=config.botocore), function_vars.get("endpoint_name"))
        ),
        span_name=f"Bedrock Agent {agent_id}",
        provider=None,
        submit_to_llmobs=True,
        request_args=args,
        request_kwargs=kwargs,
        operation="agent",
    )
    result = None
    # The span is finished once the caller consumes the completion stream, or here on error.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = original_func(*args, **kwargs)
            result["completion"] = TracedBotocoreEventStream(result["completion"], ctx, event)
            return result
        except Exception:
            _end_request(ctx, event, result, exc_info=sys.exc_info())
            raise
