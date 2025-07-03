import sys

import wrapt

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.llmobs._integrations import BedrockIntegration


log = get_logger(__name__)


class TracedBotocoreEventStream(wrapt.ObjectProxy):
    """This class wraps the stream response returned by invoke_agent."""

    def __init__(self, wrapped, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._stream_chunks = []
        self._dd_integration: BedrockIntegration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                self._stream_chunks.append(chunk)
                yield chunk
        except (GeneratorExit, Exception):
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if self._dd_span.finished:
                return
            traces, chunks = _extract_traces_response_from_chunks(self._stream_chunks)
            response = _process_streamed_response_chunks(chunks)
            try:
                self._dd_integration.translate_bedrock_traces(traces, self._dd_span)
            except Exception as e:
                log.error("Error translating Bedrock traces: %s", e, exc_info=True)
            self._dd_integration.llmobs_set_tags(self._dd_span, self._args, self._kwargs, response, operation="agent")
            self._dd_span.finish()


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


def handle_bedrock_agent_response(result, integration, span, args, kwargs):
    completion = result["completion"]
    result["completion"] = TracedBotocoreEventStream(completion, integration, span, args, kwargs)
    return result


def patched_bedrock_agents_api_call(original_func, instance, args, kwargs, function_vars):
    pin = function_vars.get("pin")
    integration = function_vars.get("integration")
    agent_id = function_vars.get("params", {}).get("agentId", "")
    result = None
    span = integration.trace(
        pin,
        schematize_service_name(
            "{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))
        ),
        span_name="Bedrock Agent {}".format(agent_id),
        submit_to_llmobs=True,
        interface_type="agent",
    )
    try:
        result = original_func(*args, **kwargs)
        result = handle_bedrock_agent_response(result, integration, span, args, kwargs)
        return result
    except Exception:
        # We only finish the span if an exception happens, otherwise we'll finish it in the TracedBotocoreEventStream.
        integration.llmobs_set_tags(span, args, kwargs, result, operation="agent")
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise
