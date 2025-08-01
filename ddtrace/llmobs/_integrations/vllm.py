from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr


class VLLMIntegration(BaseLLMIntegration):
    _integration_name = "vllm"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        """Set base span tags for vLLM operations."""
        if provider:  # Only set if provider is not None or empty
            span.set_tag_str("vllm.request.provider", provider)
        if model:  # Only set if model is not None or empty
            span.set_tag_str("vllm.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "llm",
    ) -> None:
        """Set LLMObs tags for vLLM operations."""
        if not self.llmobs_enabled:
            return

        # For vLLM engine.step() tracing, the response is a RequestOutput object
        # and we extract everything from it, not from args/kwargs
        if operation == "vllm_request" and response is not None:
            self._set_llmobs_tags_from_request_output(span, response, kwargs.get("engine_instance"))
        else:
            # Fallback to original high-level API extraction
            self._set_llmobs_tags_from_high_level_api(span, args, kwargs, response, operation)

    def _set_llmobs_tags_from_request_output(self, span: Span, request_output: Any, engine_instance: Any) -> None:
        """Set LLMObs tags from vLLM RequestOutput object (for engine.step() tracing)."""
        # Extract model name and provider from span tags
        model_name = span.get_tag("vllm.request.model") or ""
        model_provider = span.get_tag("vllm.request.provider") or "vllm"

        # Extract input/output data for LLMObs
        input_messages = self._extract_input_messages_from_request_output(request_output)
        output_messages = self._extract_output_messages_from_request_output(request_output)
        output_value = self._extract_output_value_from_request_output(request_output)
        metadata = self._extract_metadata_from_request_output(request_output, engine_instance)
        metrics = self._extract_metrics_from_request_output(request_output)

        # Set LLMObs context items
        span._set_ctx_items({
            SPAN_KIND: "llm",
            MODEL_NAME: model_name,
            MODEL_PROVIDER: model_provider,
            INPUT_MESSAGES: input_messages,
            OUTPUT_MESSAGES: output_messages,
            OUTPUT_VALUE: output_value,
            METADATA: metadata,
            METRICS: metrics,
        })

    def _set_llmobs_tags_from_high_level_api(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any], operation: str
    ) -> None:
        """Set LLMObs tags for high-level vLLM API operations (original implementation)."""
        # Set span kind based on operation
        span_kind = "llm" if operation == "llm" else "embedding"
        
        # Extract model name and provider from span tags
        model_name = span.get_tag("vllm.request.model") or ""
        model_provider = span.get_tag("vllm.request.provider") or ""

        # Extract input from various sources
        input_data = self._extract_input(args, kwargs, operation)
        input_messages = self._extract_input_messages(args, kwargs, operation)

        # Extract metadata
        metadata = self._extract_metadata(kwargs, operation)
        
        # Extract metrics (token counts, etc.)
        metrics = self._extract_metrics(response, operation)

        # Set output if available and no error
        output_data = ""
        output_messages = [{"content": ""}]
        if response is not None and not span.error:
            output_data = self._extract_output(response, operation)
            output_messages = self._extract_output_messages(response, operation)

        # Set all LLMObs context items following Anthropic pattern
        span._set_ctx_items({
            SPAN_KIND: span_kind,
            MODEL_NAME: model_name,
            MODEL_PROVIDER: model_provider,
            INPUT_VALUE: input_data,
            OUTPUT_VALUE: output_data,
            INPUT_MESSAGES: input_messages,
            OUTPUT_MESSAGES: output_messages,
            METADATA: metadata,
            METRICS: metrics,
        })

    def _extract_input(self, args: List[Any], kwargs: Dict[str, Any], operation: str) -> str:
        """Extract input data from vLLM method arguments."""
        # For generate operations, look for prompts
        if operation == "llm":
            # Try to get prompts from various argument positions/names
            prompts = (
                get_argument_value(args, kwargs, 0, "prompts", optional=True) or
                get_argument_value(args, kwargs, 0, "inputs", optional=True) or
                get_argument_value(args, kwargs, 0, "prompt", optional=True)
            )
            if prompts:
                return self._format_io(prompts)
        
        # For encode operations, look for text inputs
        elif operation == "embedding":
            inputs = (
                get_argument_value(args, kwargs, 0, "prompts", optional=True) or
                get_argument_value(args, kwargs, 0, "inputs", optional=True) or
                get_argument_value(args, kwargs, 0, "text", optional=True)
            )
            if inputs:
                return self._format_io(inputs)

        # Fallback: stringify first argument if available
        if args:
            return self._format_io(args[0])
        
        return ""

    def _extract_metadata(self, kwargs: Dict[str, Any], operation: str) -> Dict[str, Any]:
        """Extract metadata from vLLM method arguments."""
        metadata = {}
        
        # Extract common vLLM parameters
        instance = kwargs.get("instance")
        if instance:
            # Try to extract model configuration details
            if hasattr(instance, "model_config"):
                model_config = instance.model_config
                metadata["model"] = _get_attr(model_config, "model", "")
                metadata["dtype"] = _get_attr(model_config, "dtype", "")
                metadata["max_model_len"] = _get_attr(model_config, "max_model_len", "")
            
            # Extract engine configuration if available
            if hasattr(instance, "engine_config"):
                engine_config = instance.engine_config
                if hasattr(engine_config, "parallel_config"):
                    parallel_config = engine_config.parallel_config
                    metadata["tensor_parallel_size"] = _get_attr(parallel_config, "tensor_parallel_size", "")
                    metadata["pipeline_parallel_size"] = _get_attr(parallel_config, "pipeline_parallel_size", "")

        # Extract sampling/pooling parameters
        if operation == "llm":
            sampling_params = get_argument_value([], kwargs, 1, "sampling_params", optional=True)
            if sampling_params:
                metadata["temperature"] = _get_attr(sampling_params, "temperature", "")
                metadata["top_p"] = _get_attr(sampling_params, "top_p", "")
                metadata["top_k"] = _get_attr(sampling_params, "top_k", "")
                metadata["max_tokens"] = _get_attr(sampling_params, "max_tokens", "")
        elif operation == "embedding":
            pooling_params = get_argument_value([], kwargs, 1, "pooling_params", optional=True)
            if pooling_params:
                metadata["pooling_type"] = _get_attr(pooling_params, "pooling_type", "")

        return {k: v for k, v in metadata.items() if v}

    def _extract_metrics(self, response: Any, operation: str) -> Dict[str, Any]:
        """Extract metrics (token counts, etc.) from vLLM response."""
        metrics = {}
        
        if operation == "llm" and response is not None:
            # Handle list of RequestOutput objects (from high-level API)
            if isinstance(response, list):
                input_tokens = 0
                output_tokens = 0
                
                for output in response:
                    # Extract from RequestOutput objects
                    if hasattr(output, "prompt_token_ids") and output.prompt_token_ids:
                        input_tokens += len(output.prompt_token_ids)
                    
                    if hasattr(output, "outputs") and output.outputs:
                        for completion in output.outputs:
                            if hasattr(completion, "token_ids") and completion.token_ids:
                                output_tokens += len(completion.token_ids)
                
                if input_tokens > 0:
                    metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
                if output_tokens > 0:
                    metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
                if input_tokens > 0 or output_tokens > 0:
                    metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
                    
            # Handle single RequestOutput object
            elif hasattr(response, "prompt_token_ids"):
                input_tokens = len(response.prompt_token_ids) if response.prompt_token_ids else 0
                output_tokens = 0
                
                if hasattr(response, "outputs") and response.outputs:
                    for output in response.outputs:
                        if hasattr(output, "token_ids") and output.token_ids:
                            output_tokens += len(output.token_ids)
                
                if input_tokens > 0:
                    metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
                if output_tokens > 0:
                    metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
                if input_tokens > 0 or output_tokens > 0:
                    metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
            
            # Handle single response object from async generator
            elif hasattr(response, "__iter__") and not isinstance(response, str):
                # This handles the async generator case
                input_tokens = 0
                output_tokens = 0
                
                for item in response:
                    if hasattr(item, "prompt_token_ids") and item.prompt_token_ids:
                        input_tokens += len(item.prompt_token_ids)
                    
                    if hasattr(item, "outputs") and item.outputs:
                        for output in item.outputs:
                            if hasattr(output, "token_ids") and output.token_ids:
                                output_tokens += len(output.token_ids)
                
                if input_tokens > 0:
                    metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
                if output_tokens > 0:
                    metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
                if input_tokens > 0 or output_tokens > 0:
                    metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        
        return metrics

    def _extract_input_messages(self, args: List[Any], kwargs: Dict[str, Any], operation: str) -> List[Dict[str, Any]]:
        """Extract input messages in LLMObs format from vLLM arguments."""
        # Check if we have messages from chat API
        messages = kwargs.get("messages")
        if messages is not None:
            return self._format_chat_messages(messages)
        
        # Check if we have prompts from kwargs (high-level API)
        prompts = kwargs.get("prompts")
        if prompts is not None:
            return self._format_prompts_as_messages(prompts)
        
        # Fallback to extracting from args
        if operation == "llm":
            prompts = get_argument_value(args, kwargs, 0, "prompts", optional=True)
            if prompts:
                return self._format_prompts_as_messages(prompts)
        elif operation == "embedding":
            inputs = get_argument_value(args, kwargs, 0, "prompts", optional=True)
            if inputs:
                return self._format_prompts_as_messages(inputs)
        
        # Final fallback to first argument
        if args:
            return self._format_prompts_as_messages(args[0])
        
        return [{"content": "", "role": "user"}]

    def _format_chat_messages(self, messages: Any) -> List[Dict[str, Any]]:
        """Format chat messages from vLLM chat API."""
        if not isinstance(messages, list):
            return [{"content": str(messages), "role": "user"}]
        
        formatted_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                formatted_messages.append({
                    "content": str(msg.get("content", "")),
                    "role": msg.get("role", "user")
                })
            else:
                formatted_messages.append({"content": str(msg), "role": "user"})
        
        return formatted_messages

    def _format_prompts_as_messages(self, prompts: Any) -> List[Dict[str, Any]]:
        """Format prompts as messages for LLMObs."""
        if isinstance(prompts, str):
            return [{"content": prompts, "role": "user"}]
        elif isinstance(prompts, list):
            if len(prompts) == 1:
                return [{"content": str(prompts[0]), "role": "user"}]
            else:
                # Multiple prompts - format as single message with summary
                return [{"content": f"[{len(prompts)} prompts]", "role": "user"}]
        elif isinstance(prompts, dict):
            # Handle complex prompt structures like TextPrompt, TokensPrompt
            if "prompt" in prompts:
                return [{"content": str(prompts["prompt"]), "role": "user"}]
            elif "prompt_token_ids" in prompts:
                return [{"content": f"[token_ids: {len(prompts['prompt_token_ids'])} tokens]", "role": "user"}]
        
        return [{"content": str(prompts), "role": "user"}]

    def _extract_output_messages(self, response: Any, operation: str) -> List[Dict[str, Any]]:
        """Extract output messages in LLMObs format from vLLM response."""
        if operation == "llm":
            return self._extract_llm_output_messages(response)
        elif operation == "embedding":
            return self._extract_embedding_output_messages(response)
        
        return [{"content": str(response), "role": "assistant"}]

    def _extract_llm_output_messages(self, response: Any) -> List[Dict[str, Any]]:
        """Extract output messages from vLLM generation response."""
        if not response:
            return [{"content": "", "role": "assistant"}]
        
        # Handle list of RequestOutput objects (from LLM.generate)
        if isinstance(response, list):
            if len(response) == 1:
                # Single response
                output = response[0]
                if hasattr(output, "outputs") and output.outputs:
                    content = _get_attr(output.outputs[0], "text", "")
                    return [{"content": content, "role": "assistant"}]
            else:
                # Multiple responses - summarize
                total_outputs = sum(len(output.outputs) if hasattr(output, "outputs") and output.outputs else 0 
                                  for output in response)
                return [{"content": f"[{len(response)} responses, {total_outputs} completions]", "role": "assistant"}]
        
        # Handle single RequestOutput object
        elif hasattr(response, "outputs") and response.outputs:
            content = _get_attr(response.outputs[0], "text", "")
            return [{"content": content, "role": "assistant"}]
        
        return [{"content": str(response), "role": "assistant"}]

    def _extract_embedding_output_messages(self, response: Any) -> List[Dict[str, Any]]:
        """Extract output messages from vLLM embedding response."""
        if not response:
            return [{"content": "", "role": "assistant"}]
        
        if isinstance(response, list):
            embedding_count = len(response)
            if embedding_count == 1 and hasattr(response[0], "outputs"):
                if hasattr(response[0].outputs, "embedding") and response[0].outputs.embedding:
                    dim = len(response[0].outputs.embedding)
                    return [{"content": f"[embedding dim={dim}]", "role": "assistant"}]
            return [{"content": f"[{embedding_count} embeddings]", "role": "assistant"}]
        
        return [{"content": "[embedding]", "role": "assistant"}]

    def _extract_output(self, response: Any, operation: str) -> str:
        """Extract output data from vLLM response."""
        if operation == "llm":
            # Handle RequestOutput objects
            if hasattr(response, "__iter__") and not isinstance(response, str):
                # Multiple outputs
                outputs = []
                for item in response:
                    if hasattr(item, "outputs") and item.outputs:
                        # Get text from first output
                        output_text = _get_attr(item.outputs[0], "text", "")
                        outputs.append(output_text)
                    else:
                        outputs.append(str(item))
                return self._format_io(outputs)
            elif hasattr(response, "outputs") and response.outputs:
                # Single RequestOutput
                return _get_attr(response.outputs[0], "text", "")
        
        elif operation == "embedding":
            # Handle EmbeddingRequestOutput objects
            if hasattr(response, "__iter__") and not isinstance(response, str):
                # Multiple embedding outputs
                embeddings = []
                for item in response:
                    if hasattr(item, "outputs"):
                        embeddings.append(f"[embedding dim={len(item.outputs.embedding) if item.outputs.embedding else 0}]")
                    else:
                        embeddings.append(str(item))
                return self._format_io(embeddings)
            elif hasattr(response, "outputs"):
                # Single embedding output
                if hasattr(response.outputs, "embedding") and response.outputs.embedding:
                    return f"[embedding dim={len(response.outputs.embedding)}]"

        # Fallback
        return str(response)

    def _format_io(self, data: Any) -> str:
        """Format input/output data for display."""
        if isinstance(data, str):
            return data
        elif isinstance(data, list):
            if len(data) == 1:
                return self._format_io(data[0])
            else:
                return f"[{len(data)} items]"
        elif hasattr(data, "prompt"):
            return data.prompt
        else:
            return str(data)

    # RequestOutput extraction methods for engine.step() tracing
    def _extract_input_messages_from_request_output(self, request_output) -> List[Dict[str, Any]]:
        """Extract input messages from vLLM RequestOutput object."""
        if hasattr(request_output, "prompt") and request_output.prompt:
            return [{"content": str(request_output.prompt), "role": "user"}]
        return [{"content": "", "role": "user"}]

    def _extract_output_messages_from_request_output(self, request_output) -> List[Dict[str, Any]]:
        """Extract output messages from vLLM RequestOutput object."""
        if hasattr(request_output, "outputs") and request_output.outputs:
            # For single output, return the text
            if len(request_output.outputs) == 1:
                output_text = _get_attr(request_output.outputs[0], "text", "")
                return [{"content": output_text, "role": "assistant"}]
            else:
                # For multiple outputs, summarize
                total_text = " ".join(_get_attr(output, "text", "") for output in request_output.outputs)
                return [{"content": f"[{len(request_output.outputs)} outputs] {total_text[:100]}...", "role": "assistant"}]
        return [{"content": "", "role": "assistant"}]

    def _extract_output_value_from_request_output(self, request_output) -> str:
        """Extract output value from vLLM RequestOutput object."""
        if hasattr(request_output, "outputs") and request_output.outputs:
            if len(request_output.outputs) == 1:
                return _get_attr(request_output.outputs[0], "text", "")
            else:
                # Multiple outputs - return first one or summary
                texts = [_get_attr(output, "text", "") for output in request_output.outputs]
                return f"[{len(texts)} outputs]: {texts[0] if texts else ''}"
        return ""

    def _extract_metadata_from_request_output(self, request_output, engine_instance) -> Dict[str, Any]:
        """Extract metadata from vLLM RequestOutput and engine instance."""
        metadata = {}
        
        # Add model configuration
        if engine_instance and hasattr(engine_instance, "model_config"):
            model_config = engine_instance.model_config
            if hasattr(model_config, "dtype"):
                metadata["dtype"] = str(model_config.dtype)
            if hasattr(model_config, "max_model_len"):
                metadata["max_model_len"] = model_config.max_model_len
        
        # Add sampling parameters if available from the request
        if hasattr(request_output, "outputs") and request_output.outputs:
            output = request_output.outputs[0]
            if hasattr(output, "finish_reason"):
                metadata["finish_reason"] = output.finish_reason
        
        return {k: v for k, v in metadata.items() if v}

    def _extract_metrics_from_request_output(self, request_output) -> Dict[str, Any]:
        """Extract token metrics from vLLM RequestOutput object."""
        metrics = {}
        
        # Extract token counts
        input_tokens = 0
        output_tokens = 0
        
        # Input tokens from prompt
        if hasattr(request_output, "prompt_token_ids") and request_output.prompt_token_ids:
            input_tokens = len(request_output.prompt_token_ids)
        
        # Output tokens from completions
        if hasattr(request_output, "outputs") and request_output.outputs:
            for output in request_output.outputs:
                if hasattr(output, "token_ids") and output.token_ids:
                    output_tokens += len(output.token_ids)
        
        if input_tokens > 0:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens > 0:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens > 0 or output_tokens > 0:
            metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        
        return metrics 