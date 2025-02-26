"""
Trace queries to aws api done via botocore client
"""

import collections
import json
import os
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Set  # noqa:F401
from typing import Union  # noqa:F401

from botocore import __version__
import botocore.client
import botocore.exceptions
import wrapt

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.llmobs._integrations import BedrockIntegration
from ddtrace.settings._config import Config
from ddtrace.trace import Pin

from .services.bedrock import patched_bedrock_api_call
from .services.kinesis import patched_kinesis_api_call
from .services.sqs import patched_sqs_api_call
from .services.sqs import update_messages as inject_trace_to_sqs_or_sns_message
from .services.stepfunctions import patched_stepfunction_api_call
from .services.stepfunctions import update_stepfunction_input
from .utils import update_client_context
from .utils import update_eventbridge_detail


_PATCHED_SUBMODULES = set()  # type: Set[str]

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}
PATCHING_FUNCTIONS = {
    "kinesis": patched_kinesis_api_call,
    "sqs": patched_sqs_api_call,
    "states": patched_stepfunction_api_call,
}

log = get_logger(__name__)


def _load_dynamodb_primary_key_names_for_tables() -> Dict[str, Set[str]]:
    try:
        encoded_table_primary_keys = os.getenv("DD_BOTOCORE_DYNAMODB_TABLE_PRIMARY_KEYS", "{}")
        raw_table_primary_keys = json.loads(encoded_table_primary_keys)

        table_primary_keys = {}
        for table, primary_keys in raw_table_primary_keys.items():
            if not isinstance(table, str):
                raise ValueError(f"expected string table name: {table}")

            if not isinstance(primary_keys, list):
                raise ValueError(f"expected list of primary keys: {primary_keys}")

            unique_primary_keys = set(primary_keys)
            if not len(unique_primary_keys) == len(primary_keys):
                raise ValueError(f"expected unique primary keys: {primary_keys}")

            table_primary_keys[table] = unique_primary_keys

        return table_primary_keys

    except Exception as e:
        log.warning("failed to load DD_BOTOCORE_DYNAMODB_TABLE_PRIMARY_KEYS: %s", e)
        return {}
        
        
def _direct_patched_bedrock_converse(original_func, instance, args, kwargs, function_vars):
    """
    Direct patching function specifically for the Bedrock Converse API to ensure spans are finished.
    """
    # Import here to avoid circular imports
    from ddtrace.contrib.internal.botocore.services.bedrock import _extract_request_params
    from ddtrace.contrib.internal.botocore.services.bedrock import _extract_text_and_response_reason
    from ddtrace.contrib.internal.botocore.services.bedrock import _parse_model_id
    
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    model_id = params.get("modelId")
    model_provider, model_name = _parse_model_id(model_id)
    integration = function_vars.get("integration")
    submit_to_llmobs = integration.llmobs_enabled and "embed" not in model_name
    
    # Create a tracer and span directly
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)
    
    tracer = pin.tracer
    span = tracer.trace(
        function_vars.get("trace_operation"),
        service=schematize_service_name("{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))),
        resource="Converse",
        span_type=SpanTypes.LLM if submit_to_llmobs else None,
    )
    
    # Set span tags
    span.set_tag_str("component", config.botocore.integration_name)
    span.set_tag_str("span.kind", SpanKind.CLIENT)
    span.set_tag_str("bedrock.request.model", model_id)
    span.set_tag_str("bedrock.request.model_provider", model_provider)
    span.set_tag_str("bedrock.request.model", model_name)
    span.set_tag_str("bedrock.operation", "Converse")
    
    # Extract request parameters
    try:
        request_params = _extract_request_params(params, model_provider)
        for k, v in request_params.items():
            if k == "prompt" and integration.is_pc_sampled_llmobs(span):
                # Set prompt-related tags if PC sampled
                if isinstance(v, list):
                    for i, msg in enumerate(v):
                        if isinstance(msg, dict):
                            role = msg.get("role", "")
                            content = msg.get("content", "")
                            span.set_tag_str(f"bedrock.request.messages.{i}.role", role)
                            span.set_tag_str(f"bedrock.request.messages.{i}.content", integration.trunc(str(content)))
                else:
                    span.set_tag_str("bedrock.request.prompt", integration.trunc(str(v)))
            
            # Set other parameter tags
            if k == "temperature" and v != "":
                span.set_tag_str("bedrock.request.temperature", str(v))
            elif k == "top_p" and v != "":
                span.set_tag_str("bedrock.request.top_p", str(v))
            elif k == "max_tokens" and v != "":
                span.set_tag_str("bedrock.request.max_tokens", str(v))
                
        # Set conversation ID if available
        if "conversation_id" in request_params and request_params["conversation_id"]:
            span.set_tag_str("bedrock.conversation_id", str(request_params["conversation_id"]))
            
        # Make the API call
        result = original_func(*args, **kwargs)
        
        # Process the response
        if "output" in result:
            # Extract usage information
            if "usage" in result["output"]:
                usage = result["output"]["usage"]
                if "inputTokens" in usage:
                    span.set_metric("bedrock.response.usage.prompt_tokens", int(usage["inputTokens"]))
                if "outputTokens" in usage:
                    span.set_metric("bedrock.response.usage.completion_tokens", int(usage["outputTokens"]))
                if "inputTokens" in usage and "outputTokens" in usage:
                    span.set_metric("bedrock.response.usage.total_tokens", int(usage["inputTokens"]) + int(usage["outputTokens"]))
            
            # Extract text from the response
            formatted_response = _extract_text_and_response_reason({"params": params, "model_provider": model_provider, "model_name": model_name}, result)
            if integration.is_pc_sampled_llmobs(span) and formatted_response["text"]:
                for i, text in enumerate(formatted_response["text"]):
                    span.set_tag_str(f"bedrock.response.text.{i}", integration.trunc(str(text)))
                    
                for i, reason in enumerate(formatted_response["finish_reason"]):
                    if reason:
                        span.set_tag_str(f"bedrock.response.finish_reason.{i}", str(reason))
            
            # Set LLM Observability tags
            if submit_to_llmobs:
                # Build messages format for LLM Observability
                prompt_messages = []
                if isinstance(request_params.get("prompt"), list):
                    # Structured messages from the request
                    prompt_messages = request_params.get("prompt", [])
                
                # Extract output content from the response
                output_text = ""
                if "output" in result and "message" in result["output"]:
                    message = result["output"]["message"]
                    if "content" in message:
                        if isinstance(message["content"], list):
                            # Handle array content format
                            for content_part in message["content"]:
                                if isinstance(content_part, dict) and "text" in content_part:
                                    output_text += content_part["text"]
                        else:
                            # Handle string content format
                            output_text = message["content"]
                
                # Create custom response format for llmobs
                custom_response = {
                    "text": [output_text],
                    "finish_reason": formatted_response.get("finish_reason", [""])
                }
                
                # Pass the raw messages directly to the integration
                # The integration will extract and format them properly
                integration.llmobs_set_tags(span, args=[], kwargs={"prompt": params.get("messages", [])}, response=result)
        
        # Finish the span
        span.finish()
        return result
        
    except Exception as e:
        # Handle errors
        span.error = 1
        span.set_exc_info(*sys.exc_info())
        
        # Still set LLM Observability tags for errors
        if submit_to_llmobs:
            integration.llmobs_set_tags(span, args=[], kwargs={"prompt": getattr(request_params, "prompt", None)}, response=None)
        
        # Finish the span
        span.finish()
        raise


def _direct_patched_bedrock_converse_stream(original_func, instance, args, kwargs, function_vars):
    """
    Direct patching function specifically for the Bedrock Converse streaming API to ensure spans are finished.
    """
    # Import here to avoid circular imports
    from ddtrace.contrib.internal.botocore.services.bedrock import _extract_request_params
    from ddtrace.contrib.internal.botocore.services.bedrock import _extract_streamed_response
    from ddtrace.contrib.internal.botocore.services.bedrock import _extract_streamed_response_metadata
    from ddtrace.contrib.internal.botocore.services.bedrock import _parse_model_id
    
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    model_id = params.get("modelId")
    model_provider, model_name = _parse_model_id(model_id)
    integration = function_vars.get("integration")
    submit_to_llmobs = integration.llmobs_enabled and "embed" not in model_name
    
    # Create a tracer and span directly
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)
    
    tracer = pin.tracer
    span = tracer.trace(
        function_vars.get("trace_operation"),
        service=schematize_service_name("{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))),
        resource="ConverseStream",
        span_type=SpanTypes.LLM if submit_to_llmobs else None,
    )
    
    # Set span tags
    span.set_tag_str("component", config.botocore.integration_name)
    span.set_tag_str("span.kind", SpanKind.CLIENT)
    span.set_tag_str("bedrock.request.model", model_id)
    span.set_tag_str("bedrock.request.model_provider", model_provider)
    span.set_tag_str("bedrock.request.model", model_name)
    span.set_tag_str("bedrock.operation", "ConverseStream")
    
    # Extract request parameters
    try:
        request_params = _extract_request_params(params, model_provider)
        for k, v in request_params.items():
            if k == "prompt" and integration.is_pc_sampled_llmobs(span):
                # Set prompt-related tags if PC sampled
                if isinstance(v, list):
                    for i, msg in enumerate(v):
                        if isinstance(msg, dict):
                            role = msg.get("role", "")
                            content = msg.get("content", "")
                            span.set_tag_str(f"bedrock.request.messages.{i}.role", role)
                            span.set_tag_str(f"bedrock.request.messages.{i}.content", integration.trunc(str(content)))
                else:
                    span.set_tag_str("bedrock.request.prompt", integration.trunc(str(v)))
            
            # Set other parameter tags
            if k == "temperature" and v != "":
                span.set_tag_str("bedrock.request.temperature", str(v))
            elif k == "top_p" and v != "":
                span.set_tag_str("bedrock.request.top_p", str(v))
            elif k == "max_tokens" and v != "":
                span.set_tag_str("bedrock.request.max_tokens", str(v))
        
        # Set conversation ID if available
        if "conversation_id" in request_params and request_params["conversation_id"]:
            span.set_tag_str("bedrock.conversation_id", str(request_params["conversation_id"]))
            
        # Make the API call
        result = original_func(*args, **kwargs)
        
        # For streaming responses, wrap the stream iterator to collect chunks and finish the span
        if "stream" in result:
            original_stream = result["stream"]
            original_iter = original_stream.__iter__
            stream_chunks = []
            
            def traced_iter():
                try:
                    for chunk in original_iter():
                        stream_chunks.append(chunk)
                        yield chunk
                        
                    # At the end of the stream, process the chunks and finish the span
                    if stream_chunks:
                        # Extract streaming response data
                        formatted_response = _extract_streamed_response(
                            {"params": params, "model_provider": model_provider, "model_name": model_name},
                            stream_chunks
                        )
                        metadata = _extract_streamed_response_metadata(
                            {"params": params, "model_provider": model_provider, "model_name": model_name},
                            stream_chunks
                        )
                        
                        # Set usage metrics
                        if "usage.prompt_tokens" in metadata and metadata["usage.prompt_tokens"] is not None:
                            span.set_metric("bedrock.response.usage.prompt_tokens", int(metadata["usage.prompt_tokens"]))
                        if "usage.completion_tokens" in metadata and metadata["usage.completion_tokens"] is not None:
                            span.set_metric("bedrock.response.usage.completion_tokens", int(metadata["usage.completion_tokens"]))
                        if "usage.prompt_tokens" in metadata and metadata["usage.prompt_tokens"] is not None and \
                           "usage.completion_tokens" in metadata and metadata["usage.completion_tokens"] is not None:
                            span.set_metric(
                                "bedrock.response.usage.total_tokens", 
                                int(metadata["usage.prompt_tokens"]) + int(metadata["usage.completion_tokens"])
                            )
                            
                        # Set response text tags
                        if integration.is_pc_sampled_llmobs(span) and formatted_response["text"]:
                            for i, text in enumerate(formatted_response["text"]):
                                span.set_tag_str(f"bedrock.response.text.{i}", integration.trunc(str(text)))
                                
                            for i, reason in enumerate(formatted_response["finish_reason"]):
                                if reason:
                                    span.set_tag_str(f"bedrock.response.finish_reason.{i}", str(reason))
                        
                        # Set LLM Observability tags
                        if submit_to_llmobs:
                            # Build messages format for LLM Observability
                            prompt_messages = []
                            if isinstance(request_params.get("prompt"), list):
                                # Structured messages from the request
                                prompt_messages = request_params.get("prompt", [])
                            
                            # Get compiled text from formatted_response
                            output_text = ""
                            if formatted_response and "text" in formatted_response:
                                if isinstance(formatted_response["text"], list) and formatted_response["text"]:
                                    output_text = formatted_response["text"][0]
                                else:
                                    output_text = formatted_response.get("text", "")
                            
                            # Create custom response format for llmobs
                            custom_response = {
                                "text": [output_text] if output_text else [""],
                                "finish_reason": formatted_response.get("finish_reason", [""])
                            }
                            
                            # Pass the raw messages directly to the integration
                            # The integration will extract and format them properly
                            integration.llmobs_set_tags(span, args=[], kwargs={"prompt": params.get("messages", [])}, response={"stream_chunks": stream_chunks})
                    
                    # Always finish the span at the end
                    span.finish()
                    
                except Exception as stream_error:
                    # Handle errors during streaming
                    span.error = 1
                    span.set_exc_info(*sys.exc_info())
                    
                    # Set LLM Observability tags even for errors
                    if submit_to_llmobs:
                        integration.llmobs_set_tags(span, args=[], kwargs={"prompt": params.get("messages", [])}, response=None)
                    
                    # Finish the span
                    span.finish()
                    raise
            
            # Replace the iterator
            original_stream.__iter__ = traced_iter
            result["stream"] = original_stream
            
        return result
        
    except Exception as e:
        # Handle errors
        span.error = 1
        span.set_exc_info(*sys.exc_info())
        
        # Still set LLM Observability tags for errors
        if submit_to_llmobs:
            integration.llmobs_set_tags(span, args=[], kwargs={"prompt": getattr(request_params, "prompt", None)}, response=None)
        
        # Finish the span
        span.finish()
        raise


# Botocore default settings
config._add(
    "botocore",
    {
        "_default_service": os.getenv("DD_BOTOCORE_SERVICE", default="aws"),
        "distributed_tracing": asbool(os.getenv("DD_BOTOCORE_DISTRIBUTED_TRACING", default=True)),
        "invoke_with_legacy_context": asbool(os.getenv("DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT", default=False)),
        "operations": collections.defaultdict(Config._HTTPServerConfig),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_BEDROCK_SPAN_CHAR_LIMIT", 128)),
        "tag_no_params": asbool(os.getenv("DD_AWS_TAG_NO_PARAMS", default=False)),
        "instrument_internals": asbool(os.getenv("DD_BOTOCORE_INSTRUMENT_INTERNALS", default=False)),
        "propagation_enabled": asbool(os.getenv("DD_BOTOCORE_PROPAGATION_ENABLED", default=False)),
        "empty_poll_enabled": asbool(os.getenv("DD_BOTOCORE_EMPTY_POLL_ENABLED", default=True)),
        "dynamodb_primary_key_names_for_tables": _load_dynamodb_primary_key_names_for_tables(),
        "add_span_pointers": asbool(os.getenv("DD_BOTOCORE_ADD_SPAN_POINTERS", default=True)),
        "payload_tagging_request": os.getenv("DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING", default=None),
        "payload_tagging_response": os.getenv("DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING", default=None),
        "payload_tagging_max_depth": int(
            os.getenv("DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_DEPTH", 10)
        ),  # RFC defined 10 levels (1.2.3.4...10) as max tagging depth
        "payload_tagging_max_tags": int(
            os.getenv("DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_TAGS", 758)
        ),  # RFC defined default limit - spans are limited past 1000
        "payload_tagging_services": set(
            service.strip()
            for service in os.getenv("DD_TRACE_CLOUD_PAYLOAD_TAGGING_SERVICES", "s3,sns,sqs,kinesis,eventbridge").split(
                ","
            )
        ),
    },
)


def get_version():
    # type: () -> str
    return __version__


def patch():
    if getattr(botocore.client, "_datadog_patch", False):
        return
    botocore.client._datadog_patch = True

    botocore._datadog_integration = BedrockIntegration(integration_config=config.botocore)
    wrapt.wrap_function_wrapper("botocore.client", "BaseClient._make_api_call", patched_api_call(botocore))
    Pin().onto(botocore.client.BaseClient)
    wrapt.wrap_function_wrapper("botocore.parsers", "ResponseParser.parse", patched_lib_fn)
    Pin().onto(botocore.parsers.ResponseParser)
    _PATCHED_SUBMODULES.clear()


def unpatch():
    _PATCHED_SUBMODULES.clear()
    if getattr(botocore.client, "_datadog_patch", False):
        botocore.client._datadog_patch = False
        unwrap(botocore.parsers.ResponseParser, "parse")
        unwrap(botocore.client.BaseClient, "_make_api_call")


def patch_submodules(submodules):
    # type: (Union[List[str], bool]) -> None
    if isinstance(submodules, bool) and submodules:
        _PATCHED_SUBMODULES.clear()
    elif isinstance(submodules, list):
        submodules = [sub_module.lower() for sub_module in submodules]
        _PATCHED_SUBMODULES.update(submodules)


def patched_lib_fn(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.botocore["instrument_internals"]:
        return original_func(*args, **kwargs)
    with core.context_with_data(
        "botocore.instrumented_lib_function",
        span_name="{}.{}".format(original_func.__module__, original_func.__name__),
        tags={COMPONENT: config.botocore.integration_name, SPAN_KIND: SpanKind.CLIENT},
    ) as ctx, ctx.span:
        return original_func(*args, **kwargs)


@with_traced_module
def patched_api_call(botocore, pin, original_func, instance, args, kwargs):
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    if _PATCHED_SUBMODULES and endpoint_name not in _PATCHED_SUBMODULES:
        return original_func(*args, **kwargs)

    trace_operation = schematize_cloud_api_operation(
        "{}.command".format(endpoint_name), cloud_provider="aws", cloud_service=endpoint_name
    )

    operation = get_argument_value(args, kwargs, 0, "operation_name", True)
    params = get_argument_value(args, kwargs, 1, "api_params", True)

    function_vars = {
        "endpoint_name": endpoint_name,
        "operation": operation,
        "params": params,
        "pin": pin,
        "trace_operation": trace_operation,
        "integration": botocore._datadog_integration,
    }

    # Special handling for Bedrock Converse operations
    is_bedrock_converse = endpoint_name == "bedrock-runtime" and operation == "Converse"
    is_bedrock_converse_stream = endpoint_name == "bedrock-runtime" and operation == "ConverseStream"
    is_bedrock_invoke = endpoint_name == "bedrock-runtime" and operation.startswith("InvokeModel")
    
    if is_bedrock_converse or is_bedrock_converse_stream or is_bedrock_invoke:
        patching_fn = patched_bedrock_api_call
    else:
        patching_fn = PATCHING_FUNCTIONS.get(endpoint_name, patched_api_call_fallback)

    # Special direct wrapping for Converse and ConverseStream to ensure spans are finished
    if is_bedrock_converse:
        return _direct_patched_bedrock_converse(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            function_vars=function_vars,
        )
    elif is_bedrock_converse_stream:
        return _direct_patched_bedrock_converse_stream(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            function_vars=function_vars,
        )
    else:
        return patching_fn(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            function_vars=function_vars,
        )


def prep_context_injection(ctx, endpoint_name, operation, trace_operation, params):
    cloud_service = None
    injection_function = None
    schematization_function = schematize_cloud_messaging_operation

    if endpoint_name == "lambda" and operation == "Invoke":
        injection_function = update_client_context
        schematization_function = schematize_cloud_faas_operation
        cloud_service = "lambda"
    if endpoint_name == "events" and operation == "PutEvents":
        injection_function = update_eventbridge_detail
        cloud_service = "events"
    if endpoint_name == "sns" and "Publish" in operation:
        injection_function = inject_trace_to_sqs_or_sns_message
        cloud_service = "sns"
    if endpoint_name == "states" and (operation == "StartExecution" or operation == "StartSyncExecution"):
        injection_function = update_stepfunction_input
        cloud_service = "stepfunctions"

    core.dispatch(
        "botocore.prep_context_injection.post",
        [ctx, cloud_service, schematization_function, injection_function, trace_operation],
    )


def patched_api_call_fallback(original_func, instance, args, kwargs, function_vars):
    # default patched api call that is used generally for several services / operations
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    with core.context_with_data(
        "botocore.instrumented_api_call",
        instance=instance,
        args=args,
        params=params,
        endpoint_name=endpoint_name,
        operation=operation,
        service=schematize_service_name("{}.{}".format(ext_service(pin, int_config=config.botocore), endpoint_name)),
        pin=pin,
        span_name=function_vars.get("trace_operation"),
        span_type=SpanTypes.HTTP,
        span_key="instrumented_api_call",
    ) as ctx, ctx.span:
        core.dispatch("botocore.patched_api_call.started", [ctx])
        if args and config.botocore["distributed_tracing"]:
            prep_context_injection(ctx, endpoint_name, operation, trace_operation, params)

        try:
            result = original_func(*args, **kwargs)
        except botocore.exceptions.ClientError as e:
            core.dispatch(
                "botocore.patched_api_call.exception",
                [
                    ctx,
                    e.response,
                    botocore.exceptions.ClientError,
                    config.botocore.operations[ctx.span.resource].is_error_code,
                ],
            )
            raise
        else:
            core.dispatch("botocore.patched_api_call.success", [ctx, result])
            return result
