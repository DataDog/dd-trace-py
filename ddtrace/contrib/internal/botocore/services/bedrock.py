import json
import sys
from typing import Any
from typing import Dict
from typing import List

import wrapt

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name


log = get_logger(__name__)


_AI21 = "ai21"
_AMAZON = "amazon"
_ANTHROPIC = "anthropic"
_COHERE = "cohere"
_META = "meta"
_STABILITY = "stability"

_MODEL_TYPE_IDENTIFIERS = (
    "foundation-model/",
    "custom-model/",
    "provisioned-model/",
    "imported-model/",
    "prompt/",
    "endpoint/",
    "inference-profile/",
    "default-prompt-router/",
)


class TracedBotocoreStreamingBody(wrapt.ObjectProxy):
    """
    This class wraps the StreamingBody object returned by botocore api calls, specifically for Bedrock invocations.
    Since the response body is in the form of a stream object, we need to wrap it in order to tag the response data
    and fire completion events as the user consumes the streamed response.
    """

    def __init__(self, wrapped, ctx: core.ExecutionContext):
        super().__init__(wrapped)
        self._body = []
        self._execution_ctx = ctx

    def read(self, amt=None):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            body = self.__wrapped__.read(amt=amt)
            self._body.append(json.loads(body))
            if self.__wrapped__.tell() == int(self.__wrapped__._content_length):
                formatted_response = _extract_text_and_response_reason(self._execution_ctx, self._body[0])
                model_provider = self._execution_ctx["model_provider"]
                model_name = self._execution_ctx["model_name"]
                should_set_choice_ids = model_provider == _COHERE and "embed" not in model_name
                core.dispatch(
                    "botocore.bedrock.process_response",
                    [self._execution_ctx, formatted_response, None, self._body[0], should_set_choice_ids],
                )
            return body
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            raise

    def readlines(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            lines = self.__wrapped__.readlines()
            for line in lines:
                self._body.append(json.loads(line))
            formatted_response = _extract_text_and_response_reason(self._execution_ctx, self._body[0])
            model_provider = self._execution_ctx["model_provider"]
            model_name = self._execution_ctx["model_name"]
            should_set_choice_ids = model_provider == _COHERE and "embed" not in model_name
            core.dispatch(
                "botocore.bedrock.process_response",
                [self._execution_ctx, formatted_response, None, self._body[0], should_set_choice_ids],
            )
            return lines
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            raise

    def __iter__(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        exception_raised = False
        try:
            for line in self.__wrapped__:
                self._body.append(json.loads(line["chunk"]["bytes"]))
                yield line
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            exception_raised = True
            raise
        finally:
            if exception_raised:
                return
            metadata = _extract_streamed_response_metadata(self._execution_ctx, self._body)
            formatted_response = _extract_streamed_response(self._execution_ctx, self._body)
            model_provider = self._execution_ctx["model_provider"]
            model_name = self._execution_ctx["model_name"]
            should_set_choice_ids = (
                model_provider == _COHERE and "is_finished" not in self._body[0] and "embed" not in model_name
            )
            core.dispatch(
                "botocore.bedrock.process_response",
                [self._execution_ctx, formatted_response, metadata, self._body, should_set_choice_ids],
            )


def _extract_request_params(params: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """
    Extracts request parameters including prompt, temperature, top_p, max_tokens, and stop_sequences.
    """
    # Handle the Converse API which has different parameter structure
    operation = params.get("operation", "")
    if operation == "Converse" or operation == "ConverseStream":
        converse_body = params.get("conversationId", "")
        messages = params.get("messages", [])
        inference_config = params.get("inferenceConfig", {})
        
        # Extract prompt from messages
        prompt = []
        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")
            
            # Handle nested content structure (list of content items)
            if isinstance(content, list):
                text_parts = []
                for content_item in content:
                    if isinstance(content_item, dict) and "text" in content_item:
                        text_parts.append(content_item["text"])
                prompt.append({"role": role, "content": " ".join(text_parts)})
            # Handle direct content string
            elif content:
                prompt.append({"role": role, "content": content})
        
        return {
            "prompt": prompt,
            "temperature": inference_config.get("temperature", ""),
            "top_p": inference_config.get("topP", ""),
            "max_tokens": inference_config.get("maxTokens", ""),
            "stop_sequences": inference_config.get("stopSequences", []),
            "conversation_id": converse_body,
        }
    
    # Handle the standard InvokeModel API
    body = params.get("body")
    if body:
        request_body = json.loads(body)
    else:
        request_body = {}
    model_id = params.get("modelId")
    if provider == _AI21:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("topP", ""),
            "max_tokens": request_body.get("maxTokens", ""),
            "stop_sequences": request_body.get("stopSequences", []),
        }
    elif provider == _AMAZON and "embed" in model_id:
        return {"prompt": request_body.get("inputText")}
    elif provider == _AMAZON:
        text_generation_config = request_body.get("textGenerationConfig", {})
        return {
            "prompt": request_body.get("inputText"),
            "temperature": text_generation_config.get("temperature", ""),
            "top_p": text_generation_config.get("topP", ""),
            "max_tokens": text_generation_config.get("maxTokenCount", ""),
            "stop_sequences": text_generation_config.get("stopSequences", []),
        }
    elif provider == _ANTHROPIC:
        prompt = request_body.get("prompt", "")
        messages = request_body.get("messages", "")
        return {
            "prompt": prompt or messages,
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("top_p", ""),
            "top_k": request_body.get("top_k", ""),
            "max_tokens": request_body.get("max_tokens_to_sample", ""),
            "stop_sequences": request_body.get("stop_sequences", []),
        }
    elif provider == _COHERE and "embed" in model_id:
        return {
            "prompt": request_body.get("texts"),
            "input_type": request_body.get("input_type", ""),
            "truncate": request_body.get("truncate", ""),
        }
    elif provider == _COHERE:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("p", ""),
            "top_k": request_body.get("k", ""),
            "max_tokens": request_body.get("max_tokens", ""),
            "stop_sequences": request_body.get("stop_sequences", []),
            "stream": request_body.get("stream", ""),
            "n": request_body.get("num_generations", ""),
        }
    elif provider == _META:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("top_p", ""),
            "max_tokens": request_body.get("max_gen_len", ""),
        }
    elif provider == _STABILITY:
        # TODO: request/response formats are different for image-based models. Defer for now
        return {}
    return {}


def _extract_text_and_response_reason(ctx: core.ExecutionContext, body: Dict[str, Any]) -> Dict[str, List[str]]:
    text, finish_reason = "", ""
    model_name = ctx["model_name"]
    provider = ctx["model_provider"]
    # Check if this is a Converse API response
    if ctx["params"].get("operation") == "Converse":
        try:
            # Handle the actual Converse API response format
            if "output" in body and "message" in body.get("output", {}):
                message = body.get("output", {}).get("message", {})
                # Extract text from the content array
                if message.get("content") and isinstance(message["content"], list):
                    # Join all text content parts
                    content_texts = []
                    for content_part in message["content"]:
                        if content_part.get("type", "") == "text" or "text" in content_part:
                            content_texts.append(content_part.get("text", ""))
                    text = " ".join(content_texts)
                else:
                    text = message.get("content", "")
                
                finish_reason = message.get("completionReason", "")
            # Fallback to older format for backwards compatibility
            elif "message" in body:
                message = body.get("message", {})
                if message:
                    text = message.get("content", "")
                    finish_reason = message.get("completionReason", "")
                    
            # Extract usage information if available
            if "usage" in body:
                ctx["usage"] = body.get("usage", {})
            elif "output" in body and "usage" in body.get("output", {}):
                ctx["usage"] = body.get("output", {}).get("usage", {})
                
        except (IndexError, AttributeError, TypeError):
            log.warning("Unable to extract text/finish_reason from Converse response. Defaulting to empty text/finish_reason.")
    else:
        # Standard InvokeModel API response handling
        try:
            if provider == _AI21:
                completions = body.get("completions", [])
                if completions:
                    data = completions[0].get("data", {})
                    text = data.get("text")
                    finish_reason = completions[0].get("finishReason")
            elif provider == _AMAZON and "embed" in model_name:
                text = [body.get("embedding", [])]
            elif provider == _AMAZON:
                results = body.get("results", [])
                if results:
                    text = results[0].get("outputText")
                    finish_reason = results[0].get("completionReason")
            elif provider == _ANTHROPIC:
                text = body.get("completion", "") or body.get("content", "")
                finish_reason = body.get("stop_reason")
            elif provider == _COHERE and "embed" in model_name:
                text = body.get("embeddings", [[]])
            elif provider == _COHERE:
                generations = body.get("generations", [])
                text = [generation["text"] for generation in generations]
                finish_reason = [generation["finish_reason"] for generation in generations]
            elif provider == _META:
                text = body.get("generation")
                finish_reason = body.get("stop_reason")
            elif provider == _STABILITY:
                # TODO: request/response formats are different for image-based models. Defer for now
                pass
        except (IndexError, AttributeError, TypeError):
            log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response(ctx: core.ExecutionContext, streamed_body: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    text, finish_reason = "", ""
    model_name = ctx["model_name"]
    provider = ctx["model_provider"]
    
    # Check if this is a Converse API streaming response
    if ctx["params"].get("operation") == "Converse":
        try:
            # For streaming Converse responses, extract content from each chunk
            for chunk in streamed_body:
                # Handle different types of streaming chunks for Converse API
                chunk_type = chunk.get("type", "")
                
                # Handle message start chunk
                if chunk_type == "message_start":
                    if "conversationId" in chunk:
                        ctx.span.set_tag_str("bedrock.conversation_id", chunk.get("conversationId", ""))
                
                # Handle content block delta chunks (text content)
                elif chunk_type == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "text":
                        text += delta.get("text", "")
                
                # Handle message delta chunks (completion reason)
                elif chunk_type == "message_delta":
                    delta = chunk.get("delta", {})
                    if "completionReason" in delta:
                        finish_reason = delta.get("completionReason", "")
                
                # Handle message stop chunk (usage info)
                elif chunk_type == "message_stop":
                    if "usage" in chunk:
                        ctx["usage"] = chunk.get("usage", {})
                
                # Legacy format support
                elif chunk_type == "message":
                    message = chunk.get("message", {})
                    if message:
                        content = message.get("content", "")
                        text += content
                        # Get completion reason from last chunk if available
                        if message.get("completionReason"):
                            finish_reason = message.get("completionReason")
                
                # Track usage information if available
                if "usage" in chunk and not hasattr(ctx, "usage"):
                    ctx["usage"] = chunk.get("usage", {})
        except (IndexError, AttributeError):
            log.warning("Unable to extract text/finish_reason from Converse streaming response. Defaulting to empty text/finish_reason.")
    else:
        # Standard InvokeModel API streaming response handling
        try:
            if provider == _AI21:
                pass  # DEV: ai21 does not support streamed responses
            elif provider == _AMAZON and "embed" in model_name:
                pass  # DEV: amazon embed models do not support streamed responses
            elif provider == _AMAZON:
                text = "".join([chunk["outputText"] for chunk in streamed_body])
                finish_reason = streamed_body[-1]["completionReason"]
            elif provider == _ANTHROPIC:
                for chunk in streamed_body:
                    if "completion" in chunk:
                        text += chunk["completion"]
                        if chunk["stop_reason"]:
                            finish_reason = chunk["stop_reason"]
                    elif "delta" in chunk:
                        text += chunk["delta"].get("text", "")
                        if "stop_reason" in chunk["delta"]:
                            finish_reason = str(chunk["delta"]["stop_reason"])
            elif provider == _COHERE and "embed" in model_name:
                pass  # DEV: cohere embed models do not support streamed responses
            elif provider == _COHERE:
                if "is_finished" in streamed_body[0]:  # streamed response
                    if "index" in streamed_body[0]:  # n >= 2
                        num_generations = int(ctx.get_item("num_generations") or 0)
                        text = [
                            "".join([chunk["text"] for chunk in streamed_body[:-1] if chunk["index"] == i])
                            for i in range(num_generations)
                        ]
                        finish_reason = [streamed_body[-1]["finish_reason"] for _ in range(num_generations)]
                    else:
                        text = "".join([chunk["text"] for chunk in streamed_body[:-1]])
                        finish_reason = streamed_body[-1]["finish_reason"]
                else:
                    text = [chunk["text"] for chunk in streamed_body[0]["generations"]]
                    finish_reason = [chunk["finish_reason"] for chunk in streamed_body[0]["generations"]]
            elif provider == _META:
                text = "".join([chunk["generation"] for chunk in streamed_body])
                finish_reason = streamed_body[-1]["stop_reason"]
            elif provider == _STABILITY:
                pass  # DEV: we do not yet support image modality models
        except (IndexError, AttributeError):
            log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response_metadata(
    ctx: core.ExecutionContext, streamed_body: List[Dict[str, Any]]
) -> Dict[str, Any]:
    provider = ctx["model_provider"]
    metadata = {}
    
    # Check if this is a Converse API request
    if ctx["params"].get("operation") == "Converse" and streamed_body:
        # For Converse API, usage information may be in the final chunk or in the context
        # First, check for message_stop chunks which contain usage info
        for chunk in reversed(streamed_body):
            if chunk.get("type") == "message_stop" and "usage" in chunk:
                usage = chunk.get("usage", {})
                return {
                    "response.duration": None,  # Not directly provided in Converse API
                    "usage.prompt_tokens": usage.get("inputTokens", None),
                    "usage.completion_tokens": usage.get("outputTokens", None),
                }
            
            # Also check regular usage field
            if "usage" in chunk:
                usage = chunk.get("usage", {})
                return {
                    "response.duration": None,
                    "usage.prompt_tokens": usage.get("inputTokens", None),
                    "usage.completion_tokens": usage.get("outputTokens", None),
                }
                
        # If we didn't find usage info in the chunks, check if it was stored in context
        if hasattr(ctx, "usage"):
            usage = ctx.get_item("usage") or {}
            return {
                "response.duration": None,
                "usage.prompt_tokens": usage.get("inputTokens", None),
                "usage.completion_tokens": usage.get("outputTokens", None),
            }
    # Standard InvokeModel API metadata handling
    elif provider == _AI21:
        pass  # ai21 does not support streamed responses
    elif provider in [_AMAZON, _ANTHROPIC, _COHERE, _META] and streamed_body:
        metadata = streamed_body[-1].get("amazon-bedrock-invocationMetrics", {})
    elif provider == _STABILITY:
        # TODO: figure out extraction for image-based models
        pass
        
    return {
        "response.duration": metadata.get("invocationLatency", None),
        "usage.prompt_tokens": metadata.get("inputTokenCount", None),
        "usage.completion_tokens": metadata.get("outputTokenCount", None),
    }


def handle_bedrock_request(ctx: core.ExecutionContext) -> None:
    """Perform request param extraction and tagging."""
    request_params = _extract_request_params(ctx["params"], ctx["model_provider"])
    
    # Tag the operation type - either InvokeModel or Converse
    operation = ctx["params"].get("operation", "")
    ctx.span.set_tag_str("bedrock.operation", operation)
    
    core.dispatch("botocore.patched_bedrock_api_call.started", [ctx, request_params])
    
    prompt = None
    for k, v in request_params.items():
        if k == "prompt" and ctx["bedrock_integration"].is_pc_sampled_llmobs(ctx.span):
            prompt = v
    
    # For Converse API, store conversation ID if available
    if operation == "Converse" and "conversation_id" in request_params:
        ctx.span.set_tag_str("bedrock.conversation_id", request_params.get("conversation_id", ""))
    
    ctx.set_item("prompt", prompt)
    
    # For numeric generations, store for use in streaming response processing
    if "n" in request_params:
        ctx.set_item("num_generations", request_params.get("n", 1))


def handle_bedrock_response(
    ctx: core.ExecutionContext,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = result["ResponseMetadata"]
    http_headers = metadata["HTTPHeaders"]
    
    # Extract metrics from headers - note that for Converse API, different header names might be used
    operation = ctx["params"].get("operation", "")
    invocation_latency = str(http_headers.get("x-amzn-bedrock-invocation-latency", ""))
    input_token_count = str(http_headers.get("x-amzn-bedrock-input-token-count", ""))
    output_token_count = str(http_headers.get("x-amzn-bedrock-output-token-count", ""))
    
    # For Converse API, check for usage information in the response body
    if operation == "Converse":
        # Check for usage in the result top level
        if "usage" in result:
            usage = result.get("usage", {})
            if usage:
                # Store usage information in context for later use
                ctx.set_item("usage", usage)
                # Override token counts from usage data if available
                input_token_count = str(usage.get("inputTokens", input_token_count))
                output_token_count = str(usage.get("outputTokens", output_token_count))
        
        # Check for usage in the output.usage field (newer Converse API format)
        elif "output" in result and "usage" in result.get("output", {}):
            usage = result.get("output", {}).get("usage", {})
            if usage:
                # Store usage information in context for later use
                ctx.set_item("usage", usage)
                # Override token counts from usage data if available
                input_token_count = str(usage.get("inputTokens", input_token_count))
                output_token_count = str(usage.get("outputTokens", output_token_count))
    
    core.dispatch(
        "botocore.patched_bedrock_api_call.success",
        [
            ctx,
            str(metadata.get("RequestId", "")),
            invocation_latency,
            input_token_count,
            output_token_count,
        ],
    )
    
    # Handle different types of responses based on the operation
    if "body" in result:
        # For InvokeModel operations, the response includes a streaming body that needs wrapping
        body = result["body"]
        result["body"] = TracedBotocoreStreamingBody(body, ctx)
    elif "stream" in result and operation == "ConverseStream":
        # For ConverseStream operations, need to wrap the stream
        stream = result["stream"]
        # Create a streaming body wrapper for the stream
        # We'll handle this by wrapping the stream items iterator
        original_stream_iter = stream.__iter__
        
        def traced_stream_iter():
            try:
                text = ""
                finish_reason = ""
                for item in original_stream_iter():
                    yield item
                    # Track chunks for later extraction
                    ctx._stream_chunks = getattr(ctx, "_stream_chunks", [])
                    ctx._stream_chunks.append(item)
                    
                # Process the collected chunks at the end
                if hasattr(ctx, "_stream_chunks") and ctx._stream_chunks:
                    formatted_response = _extract_streamed_response(ctx, ctx._stream_chunks)
                    metadata = _extract_streamed_response_metadata(ctx, ctx._stream_chunks)
                    should_set_choice_ids = False
                    
                    # Process streaming response
                    core.dispatch(
                        "botocore.bedrock.process_response",
                        [ctx, formatted_response, metadata, ctx._stream_chunks, should_set_choice_ids],
                    )
                    
                    # Finish the span for ConverseStream
                    model_provider = ctx["model_provider"]
                    model_name = ctx["model_name"]
                    if ctx["bedrock_integration"].llmobs_enabled and "embed" not in model_name:
                        ctx["bedrock_integration"].llmobs_set_tags(ctx.span, args=[], kwargs={"prompt": ctx.get_item("prompt")}, response=formatted_response)
                    ctx.span.finish()
            except Exception:
                core.dispatch("botocore.patched_bedrock_api_call.exception", [ctx, sys.exc_info()])
                # Finish the span in case of error
                ctx.span.finish()
                raise
                
        # Replace the iterator method
        stream.__iter__ = traced_stream_iter
        result["stream"] = stream
    elif operation == "Converse":
        # For Converse API, we need to process the response directly
        # Already processed and extracted usage data above
        formatted_response = _extract_text_and_response_reason(ctx, result)
        should_set_choice_ids = False
        
        # Process the response and finish the span
        core.dispatch(
            "botocore.bedrock.process_response",
            [ctx, formatted_response, None, result, should_set_choice_ids],
        )
        
        # Make sure to finish the span for Converse API
        model_provider = ctx["model_provider"]
        model_name = ctx["model_name"]
        if ctx["bedrock_integration"].llmobs_enabled and "embed" not in model_name:
            ctx["bedrock_integration"].llmobs_set_tags(ctx.span, args=[], kwargs={"prompt": ctx.get_item("prompt")}, response=formatted_response)
        ctx.span.finish()
    
    return result


def _parse_model_id(model_id: str):
    """Best effort to extract the model provider and model name from the bedrock model ID.
    model_id can be a 1/2 period-separated string or a full AWS ARN, based on the following formats:
    1. Base model: "{model_provider}.{model_name}"
    2. Cross-region model: "{region}.{model_provider}.{model_name}"
    3. Other: Prefixed by AWS ARN "arn:aws{+region?}:bedrock:{region}:{account-id}:"
        a. Foundation model: ARN prefix + "foundation-model/{region?}.{model_provider}.{model_name}"
        b. Custom model: ARN prefix + "custom-model/{model_provider}.{model_name}"
        c. Provisioned model: ARN prefix + "provisioned-model/{model-id}"
        d. Imported model: ARN prefix + "imported-module/{model-id}"
        e. Prompt management: ARN prefix + "prompt/{prompt-id}"
        f. Sagemaker: ARN prefix + "endpoint/{model-id}"
        g. Inference profile: ARN prefix + "{application-?}inference-profile/{model-id}"
        h. Default prompt router: ARN prefix + "default-prompt-router/{prompt-id}"
    If model provider cannot be inferred from the model_id formatting, then default to "custom"
    """
    if not model_id.startswith("arn:aws"):
        model_meta = model_id.split(".")
        if len(model_meta) < 2:
            return "custom", model_meta[0]
        return model_meta[-2], model_meta[-1]
    for identifier in _MODEL_TYPE_IDENTIFIERS:
        if identifier not in model_id:
            continue
        model_id = model_id.rsplit(identifier, 1)[-1]
        if identifier in ("foundation-model/", "custom-model/"):
            model_meta = model_id.split(".")
            if len(model_meta) < 2:
                return "custom", model_id
            return model_meta[-2], model_meta[-1]
        return "custom", model_id
    return "custom", "custom"


def patched_bedrock_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    model_id = params.get("modelId")
    model_provider, model_name = _parse_model_id(model_id)
    integration = function_vars.get("integration")
    submit_to_llmobs = integration.llmobs_enabled and "embed" not in model_name
    with core.context_with_data(
        "botocore.patched_bedrock_api_call",
        pin=pin,
        span_name=function_vars.get("trace_operation"),
        service=schematize_service_name(
            "{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))
        ),
        resource=function_vars.get("operation"),
        span_type=SpanTypes.LLM if submit_to_llmobs else None,
        call_trace=True,
        bedrock_integration=integration,
        params=params,
        model_provider=model_provider,
        model_name=model_name,
    ) as ctx:
        try:
            handle_bedrock_request(ctx)
            result = original_func(*args, **kwargs)
            result = handle_bedrock_response(ctx, result)
            return result
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [ctx, sys.exc_info()])
            
            # For Converse API, we need to explicitly finish the span in case of error
            # The span for InvokeModel is finished by the TracedBotocoreStreamingBody
            operation = ctx["params"].get("operation", "")
            if operation == "Converse":
                # Make sure to finish the span for error case
                model_provider = ctx["model_provider"]
                model_name = ctx["model_name"]
                if ctx["bedrock_integration"].llmobs_enabled and "embed" not in model_name:
                    ctx["bedrock_integration"].llmobs_set_tags(ctx.span, args=[], kwargs={"prompt": ctx.get_item("prompt")}, response=None)
                ctx.span.finish()
                
            raise
