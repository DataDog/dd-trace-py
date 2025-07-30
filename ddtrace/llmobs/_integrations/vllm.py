from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr


class VLLMIntegration(BaseLLMIntegration):
    _integration_name = "vllm"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        """Set base span tags for vLLM operations."""
        if provider is not None:
            span.set_tag_str("vllm.request.provider", provider)
        if model is not None:
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

        # Set span kind based on operation
        span_kind = "llm" if operation == "llm" else "embedding"
        span._set_ctx_item(SPAN_KIND, span_kind)

        # Extract input from various sources
        input_data = self._extract_input(args, kwargs, operation)
        span._set_ctx_item(INPUT_VALUE, input_data)

        # Extract metadata
        metadata = self._extract_metadata(kwargs, operation)
        span._set_ctx_item(METADATA, metadata)

        # Set output if available and no error
        if response is not None and not span.error:
            output_data = self._extract_output(response, operation)
            span._set_ctx_item(OUTPUT_VALUE, output_data)

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