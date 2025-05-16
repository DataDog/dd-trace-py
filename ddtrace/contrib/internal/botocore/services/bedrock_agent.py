import sys

import wrapt

from ddtrace import config
from ddtrace.contrib.internal.httplib.patch import span_name
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name


class TracedBotocoreEventStream(wrapt.ObjectProxy):
    """
    This class wraps the stream response returned by converse_stream.
    """

    def __init__(self, wrapped, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._stream_chunks = []
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                self._stream_chunks.append(chunk)
                yield chunk
        except GeneratorExit:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if self._dd_span.finished:
                return
            traces, chunks = extract_traces_response_from_chunks(self._stream_chunks)
            self._dd_integration._translate_bedrock_traces(traces, chunks, self._dd_span)
            self._dd_integration._llmobs_set_tags_agent(self._dd_span, self._args, self._kwargs)
            self._dd_span.finish()


def extract_traces_response_from_chunks(chunks):
    traces = []
    response = []
    for chunk in chunks:
        if "chunk" in chunk:
            response.append(chunk["chunk"])
        elif "trace" in chunk:
            traces.append(chunk["trace"])
    return traces, response


def handle_bedrock_agent_response(result, integration, span, args, kwargs):
    completion = result["completion"]
    result["completion"] = TracedBotocoreEventStream(completion, integration, span, args, kwargs)
    return result


def patched_bedrock_agent_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    integration = function_vars.get("integration")
    span = integration.trace(
        pin,
        schematize_service_name(
            "{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))
        ),
        span_name="{}.{}".format(function_vars.get("endpoint_name", ""), function_vars.get("operation", "")),
        submit_to_llmobs=True,
        interface_type="agent",
    )
    try:
        result = original_func(*args, **kwargs)
        result = handle_bedrock_agent_response(result, integration, span, args, kwargs)
        return result
    except Exception:
        integration._llmobs_set_tags_agent(span, args, kwargs)
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise
